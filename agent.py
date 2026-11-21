"""
Portfolio Agent (chat): contexto completo desde las cachés y respuestas verificadas.

Regla de oro: la IA nunca es fuente de un número.
- El contexto (DATOS) sale de las cachés de financials.py y risk.py, del CSV del portfolio y de las
  cotizaciones en memoria. Armarlo no consulta la red.
- Tablas: el modelo NO las escribe. Pide una con una línea [[TABLE cols=... tickers=...]] y el
  servidor la arma con los mismos datos. Una tabla escrita por el modelo se descarta.
- Texto: cada número tiene que estar en los DATOS (tolerancia de redondeo de narrative.is_grounded).
  Una oración con una cifra sin respaldo, un precio objetivo o una orden de compra/venta se omite.
"""
import re
from datetime import datetime, timezone

import explore
import financials as fin
import narrative
import risk

MAX_FOCUS = 4                # tickers con detalle completo en el contexto
MAX_CONTEXT_CHARS = 24000    # presupuesto del bloque de DATOS (~7k tokens)
MAX_TABLES = 3
MAX_TABLE_ROWS = 60
MAX_TABLE_COLS = 10
HISTORY_TURNS = 10
CASH_KEYS = ('USD_CASH', 'ARS', 'EUR', 'BRL')   # plus any position flagged {'cash': True}

# Columnas permitidas en tablas: clave → (unidad, etiqueta ES, etiqueta EN)
COLUMNS = {
    'ticker': ('text', 'Ticker', 'Ticker'),
    'name': ('text', 'Nombre', 'Name'),
    'category': ('code', 'Lista', 'List'),
    'kind': ('code', 'Tipo', 'Type'),
    'sector': ('text', 'Sector', 'Sector'),
    'price': ('price', 'Precio', 'Price'),
    'chg_day_pct': ('pct', 'Var. día', 'Day chg'),
    'ret_12m_pct': ('pct', 'Var. 12 meses', '12m chg'),
    'shares': ('num', 'Cantidad', 'Shares'),
    'cost_basis': ('money', 'Costo', 'Cost'),
    'value': ('money', 'Valor', 'Value'),
    'pnl': ('money', 'Ganancia', 'P&L'),
    'pnl_pct': ('pct', 'Ganancia %', 'P&L %'),
    'weight_pct': ('pct', 'Peso', 'Weight'),
    'risk_score': ('int', 'Riesgo', 'Risk'),
    'level': ('code', 'Nivel', 'Level'),
    'valuation': ('code', 'Valuación', 'Valuation'),
    'pe': ('x', 'P/E', 'P/E'),
    'fwd_pe': ('x', 'P/E futuro', 'Fwd P/E'),
    'ps': ('x', 'P/Ventas', 'P/S'),
    'fcf_yield': ('pct', 'Rend. FCF', 'FCF yield'),
    'dividend_yield': ('pct', 'Dividendo', 'Div. yield'),
    'net_margin': ('pct', 'Margen neto', 'Net margin'),
    'gross_margin': ('pct', 'Margen bruto', 'Gross margin'),
    'op_margin': ('pct', 'Margen operativo', 'Op. margin'),
    'revenue_growth': ('pct', 'Crec. ingresos', 'Rev. growth'),
    'revenue_ttm': ('bigmoney', 'Ingresos TTM', 'Revenue TTM'),
    'net_income_ttm': ('bigmoney', 'Ganancia neta TTM', 'Net income TTM'),
    'fcf_ttm': ('bigmoney', 'FCF TTM', 'FCF TTM'),
    'roe': ('pct', 'ROE', 'ROE'),
    'debt_to_equity': ('x', 'Deuda/Patrimonio', 'Debt/Equity'),
    'current_ratio': ('x', 'Liquidez corriente', 'Current ratio'),
    'beta': ('x', 'Beta', 'Beta'),
    'volatility': ('pct', 'Volatilidad', 'Volatility'),
    'max_drawdown': ('pct', 'Caída máx. 1 año', 'Max drawdown'),
    'from_high_pct': ('pct', 'Desde máximo', 'From high'),
    'rsi': ('num', 'RSI 14', 'RSI 14'),
    'trend': ('code', 'Tendencia', 'Trend'),
    'sma200': ('price', 'Media 200d', 'SMA 200'),
    'ema20': ('price', 'EMA 20', 'EMA 20'),
    'price_vs_ema20_pct': ('pct', 'Precio vs EMA 20', 'Price vs EMA 20'),
    'expense_ratio': ('pct', 'Costo anual', 'Expense ratio'),
    'last_quarter': ('text', 'Último trimestre', 'Last quarter'),
    'eps_surprise_pct': ('pct', 'Sorpresa BPA', 'EPS surprise'),
    'next_earnings': ('date', 'Próx. resultados', 'Next earnings'),
}
CATEGORICAL = {'category', 'kind', 'level', 'valuation', 'trend', 'sector'}

