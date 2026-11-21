"""
Modo Explorar del Portfolio Agent: candidatos nuevos para OBSERVAR (no para comprar).

Reglas (las mismas del resto del dashboard):
- La IA no elige tickers ni números. Gemma solo pide un tipo de búsqueda con una línea
  [[EXPLORE intent=... sectors=... ...]] que se valida contra una lista cerrada.
- Los candidatos salen del screener de Yahoo (yf.screen), filtrados a NYSE/Nasdaq, ordenados por
  capitalización y sin duplicados ni tickers que ya están en el dashboard.
- Cada candidato pasa por el mismo cálculo que el resto (financials.py + risk.py): los números
  de la tabla salen de ahí, no del screener.
- El "por qué encaja" lo arma una plantilla con datos (códigos + valores); no lo escribe la IA.
- La calificación de analistas que trae Yahoo nunca se usa ni se muestra.

Sondeo del screener (yfinance 1.3, scripts/probe_screener.py): sin orden explícito Yahoo devuelve
por símbolo descendente; mezcla OTC (PNK/OQX), duplicados extranjeros y acciones de prueba; no trae
el sector en los resultados; `dividendYield` viene en % y `trailingAnnualDividendYield` en fracción.
"""
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

import financials as fin
import risk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PREFS_PATH = os.path.join(BASE_DIR, 'data', 'explore_profile.json')

# Los 11 sectores de Yahoo (yfinance.const.SECTOR_INDUSTY_MAPPING)
SECTORS = ('Basic Materials', 'Communication Services', 'Consumer Cyclical', 'Consumer Defensive', 'Energy',
           'Financial Services', 'Healthcare', 'Industrials', 'Real Estate', 'Technology', 'Utilities')
# Claves de Ticker.funds_data.sector_weightings (ETFs) → sector de Yahoo
ETF_SECTOR_KEYS = {
    'basic_materials': 'Basic Materials', 'communication_services': 'Communication Services',
    'consumer_cyclical': 'Consumer Cyclical', 'consumer_defensive': 'Consumer Defensive', 'energy': 'Energy',
    'financial_services': 'Financial Services', 'healthcare': 'Healthcare', 'industrials': 'Industrials',
    'realestate': 'Real Estate', 'real_estate': 'Real Estate', 'technology': 'Technology', 'utilities': 'Utilities',
}
ALLOWED_EXCHANGES = {'NYQ', 'NMS', 'NGM', 'NCM', 'ASE'}      # NYSE, Nasdaq (GS/GM/CM), NYSE American
INTENTS = ('diversify', 'similar', 'dividends', 'value', 'growth')
LEVELS = ('low', 'moderate', 'high', 'very_high')
PRIORITIES = (None, 'diversify', 'similar', 'dividends', 'value', 'growth')

# Límites de los parámetros que puede pedir el modelo (se recortan a estos rangos)
BOUNDS = {'max_beta': (0.3, 3.0), 'min_div': (0.0, 15.0), 'pe_max': (3.0, 100.0), 'min_mcap_b': (0.3, 3000.0)}
DEFAULT_MIN_MCAP_B = 2.0
SCREEN_SIZE = 100            # resultados pedidos a Yahoo (se filtran OTC, duplicados y propios)
FETCH_LIMIT = 10             # candidatos que pasan por el cálculo completo
SHOW_LIMIT = 8               # candidatos que se muestran
WORKERS = 4
SCREEN_TTL = 15 * 60

_screen_cache = {}
_screen_lock = threading.Lock()


# ── Preferencias guardadas ──────────────────────────────────────────────────

DEFAULT_PREFS = {'max_level': 'moderate', 'priority': None, 'exclude_sectors': [],
                 'min_mcap_b': DEFAULT_MIN_MCAP_B, 'avoid_crypto': True}


def clean_prefs(p):
    """Valida y normaliza preferencias (lo que no es válido vuelve al valor por defecto)."""
    p = p if isinstance(p, dict) else {}
    out = dict(DEFAULT_PREFS)
    if p.get('max_level') in LEVELS or p.get('max_level') is None:
        out['max_level'] = p.get('max_level', DEFAULT_PREFS['max_level'])
    if p.get('priority') in PRIORITIES:
        out['priority'] = p.get('priority')
    out['exclude_sectors'] = [s for s in p.get('exclude_sectors') or [] if s in SECTORS]
    v = _num(p.get('min_mcap_b'))
    if v is not None:
        out['min_mcap_b'] = _clamp(v, *BOUNDS['min_mcap_b'])
    if isinstance(p.get('avoid_crypto'), bool):
        out['avoid_crypto'] = p['avoid_crypto']
    return out