GLOSSARY = """GLOSSARY AND RULES OF THE DASHBOARD MODEL (numbers here may be quoted):
- risk_score: 0 to 100, higher = more risk. Levels: below 30 low, below 45 moderate, below 60 high, else very_high.
- valuation: cheap / fair / expensive from the valuation pillar (P/E, forward P/E vs P/E, P/S, FCF yield) at today's
  price; points/max below 0.35 cheap, below 0.60 fair, else expensive. null = not available (ETF, crypto, or fewer than 2 metrics).
- Technical: EMA 20, Bollinger 20 with 2 standard deviations, RSI 14 (above 70 overbought, below 30 oversold),
  200-day moving average (trend: above_rising, above, below, below_falling), 52-week high and low, 12 months.
- Margins, growth, FCF yield, weight, P&L % and volatility are percentages. pe, fwd_pe, ps, debt_to_equity are multiples (x).
- TTM = trailing twelve months. The S&P 500 is not in DATA unless listed."""


# ── Utilidades ──────────────────────────────────────────────────────────────

def _r(v, d=2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float('inf'), float('-inf')) else round(f, d)


def _last(lst):
    vals = [x for x in (lst or []) if x is not None]
    return vals[-1] if vals else None


def _metric(report, key):
    for p in (report or {}).get('pillars', []):
        for m in p['metrics']:
            if m['key'] == key:
                return m
    return None


def _mval(report, key):
    m = _metric(report, key)
    return None if not m or m.get('missing') or m.get('excluded') else m.get('value')


# ── Filas por ticker (sin red) ──────────────────────────────────────────────

def build_rows(tickers, meta=None, positions=None, quotes=None):
    """{ticker: fila plana}. meta: TICKER_META; positions: load_portfolio_from_csv();
    quotes: {ticker: {'price', 'chg_pct', 'ts' (epoch)}} de la caché de cotizaciones."""
    meta, positions, quotes = meta or {}, positions or {}, quotes or {}
    rows = {}
    for t in tickers:
        t = t.upper()
        m = meta.get(t, {})
        entry = fin.read_cache(t) or {}
        p = entry.get('payload') or {}
        raw = p.get('metrics') or {}
        rep = risk.read_cache(t) or {}
        mk = rep.get('market') or {}
        basis = rep.get('valuation_basis') or {}
        k = basis.get('factor') if basis.get('repriced') else None
        q = quotes.get(t) or {}
        price = q.get('price') if q.get('price') else mk.get('price')
        if q.get('price') and q.get('ts'):
            as_of = datetime.fromtimestamp(q['ts'], timezone.utc).isoformat(timespec='minutes')
        else:
            as_of = rep.get('price_date')
        pos = positions.get(t) or {}
        shares, cost = pos.get('shares'), pos.get('costBasis')
        value = price * shares if price is not None and shares else None
        recovered = bool(value and cost is not None and cost < 4)
        pnl = value - cost if value is not None and cost is not None else None
        pnl_pct = pnl / cost * 100 if pnl is not None and cost and cost > 0 and not recovered else None
        p12 = rep.get('prices_12m') or []
        ret12 = (p12[-1]['close'] / p12[0]['close'] - 1) * 100 if len(p12) >= 2 and p12[0]['close'] else None
        quarters = p.get('quarters') or []
        lastq = quarters[-1] if quarters else {}
        surprise = next((x.get('surprise_pct') for x in reversed(quarters) if x.get('surprise_pct') is not None), None)
        trend_m = _metric(rep, 'trend')
        ema20 = mk.get('ema20')
        div = raw.get('dividend_yield_pct')
        rows[t] = {
            'ticker': t, 'name': p.get('name') or rep.get('name') or m.get('name') or t,
            'category': m.get('category'), 'kind': rep.get('kind') or entry.get('kind') or None,
            'sector': m.get('sector') or p.get('type'),
            'currency': raw.get('currency'), 'financial_currency': raw.get('financial_currency'),
            'price': _r(price, 4), 'price_as_of': as_of, 'chg_day_pct': _r(q.get('chg_pct')),
            'ret_12m_pct': _r(ret12), 'shares': _r(shares, 6), 'cost_basis': _r(cost),
            'value': _r(value), 'pnl': _r(pnl), 'pnl_pct': _r(pnl_pct), 'recovered': recovered,
            'weight_pct': None,
            'risk_score': rep.get('risk_score'), 'level': rep.get('level'),
            'valuation': rep.get('valuation_label'), 'valuation_reason': rep.get('valuation_reason'),
            'pe': _r(_mval(rep, 'pe')), 'fwd_pe': _r(raw.get('fwd_pe') * k if k and raw.get('fwd_pe') else raw.get('fwd_pe')),
            'ps': _r(_mval(rep, 'ps')), 'fcf_yield': _r(_mval(rep, 'fcf_yield')),
            'dividend_yield': _r(div / k if k and div is not None else div),
            'net_margin': _r(_mval(rep, 'net_margin')), 'gross_margin': _r(_last(p.get('gm'))),
            'op_margin': _r(_last(p.get('om'))), 'revenue_growth': _r(_mval(rep, 'revenue_growth')),
            'revenue_ttm': _r(raw.get('revenue_ttm'), 0), 'net_income_ttm': _r(raw.get('net_income_ttm'), 0),
            'fcf_ttm': _r(raw.get('fcf_ttm'), 0), 'roe': _r(raw.get('roe_pct')),
            'debt_to_equity': _r(_mval(rep, 'debt_to_equity')), 'current_ratio': _r(_mval(rep, 'current_ratio')),
            'beta': _r(raw.get('beta')), 'volatility': _r(mk.get('volatility_pct')),
            'max_drawdown': _r(mk.get('max_drawdown_pct')), 'from_high_pct': _r(mk.get('drawdown_from_high_pct')),
            'high_52w': _r(mk.get('high_52w'), 4), 'low_52w': _r(mk.get('low_52w'), 4),
            'rsi': _r(mk.get('rsi14'), 1), 'trend': trend_m.get('value') if trend_m and not trend_m.get('missing') else None,
            'sma200': _r(mk.get('sma200'), 4), 'ema20': _r(ema20, 4),
            'bb_upper': _r(mk.get('bb_upper'), 4), 'bb_lower': _r(mk.get('bb_lower'), 4),
            'price_vs_ema20_pct': _r((price / ema20 - 1) * 100) if price and ema20 else None,
            'expense_ratio': _r(_mval(rep, 'expense_ratio')),
            'last_quarter': p.get('latest_period_label'), 'eps_surprise_pct': _r(surprise),
            'rev_yoy_last_q': _r(lastq.get('rev_yoy')), 'next_earnings': p.get('next_earnings_date'),
            'risk_computed_at': rep.get('computed_at'), 'financials_fetched_at': entry.get('fetched_at'),
            '_report': rep, '_payload': p,
        }
    total = sum(r['value'] for r in rows.values() if r['value'])
    if total:
        for r in rows.values():
            r['weight_pct'] = _r(r['value'] / total * 100) if r['value'] else None
    return rows