def load_prefs(path=None):
    try:
        with open(path or PREFS_PATH, encoding='utf-8') as f:
            return clean_prefs(json.load(f))
    except (OSError, ValueError):
        return dict(DEFAULT_PREFS)


def save_prefs(p, path=None):
    p = clean_prefs(p)
    path = path or PREFS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return p


# ── Utilidades ──────────────────────────────────────────────────────────────

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float('inf'), float('-inf')) else f


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def sector_of(payload):
    """Sector de Yahoo de una acción desde la caché financiera ('Technology · Software')."""
    s = ((payload or {}).get('type') or '').split(' · ')[0].strip()
    return s if s in SECTORS else None


# ── Perfil (sin IA, sin red) ────────────────────────────────────────────────

def profile(rows):
    """Perfil del portfolio a partir de las filas de agent.build_rows (solo posiciones con valor)."""
    held = [r for r in rows.values() if r.get('value')]
    total = sum(r['value'] for r in held)
    sectors = {s: 0.0 for s in SECTORS}
    crypto = 0.0
    if total:
        for r in held:
            w = r['value'] / total
            p = r.get('_payload') or {}
            if r.get('kind') == 'crypto':
                crypto += w
            elif r.get('kind') == 'etf' and p.get('sectors'):
                tot_etf = sum(s['v'] for s in p['sectors'] if ETF_SECTOR_KEYS.get(s['l']))
                for s in p['sectors']:
                    name = ETF_SECTOR_KEYS.get(s['l'])
                    if name and tot_etf:
                        sectors[name] += w * s['v'] / tot_etf
            else:
                name = sector_of(p)
                if name:
                    sectors[name] += w

    def wavg(key):
        vals = [(r['value'], r[key]) for r in held if r.get(key) is not None]
        tw = sum(v for v, _ in vals)
        return round(sum(v * x for v, x in vals) / tw, 2) if tw else None

    weights = {s: round(v * 100, 2) for s, v in sectors.items()}
    return {
        'positions': len(held),
        'sector_weights': {s: v for s, v in sorted(weights.items(), key=lambda kv: -kv[1]) if v > 0},
        'missing_sectors': [s for s in SECTORS if weights[s] < 2.0],
        'top_sectors': [s for s, v in sorted(weights.items(), key=lambda kv: -kv[1]) if v >= 2.0][:3],
        'crypto_pct': round(crypto * 100, 2),
        'avg_risk': wavg('risk_score'), 'avg_beta': wavg('beta'), 'avg_dividend': wavg('dividend_yield'),
        'valuation_mix': {lb: round(sum(r['value'] for r in held if r.get('valuation') == lb) / total * 100, 2)
                          if total else None for lb in ('cheap', 'fair', 'expensive')},
    }


# ── Pedido del modelo → parámetros validados ────────────────────────────────

_EXPLORE_RE = re.compile(r'\[\[\s*EXPLORE\b(.*?)\]\]', re.I | re.S)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*("([^"]*)"|\'([^\']*)\'|[^\s\]]+)')


def find_spec(text):
    m = _EXPLORE_RE.search(text or '')
    return m.group(1) if m else None


def strip_specs(text):
    return _EXPLORE_RE.sub('', text or '')


def parse_spec(spec, prefs=None):
    """Valida el pedido del modelo. Devuelve (params, rechazados). Nada fuera de la lista pasa."""
    prefs = prefs or DEFAULT_PREFS
    a = {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None
                              else m.group(2)) for m in _ATTR_RE.finditer(spec or '')}
    rejected = []
    intent = (a.get('intent') or prefs.get('priority') or 'diversify').strip().lower()
    if intent not in INTENTS:
        rejected.append(f'intent={intent}')
        intent = prefs.get('priority') or 'diversify'
    sectors = []
    for s in (a.get('sectors') or '').split(','):
        s = s.strip()
        if not s:
            continue
        match = next((x for x in SECTORS if x.lower().replace(' ', '') == s.lower().replace(' ', '').replace('_', '')), None)
        if match:
            sectors.append(match)
        else:
            rejected.append(f'sector={s}')
    params = {'intent': intent, 'sectors': [s for s in dict.fromkeys(sectors) if s not in prefs.get('exclude_sectors', [])]}
    for key, (lo, hi) in BOUNDS.items():
        if key in a:
            v = _num(a[key])
            if v is None:
                rejected.append(f'{key}={a[key]}')
            else:
                params[key] = round(_clamp(v, lo, hi), 2)
    params.setdefault('min_mcap_b', prefs.get('min_mcap_b') or DEFAULT_MIN_MCAP_B)
    if intent == 'dividends':
        params.setdefault('min_div', 3.0)
    if intent == 'value':
        params.setdefault('pe_max', 18.0)
    return params, rejected