def totals(rows, positions=None):
    positions = positions or {}
    held = [r for r in rows.values() if r['value']]
    cash = sum((p or {}).get('currentValue') or 0 for k, p in positions.items()
               if k in CASH_KEYS or (p or {}).get('cash'))
    out = {'positions': len(held), 'invested_value': _r(sum(r['value'] for r in held)),
           'invested_cost': _r(sum(r['cost_basis'] or 0 for r in held)),
           'cash_value': _r(cash) if cash else None}
    if out['invested_value'] is not None and out['invested_cost']:
        out['invested_pnl'] = _r(out['invested_value'] - out['invested_cost'])
    for key, codes in (('valuation', ('cheap', 'fair', 'expensive')), ('level', ('low', 'moderate', 'high', 'very_high'))):
        for c in codes:
            out[f'count_{key}_{c}'] = sum(1 for r in rows.values() if r.get(key) == c)
    return out


# ── Contexto para el modelo ─────────────────────────────────────────────────

# Tabla compacta de todos los activos: lo justo para responder sobre el portfolio. El resto de las columnas
# (P/E futuro, variaciones, márgenes, técnico detallado) está en el detalle de los activos nombrados y en las
# tablas que arma el servidor. Achicarla bajó el tiempo de respuesta de Gemma.
COMPACT_COLS = ['ticker', 'name', 'category', 'kind', 'price', 'value', 'pnl_pct', 'weight_pct',
                'risk_score', 'level', 'valuation', 'pe', 'ps', 'net_margin', 'revenue_growth', 'rsi', 'trend']
COMPACT_NAME_CHARS = 22
_GENERIC_NAME_WORDS = {'banco', 'grupo', 'the', 'first', 'global', 'united', 'american', 'international',
                       'corporation', 'company', 'inc', 'holdings', 'fondo', 'fund', 'trust', 'ishares',
                       'spdr', 'vanguard', 'invesco'}


def _fmt(v):
    if v is None:
        return '-'
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, float):
        if abs(v) >= 1e9:
            return f'{v / 1e9:.2f}B'
        if abs(v) >= 1e6:
            return f'{v / 1e6:.2f}M'
        return f'{v:g}' if abs(v) >= 1 or v == 0 else f'{v:.4g}'
    return str(v).replace('|', '/')


def focus_tickers(message, history, rows):
    """Tickers nombrados en el mensaje (o en los últimos 2 mensajes del usuario), por símbolo o nombre."""
    texts = [message] + [h.get('content', '') for h in reversed(history or []) if h.get('role') == 'user'][:2]
    found = []
    for text in texts:
        for t, r in rows.items():
            if t in found:
                continue
            hit = re.search(rf'(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])', text)
            if not hit and len(t) >= 3:
                hit = re.search(rf'(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])', text, re.I) \
                    if t.lower() not in {'all', 'are', 'for', 'now', 'big', 'net', 'top', 'low', 'dia', 'mas', 'bien'} else None
            word = (r.get('name') or '').split(' ')[0].strip(',.').lower()
            if not hit and len(word) >= 4 and word not in _GENERIC_NAME_WORDS:
                hit = re.search(rf'\b{re.escape(word)}\b', text, re.I)
            if hit:
                found.append(t)
        if len(found) >= MAX_FOCUS:
            break
    return found[:MAX_FOCUS]


def detail_block(r):
    rep, p = r['_report'], r['_payload']
    L = [f"### {r['ticker']} ({r['name']})"]
    L.append(' · '.join(f'{k}={_fmt(v)}' for k, v in r.items()
                        if not k.startswith('_') and k not in ('ticker', 'name') and v is not None))
    for pl in rep.get('pillars', []):
        items = [f"{m['key']}={_fmt(m.get('value'))} ({m['tier']}, {m['points']}/{m['max']} pts"
                 + (', missing' if m.get('missing') else '') + (', excluded' if m.get('excluded') else '') + ')'
                 for m in pl['metrics']]
        L.append(f"risk pillar {pl['key']} {pl.get('points')}/{pl.get('max')} pts: " + '; '.join(items))
    for q in (p.get('quarters') or [])[-4:]:
        L.append('quarter {}: revenue={} net_income={} fcf={} net_margin={}% eps_adj={} eps_est={} surprise={}% {} rev_yoy={}%'.format(
            q.get('label'), _fmt(q.get('rev')), _fmt(q.get('ni')), _fmt(q.get('fcf')), _fmt(q.get('nm')),
            _fmt(q.get('eps_adj')), _fmt(q.get('eps_est')), _fmt(q.get('surprise_pct')), q.get('beat') or '-',
            _fmt(q.get('rev_yoy'))))
    if p.get('quarters'):
        L.append(f"(quarterly revenue, net_income and fcf in {p.get('unit') or 'units'} of {r.get('financial_currency') or 'USD'})")
    if r['kind'] == 'etf':
        hold = [s for s in p.get('segs') or [] if s.get('sym')][:10]
        if hold:
            L.append('top holdings: ' + ', '.join(f"{s['sym']} {_fmt(s.get('v'))}%" for s in hold))
        if p.get('sectors'):
            L.append('sectors: ' + ', '.join(f"{s['l']} {_fmt(s.get('v'))}%" for s in p['sectors'][:8]))
    ev = (rep.get('events') or [])[-5:]
    if ev:
        L.append('events: ' + '; '.join(f"{e['date']} {e['type']} {_fmt(e.get('value'))}" for e in ev))
    return '\n'.join(L)


def profile_lines(prof, prefs):
    if not prof:
        return []
    sw = ', '.join(f'{k} {_fmt(v)}%' for k, v in prof['sector_weights'].items())
    out = [f"PROFILE (computed from positions): sector weights: {sw or '-'}; crypto {_fmt(prof['crypto_pct'])}%; "
           f"missing sectors: {', '.join(prof['missing_sectors']) or '-'}; weighted avg risk_score {_fmt(prof['avg_risk'])}; "
           f"avg beta {_fmt(prof['avg_beta'])}; avg dividend yield {_fmt(prof['avg_dividend'])}%; "
           f"valuation mix by value: " + ', '.join(f'{k} {_fmt(v)}%' for k, v in prof['valuation_mix'].items())]
    if prefs:
        out.append(f"PREFERENCES (set by the user): max risk level {prefs.get('max_level') or 'any'}; "
                   f"priority {prefs.get('priority') or 'none'}; excluded sectors: {', '.join(prefs.get('exclude_sectors') or []) or 'none'}; "
                   f"min market cap {_fmt(prefs.get('min_mcap_b'))}B USD; avoid crypto: {'yes' if prefs.get('avoid_crypto') else 'no'}")
    return out


def build_context(rows, focus, tot, prof=None, prefs=None):
    head = '|'.join(COMPACT_COLS)
    lines = [f"PORTFOLIO TOTALS (USD): " + ' · '.join(f'{k}={_fmt(v)}' for k, v in tot.items() if v is not None),
             *profile_lines(prof, prefs),
             '', 'ALL TICKERS (one row each, "-" = not available):', head]
    for r in rows.values():
        lines.append('|'.join(_fmt((r.get(c) or '')[:COMPACT_NAME_CHARS] or None) if c == 'name' else _fmt(r.get(c))
                              for c in COMPACT_COLS))
    ctx = '\n'.join(lines)
    details = []
    for t in focus[:MAX_FOCUS]:
        if t in rows:
            block = detail_block(rows[t])
            if len(ctx) + sum(len(d) for d in details) + len(block) > MAX_CONTEXT_CHARS:
                break
            details.append(block)
    if details:
        ctx += '\n\nDETAIL OF THE TICKERS IN THE QUESTION:\n' + '\n\n'.join(details)
    stamps = [r['risk_computed_at'] for r in rows.values() if r.get('risk_computed_at')]
    if stamps:
        ctx += f'\n\nData computed between {min(stamps)[:10]} and {max(stamps)[:10]}.'
    return ctx