def target_sectors(params, prof, prefs):
    """Sectores a buscar: los pedidos; si no hay, según la intención y el perfil."""
    excl = set(prefs.get('exclude_sectors') or [])
    if params['sectors']:
        out = params['sectors']
    elif params['intent'] == 'diversify':
        out = prof.get('missing_sectors') or list(SECTORS)
    elif params['intent'] == 'similar':
        out = prof.get('top_sectors') or list(SECTORS)
    else:
        out = list(SECTORS)
    return [s for s in out if s not in excl] or [s for s in SECTORS if s not in excl]


def build_query(params, sectors):
    """Consulta del screener como estructura simple (operador, operandos). Solo campos probados."""
    parts = [('eq', ['region', 'us']), ('gt', ['intradaymarketcap', params['min_mcap_b'] * 1e9])]
    if len(sectors) < len(SECTORS):
        parts.append(('is-in', ['sector'] + list(sectors)) if len(sectors) > 1 else ('eq', ['sector', sectors[0]]))
    if params.get('pe_max') is not None:
        parts.append(('btwn', ['peratio.lasttwelvemonths', 0, params['pe_max']]))
    if params.get('min_div') is not None and params['min_div'] > 0:
        parts.append(('gt', ['forward_dividend_yield', params['min_div']]))     # en %, como devuelve Yahoo
    if params.get('max_beta') is not None:
        parts.append(('lt', ['beta', params['max_beta']]))
    return ('and', parts)


def to_equity_query(q):
    op, operands = q
    if op in ('and', 'or'):
        return yf.EquityQuery(op, [to_equity_query(x) for x in operands])
    return yf.EquityQuery(op, operands)


def run_screen(q, size=SCREEN_SIZE):
    key = json.dumps(q, sort_keys=True, default=str)
    with _screen_lock:
        hit = _screen_cache.get(key)
        if hit and time.time() - hit[0] < SCREEN_TTL:
            return hit[1]
    res = yf.screen(to_equity_query(q), size=size, sortField='intradaymarketcap', sortAsc=False)
    quotes = (res or {}).get('quotes') or []
    with _screen_lock:
        _screen_cache[key] = (time.time(), quotes)
    return quotes


def filter_quotes(quotes, exclude):
    """Solo acciones de NYSE/Nasdaq, sin tickers propios ni duplicados de la misma empresa."""
    out, names, seen = [], set(), set(exclude)
    dropped = {'exchange': 0, 'owned': 0, 'duplicate': 0, 'type': 0}
    for q in quotes:
        sym = (q.get('symbol') or '').upper()
        if not sym:
            continue
        if q.get('quoteType') != 'EQUITY':
            dropped['type'] += 1
            continue
        if q.get('exchange') not in ALLOWED_EXCHANGES or '.' in sym:
            dropped['exchange'] += 1
            continue
        if sym in seen:
            dropped['owned'] += 1
            continue
        name = re.sub(r'[^a-z0-9]', '', (q.get('longName') or q.get('shortName') or sym).lower())
        if name in names:
            dropped['duplicate'] += 1
            continue
        names.add(name)
        seen.add(sym)
        out.append(sym)
    return out, dropped


# ── Candidatos: mismo cálculo que el resto del dashboard ────────────────────

def _analyze(sym):
    try:
        fp = fin.get_financials(sym)
        rep = risk.get_risk(sym)
        return sym, fp, rep
    except Exception as e:
        print(f'[explore] {sym}: {e}')
        return sym, None, None


def _metric(rep, key):
    for p in (rep or {}).get('pillars', []):
        for m in p['metrics']:
            if m['key'] == key and not m.get('missing') and not m.get('excluded'):
                return m.get('value')
    return None


LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}