def build_prompt(context, lang):
    lang_name = 'Spanish (Argentina, neutral register)' if lang.startswith('es') else 'English'
    cols = ', '.join(COLUMNS)
    sectors = ', '.join(explore.SECTORS)
    return f"""You are the Portfolio Agent of a personal investment dashboard. The user is an intermediate investor:
explain terms briefly when useful. Always answer in {lang_name}.

STRICT RULES
1. You only know the DATA below. Quote numbers exactly as they appear in DATA (you may round them).
   Never compute new numbers (no sums, differences, averages, ratios, projections) and never estimate.
   If something is not in DATA, say it is not available in the dashboard.
2. Tables: NEVER write a table yourself. When a table, ranking, list or comparison would help, write one line:
   [[TABLE cols=<comma list> tickers=<all|portfolio|watching|comma list> where=<optional filters> sort=<col or -col> limit=<n> title="<short title>"]]
   Allowed cols: {cols}
   Filters: field:value for categories (valuation:cheap, level:high, kind:stock, category:portfolio), field>n or field<n for numbers.
   The dashboard fills the table with real data. Add at most a few sentences of commentary.
3. Do not give price targets and do not tell the user to buy or sell. You may discuss risk, valuation by the
   dashboard rules, scenarios and what to watch.
4. Be concise. Use short paragraphs or bullet lists (markdown), bold for key ideas.
5. New ideas outside the dashboard: you do NOT know other tickers and must never name tickers that are not in DATA.
   When the user asks for new tickers, ideas or assets to follow, write one line:
   [[EXPLORE intent=<diversify|similar|dividends|value|growth> sectors=<optional comma list> max_beta=<n> min_div=<n> pe_max=<n> min_mcap_b=<n>]]
   Sectors (Yahoo): {sectors}.
   Use the PROFILE and PREFERENCES in DATA to choose. Only add the optional parameters the user asked for.
   Then write one or two sentences explaining the search; the dashboard searches Yahoo and shows candidates
   "to watch" with real data and the reasons they fit.

{GLOSSARY}

DATA
{context}"""


# ── Respuesta: tablas y verificación ────────────────────────────────────────

_TABLE_RE = re.compile(r'\[\[\s*TABLE\b(.*?)\]\]', re.I | re.S)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*("([^"]*)"|\'([^\']*)\'|[^\s\]]+)')
_COND_RE = re.compile(r'^\s*(\w+)\s*(:|>=|<=|>|<|=)\s*(.+?)\s*$')
_FORBIDDEN_CHAT = re.compile('|'.join([
    r'\bprice target\b', r'\btarget price\b', r'\bprecio objetivo\b', r'\bobjetivo de precio\b',
    r'\bstrong buy\b', r'\bcompra fuerte\b', r'\bfair value of \$', r'\bvalor justo de \$',
    r'\b(?:te\s+)?(?:recomiendo|recomendamos|sugiero)\s+(?:que\s+)?(?:compres|vendas|comprar|vender)\b',
    r'\b(?:i|we)\s+recommend\s+(?:buying|selling)\b', r'\byou should (?:buy|sell)\b',
    r'\b(?:deberías|tenés que|tendrías que)\s+(?:comprar|vender)\b',
]), re.I)


def _attrs(s):
    return {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None
                                 else m.group(2)) for m in _ATTR_RE.finditer(s)}


def _num(s):
    v = narrative.parse_number(s.strip().rstrip('%x'))
    return v


def _match(row, cond):
    m = _COND_RE.match(cond)
    if not m:
        return True
    field, op, val = m.group(1).lower(), m.group(2), m.group(3).strip().strip('"\'')
    if field not in COLUMNS:
        return True
    v = row.get(field)
    if op in (':', '='):
        if field in CATEGORICAL or isinstance(v, str) or v is None:
            return v is not None and str(v).lower() == val.lower()
        n = _num(val)
        return n is not None and v is not None and abs(v - n) < 1e-9
    n = _num(val)
    if n is None or v is None or isinstance(v, str):
        return False
    return {'>': v > n, '<': v < n, '>=': v >= n, '<=': v <= n}[op]