def candidate_row(sym, fp, rep, params, prof):
    raw = (fp or {}).get('metrics') or {}
    basis = rep.get('valuation_basis') or {}
    k = basis.get('factor') if basis.get('repriced') else None
    div = raw.get('dividend_yield_pct')
    row = {
        'ticker': sym, 'name': fp.get('name') or rep.get('name') or sym, 'sector': sector_of(fp),
        'price': rep.get('market', {}).get('price'), 'price_date': rep.get('price_date'),
        'market_cap': raw.get('market_cap') * k if k and raw.get('market_cap') else raw.get('market_cap'),
        'risk_score': rep.get('risk_score'), 'level': rep.get('level'), 'valuation': rep.get('valuation_label'),
        'pe': _metric(rep, 'pe'), 'dividend_yield': (div / k if k and div is not None else div),
        'beta': raw.get('beta'), 'net_margin': _metric(rep, 'net_margin'),
        'revenue_growth': _metric(rep, 'revenue_growth'),
    }
    for key in ('price', 'market_cap', 'pe', 'dividend_yield', 'beta', 'net_margin', 'revenue_growth'):
        if isinstance(row[key], float):
            row[key] = round(row[key], 2 if key != 'market_cap' else 0)
    # Motivos (códigos + valores reales) y puntaje de encaje: determinista
    reasons, score = [], 0.0
    if row['sector'] and row['sector'] in prof.get('missing_sectors', []):
        reasons.append({'code': 'new_sector', 'sector': row['sector']})
        score += 2 if params['intent'] == 'diversify' else 1
    elif row['sector'] and row['sector'] in prof.get('top_sectors', []):
        reasons.append({'code': 'same_sector', 'sector': row['sector'],
                        'weight': prof['sector_weights'].get(row['sector'])})
        score += 1.5 if params['intent'] == 'similar' else 0
    avg = prof.get('avg_risk')
    if row['risk_score'] is not None and avg is not None:
        if row['risk_score'] < avg:
            reasons.append({'code': 'risk_below_avg', 'risk': row['risk_score'], 'avg': avg})
        score += _clamp((avg - row['risk_score']) / 10, -2, 2)
    if row['valuation']:
        reasons.append({'code': 'valuation', 'label': row['valuation']})
        score += {'cheap': 1.5, 'fair': 0.5, 'expensive': -1}[row['valuation']]
    if row['dividend_yield'] and row['dividend_yield'] >= 2:
        reasons.append({'code': 'dividend', 'value': row['dividend_yield']})
        score += row['dividend_yield'] / (2 if params['intent'] == 'dividends' else 6)
    if row['beta'] is not None and row['beta'] < 0.9:
        reasons.append({'code': 'low_beta', 'value': row['beta']})
        score += 0.5
    if params['intent'] == 'growth' and row['revenue_growth'] is not None:
        if row['revenue_growth'] >= 10:
            reasons.append({'code': 'growth', 'value': row['revenue_growth']})
        score += _clamp(row['revenue_growth'] / 10, -1, 3)
    row['reasons'] = reasons
    row['fit'] = round(score, 2)
    return row


def run(params, prof, prefs, exclude, crypto_symbols=None):
    """Busca, analiza y ordena candidatos. Devuelve el bloque 'explore' de la respuesta del chat."""
    t0 = time.time()
    sectors = target_sectors(params, prof, prefs)
    q = build_query(params, sectors)
    try:
        if params['intent'] == 'growth' and not params['sectors']:
            quotes = (yf.screen('undervalued_growth_stocks', count=SCREEN_SIZE) or {}).get('quotes') or []
        else:
            quotes = run_screen(q)
    except Exception as e:
        print(f'[explore] screener: {e}')
        return {'ok': False, 'error': 'screener_unavailable', 'detail': f'{type(e).__name__}: {str(e)[:160]}',
                'params': params, 'sectors': sectors}
    symbols, dropped = filter_quotes(quotes, exclude)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        analyzed = list(pool.map(_analyze, symbols[:FETCH_LIMIT]))
    rows, skipped = [], {'no_data': 0, 'level': 0, 'sector': 0, 'not_stock': 0}
    max_rank = LEVEL_RANK.get(prefs.get('max_level'), len(LEVELS))
    excl = set(prefs.get('exclude_sectors') or [])
    for sym, fp, rep in analyzed:
        if not fp or not rep or rep.get('risk_score') is None:
            skipped['no_data'] += 1
        elif fp.get('kind') != 'stock':
            skipped['not_stock'] += 1
        elif LEVEL_RANK.get(rep.get('level'), 99) > max_rank:
            skipped['level'] += 1
        elif sector_of(fp) in excl or (params['sectors'] and sector_of(fp) not in params['sectors']
                                       and params['intent'] != 'growth'):
            skipped['sector'] += 1
        else:
            rows.append(candidate_row(sym, fp, rep, params, prof))
    rows.sort(key=lambda r: -r['fit'])
    return {'ok': True, 'params': params, 'sectors': sectors, 'candidates': rows[:SHOW_LIMIT],
            'screened': len(quotes), 'dropped': dropped, 'skipped': skipped,
            'seconds': round(time.time() - t0, 1)}