def build_table(spec, rows, lang):
    """Arma la tabla pedida por el modelo con datos reales. None si no hay columnas válidas."""
    a = _attrs(spec)
    cols = [c.strip().lower() for c in (a.get('cols') or '').split(',') if c.strip()]
    cols = [c for c in dict.fromkeys(['ticker'] + cols) if c in COLUMNS][:MAX_TABLE_COLS]
    if len(cols) < 2:
        return None
    sel = (a.get('tickers') or 'all').strip()
    if sel.lower() in ('all', 'todos', '*'):
        tickers = list(rows)
    elif sel.lower() in ('portfolio', 'watching'):
        tickers = [t for t, r in rows.items() if r.get('category') == sel.lower()]
    else:
        tickers = [t.strip().upper() for t in sel.split(',') if t.strip()]
    data = []
    for t in tickers:
        r = rows.get(t) or {'ticker': t}
        if all(_match(r, c) for c in (a.get('where') or '').split(',') if c.strip()):
            data.append({c: r.get(c) for c in cols})
    sort = (a.get('sort') or '').strip().lower()
    desc = sort.startswith('-')
    key = sort.lstrip('-+')
    if key in COLUMNS:
        present = [d for d in data if d.get(key) is not None]
        absent = [d for d in data if d.get(key) is None]
        present.sort(key=lambda d: (d[key] if not isinstance(d[key], str) else d[key].lower()), reverse=desc)
        data = present + absent
    try:
        limit = max(1, min(int(a.get('limit') or MAX_TABLE_ROWS), MAX_TABLE_ROWS))
    except ValueError:
        limit = MAX_TABLE_ROWS
    data = data[:limit]
    i = 1 if lang.startswith('es') else 2
    title = (a.get('title') or '').strip()[:80] or None
    return {'title': title, 'columns': [{'key': c, 'label': COLUMNS[c][i], 'unit': COLUMNS[c][0]} for c in cols],
            'rows': data}


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[^\s])')
_LIST_PREFIX = re.compile(r'^(\s*(?:[-*•]|\d+[.)])\s+)')


def _is_model_table_line(line):
    s = line.strip()
    return s.startswith('|') and s.count('|') >= 2


_TICKERISH = re.compile(r'(?<![A-Za-z0-9$/&])([A-Z][A-Z0-9]{1,4}(?:[.-][A-Z]{1,2})?)(?![A-Za-z0-9/&])')
# Siglas que no son tickers (no se omiten aunque no estén en DATA)
NOT_TICKERS = {
    'ETF', 'ETFS', 'RSI', 'EMA', 'SMA', 'USD', 'ARS', 'EUR', 'MEP', 'CCL', 'TTM', 'ROE', 'ROA', 'FCF', 'BPA', 'EPS',
    'NYSE', 'AMEX', 'ADR', 'ADRS', 'CEDEAR', 'IPO', 'CEO', 'CFO', 'PBI', 'GDP', 'IA', 'AI', 'OK', 'EEUU', 'USA', 'UE',
    'EU', 'TIR', 'YTD', 'YOY', 'QOQ', 'MM', 'EBIT', 'EBITDA', 'PER', 'PE', 'PS', 'PEG', 'DCF', 'FED', 'IPC', 'IVA',
    'BCRA', 'TNA', 'TEA', 'FX', 'OTC', 'NAV', 'ESG', 'SEC', 'API', 'CSV', 'IT', 'TV', 'UK', 'HTML', 'PDF', 'NO', 'SI',
    'ARG', 'AR', 'BB', 'DD', 'ATH', 'ATL', 'MACD', 'VWAP', 'ATR', 'NASDAQ', 'SP', 'II', 'III', 'IV', 'VI', 'XL',
}


def unknown_tickers(sent, known):
    out = []
    for tok in _TICKERISH.findall(sent):
        if tok in known or tok in NOT_TICKERS or re.fullmatch(r'Q[1-4]|H[12]|FY\d{2,4}|T[1-4]|\d+[A-Z]?', tok):
            continue
        out.append(tok)
    return out


def verify_text(text, allowed, known=None):
    """Quita oraciones con cifras sin respaldo, contenido prohibido o tickers que no están en los datos
    (el modelo no conoce otros tickers: los candidatos nuevos salen del screener). Devuelve (texto, omitidas)."""
    omitted = {'ungrounded': 0, 'forbidden': 0, 'model_table': 0, 'unknown_ticker': 0}
    out, in_table = [], False
    for line in text.split('\n'):
        if _is_model_table_line(line):
            omitted['model_table'] += 0 if in_table else 1     # se cuentan tablas, no filas
            in_table = True
            continue
        in_table = False
        if re.match(r'^\s*\|?\s*:?-{3,}', line):
            continue
        m = _LIST_PREFIX.match(line)
        prefix, body = (m.group(1), line[m.end():]) if m else ('', line)
        kept = []
        for sent in _SENT_SPLIT.split(body):
            if _FORBIDDEN_CHAT.search(sent):
                omitted['forbidden'] += 1
            elif any(not narrative.is_grounded(n, allowed) for n in narrative.extract_numbers(sent)):
                omitted['ungrounded'] += 1
            elif known is not None and unknown_tickers(sent, known):
                omitted['unknown_ticker'] += 1
            else:
                kept.append(sent)
        if kept:
            out.append(prefix + ' '.join(kept))
        elif not body.strip():
            out.append(line)
    clean = re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip()
    return clean, omitted


def known_symbols(rows):
    known = set()
    for t in rows:
        known.add(t)
        known.add(t.split('-')[0])
    return known


def process_reply(raw, rows, allowed, lang):
    explore_spec = explore.find_spec(raw)
    raw = explore.strip_specs(raw)
    tables, specs = [], _TABLE_RE.findall(raw or '')
    for spec in specs[:MAX_TABLES]:
        t = build_table(spec, rows, lang)
        if t:
            if t['title'] and any(not narrative.is_grounded(n, allowed) for n in narrative.extract_numbers(t['title'])):
                t['title'] = None
            tables.append(t)
    text = _TABLE_RE.sub('', raw or '')
    text, omitted = verify_text(text, allowed, known_symbols(rows))
    return {'reply': text, 'tables': tables, 'omitted': omitted, 'explore_spec': explore_spec}


NO_ANSWER = {
    'es': 'No pude dar una respuesta respaldada por los datos del dashboard. Probá reformular la pregunta '
          'o pedí una tabla con las métricas que te interesan.',
    'en': 'I could not give an answer backed by the dashboard data. Try rephrasing the question '
          'or ask for a table with the metrics you need.',
}


def answer(message, history, lang, rows, tot, call_model, prefs=None):
    """Arma el prompt, llama al modelo y verifica la respuesta. call_model(messages) → texto | None.
    Si el modelo pide una exploración, 'explore_spec' trae el pedido (lo ejecuta el servidor)."""
    lang = 'es' if (lang or 'es').lower().startswith('es') else 'en'
    focus = focus_tickers(message, history, rows)
    prof = explore.profile(rows)
    context = build_context(rows, focus, tot, prof, prefs)
    prompt = build_prompt(context, lang)
    messages = [{'role': 'user', 'content': prompt + '\n\n[Data loaded. Ready.]'},
                {'role': 'assistant', 'content': 'Understood. I will only use the DATA and request tables with [[TABLE ...]].'}]
    for h in (history or [])[-HISTORY_TURNS:]:
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({'role': h['role'], 'content': str(h['content'])[:4000]})
    messages.append({'role': 'user', 'content': message})
    raw = call_model(messages)
    if raw is None:
        return None
    allowed = narrative.extract_numbers(prompt) + narrative.extract_numbers(message)
    out = process_reply(raw, rows, allowed, lang)
    if not out['reply'] and not out['tables'] and out['explore_spec'] is None:
        out['reply'] = NO_ANSWER[lang]
    out['profile'] = prof
    stamps = [r['risk_computed_at'] for r in rows.values() if r.get('risk_computed_at')]
    out.update({'ok': True, 'focus': focus,
                'as_of': {'oldest': min(stamps) if stamps else None, 'newest': max(stamps) if stamps else None}})
    return out
