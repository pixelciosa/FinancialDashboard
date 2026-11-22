"""
Datos financieros de la tab "Análisis Financiero" (Fase 1).

Principios:
- Toda cifra sale de yfinance o de un cálculo determinista sobre sus datos. Sin IA.
- Funciones puras para los cálculos; `build_payload` es lo único que consulta la red.
- Los datos contables cambian una vez por trimestre: se guardan en disco y no se
  vuelven a pedir hasta que pueda haber un trimestre nuevo (ver `freshness_state`).
- Dato faltante = None. Nunca 0, nunca NaN.
"""
import json
import math
import os
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'data', 'financials')
SCHEMA_VERSION = 3          # 3: agrega 'metrics' (cifras crudas para el modelo de riesgo)

try:   # crypto symbols from the portfolio CSV (type Crypto) plus common presets
    import portfolio_data as _pd
    CRYPTO_SYMBOLS = set(_pd.crypto_tickers())
except Exception:  # pragma: no cover - the module must import even without the CSV
    CRYPTO_SYMBOLS = {'SOL', 'ADA', 'DOGE', 'DOT', 'LINK', 'AVAX', 'LTC'}
ETF_TTL_DAYS = 30
RECHECK_HOURS = 12
MAX_QUARTERS = 8


# ── Helpers numéricos ────────────────────────────────────────────────────────

def num(v):
    """float limpio o None (NaN, inf, texto y pd.NA pasan a None)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def cell(df, row, col):
    if df is None or getattr(df, 'empty', True) or row not in df.index or col not in df.columns:
        return None
    return num(df.loc[row, col])


def pct(part, whole, decimals=1):
    if part is None or not whole:
        return None
    return round(part / whole * 100, decimals)


def scaled(v, scale):
    return None if v is None else round(v / scale, 3 if scale == 1e9 else 2)


def to_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(v).date()
    except Exception:
        return None


# ── Etiquetas de período ─────────────────────────────────────────────────────

def _effective_date(ts):
    """Corre 45 días hacia atrás: un trimestre que cierra el 27-jul cubre may-jul, o sea Q2."""
    return pd.Timestamp(ts) - pd.Timedelta(days=45)


def quarter_label(ts):
    d = _effective_date(ts)
    return f'Q{(d.month - 1) // 3 + 1} {d.year}'


def fiscal_quarter_label(ts, fiscal_year_end_month):
    """Trimestre fiscal. Apple (cierre en septiembre): el trimestre a junio es 'Q3 FY26'."""
    d = _month_anchor(ts)
    fye = fiscal_year_end_month
    fq = ((d.month - fye - 1) % 12) // 3 + 1
    year = d.year if d.month <= fye else d.year + 1
    return f'Q{fq} FY{str(year)[2:]}'


def _month_anchor(ts):
    """Un cierre en los primeros días del mes pertenece al mes anterior (p. ej. un cierre fiscal el 2-nov)."""
    ts = pd.Timestamp(ts)
    return ts - pd.Timedelta(days=ts.day) if ts.day <= 7 else ts


def fiscal_year_end_month(annual_df):
    if annual_df is None or getattr(annual_df, 'empty', True):
        return 12
    return _month_anchor(annual_df.columns[0]).month


# ── Cálculos ────────────────────────────────────────────────────────────────

def compute_ttm(series_newest_first):
    """Suma de los últimos 4 trimestres. None si falta alguno o hay menos de 4."""
    last4 = list(series_newest_first)[:4]
    if len(last4) < 4 or any(v is None for v in last4):
        return None
    return sum(last4)


def asset_kind(ticker, info=None, crypto_symbols=None):
    sym = ticker.upper()
    crypto = crypto_symbols if crypto_symbols is not None else CRYPTO_SYMBOLS
    if sym in crypto or sym.endswith('-USD'):
        return 'crypto'
    info = info or {}
    if info.get('quoteType') in ('ETF', 'MUTUALFUND'):
        return 'etf'
    if info.get('totalAssets') and not info.get('marketCap'):
        return 'etf'
    return 'stock'


def is_bank(info):
    return (info.get('sector') == 'Financial Services'
            and str(info.get('industry', '')).lower().startswith('banks'))


def dividend_yield_pct(info):
    """Rendimiento por dividendo en %. Se calcula con tasa/precio para evitar la ambigüedad
    de `dividendYield` (Yahoo lo cambió de fracción a porcentaje en 2025)."""
    rate = num(info.get('dividendRate')) or num(info.get('trailingAnnualDividendRate'))
    price = num(info.get('currentPrice')) or num(info.get('regularMarketPrice')) or num(info.get('navPrice'))
    if rate and price:
        return round(rate / price * 100, 2)
    trailing = num(info.get('trailingAnnualDividendYield'))
    if trailing is not None:
        return round(trailing * 100, 2)
    fund_yield = num(info.get('yield'))          # ETFs: rendimiento de distribución del fondo (fracción)
    return round(fund_yield * 100, 2) if fund_yield is not None else None


def data_columns(df):
    """Columnas con datos (más nueva primero). yfinance a veces agrega períodos vacíos."""
    if df is None or getattr(df, 'empty', True):
        return []
    return [c for c in df.columns
            if cell(df, 'Total Revenue', c) is not None or cell(df, 'Net Income', c) is not None]


def build_quarters(q_income, q_cashflow, earnings_hist, fye_month, scale, n=MAX_QUARTERS):
    """Filas trimestrales de la más vieja a la más nueva, con márgenes, YoY y sorpresa de EPS."""
    cols = data_columns(q_income)[:n]            # más nuevo primero
    if not cols:
        return []
    cf_cols = list(q_cashflow.columns) if q_cashflow is not None and not q_cashflow.empty else []
    rows = []
    for col in cols:
        rev = cell(q_income, 'Total Revenue', col)
        gp = cell(q_income, 'Gross Profit', col)
        oi = cell(q_income, 'Operating Income', col)
        ni = cell(q_income, 'Net Income', col)
        cf_col = _match_date(col, cf_cols)
        ocf = cell(q_cashflow, 'Operating Cash Flow', cf_col) if cf_col is not None else None
        capex = cell(q_cashflow, 'Capital Expenditure', cf_col) if cf_col is not None else None
        fcf = ocf + capex if ocf is not None and capex is not None else None
        row = {
            'period_end': pd.Timestamp(col).date().isoformat(),
            'label': quarter_label(col),
            'fiscal_label': fiscal_quarter_label(col, fye_month) if fye_month != 12 else None,
            'rev': scaled(rev, scale), 'ni': scaled(ni, scale), 'fcf': scaled(fcf, scale),
            'gm': pct(gp, rev), 'om': pct(oi, rev), 'nm': pct(ni, rev),
            'eps': num(cell(q_income, 'Diluted EPS', col)),
            '_rev_raw': rev, '_ts': pd.Timestamp(col),
        }
        row.update(_eps_surprise(col, earnings_hist))
        rows.append(row)

    # YoY: el mismo trimestre un año antes (entre 330 y 400 días)
    for r in rows:
        prior = next((p for p in rows if 330 <= (r['_ts'] - p['_ts']).days <= 400), None)
        r['rev_yoy'] = (round((r['_rev_raw'] / prior['_rev_raw'] - 1) * 100, 1)
                        if prior and r['_rev_raw'] is not None and prior['_rev_raw'] else None)
    for r in rows:
        r.pop('_rev_raw'); r.pop('_ts')
    return list(reversed(rows))


def _match_date(col, candidates, tolerance_days=20):
    ts = pd.Timestamp(col)
    best = None
    for c in candidates:
        d = abs((pd.Timestamp(c) - ts).days)
        if d <= tolerance_days and (best is None or d < best[0]):
            best = (d, c)
    return best[1] if best else None


def _eps_surprise(col, earnings_hist):
    """EPS ajustado (el que compara el mercado) vs estimado de analistas."""
    empty = {'eps_adj': None, 'eps_est': None, 'surprise_pct': None, 'beat': None}
    if earnings_hist is None or getattr(earnings_hist, 'empty', True):
        return empty
    match = _match_date(col, list(earnings_hist.index), tolerance_days=45)
    if match is None:
        return empty
    act = num(earnings_hist.loc[match].get('epsActual'))
    est = num(earnings_hist.loc[match].get('epsEstimate'))
    if act is None or est is None:
        return {**empty, 'eps_adj': act, 'eps_est': est}
    diff = act - est
    beat = 'IN LINE' if abs(diff) < 0.005 else ('BEAT' if diff > 0 else 'MISS')
    surprise = round(diff / abs(est) * 100, 1) if est else None
    return {'eps_adj': act, 'eps_est': est, 'surprise_pct': surprise, 'beat': beat}


def build_annual(income, cashflow, scale, n=4):
    """Series anuales de la más vieja a la más nueva."""
    out = {'years': [], 'rev': [], 'fcf': [], 'gm': [], 'om': [], 'nm': [], 'period_ends': []}
    for col in data_columns(income)[:n][::-1]:
        rev = cell(income, 'Total Revenue', col)
        gp = cell(income, 'Gross Profit', col)
        oi = cell(income, 'Operating Income', col)
        ni = cell(income, 'Net Income', col)
        ocf = cell(cashflow, 'Operating Cash Flow', col)
        capex = cell(cashflow, 'Capital Expenditure', col)
        fcf = ocf + capex if ocf is not None and capex is not None else None
        out['years'].append(f'FY{str(pd.Timestamp(col).year)[2:]}')
        out['period_ends'].append(pd.Timestamp(col).date().isoformat())
        out['rev'].append(scaled(rev, scale))
        out['fcf'].append(scaled(fcf, scale))
        out['gm'].append(pct(gp, rev))
        out['om'].append(pct(oi, rev))
        out['nm'].append(pct(ni, rev))
    return out


def ttm_column(q_income, q_cashflow, scale):
    """Valores TTM (últimos 4 trimestres). None si no hay 4 trimestres completos."""
    cols = data_columns(q_income)[:4]
    if len(cols) < 4:
        return None
    def series(df, row, columns):
        return [cell(df, row, c) for c in columns]
    rev = compute_ttm(series(q_income, 'Total Revenue', cols))
    if rev is None:
        return None
    gp = compute_ttm(series(q_income, 'Gross Profit', cols))
    oi = compute_ttm(series(q_income, 'Operating Income', cols))
    ni = compute_ttm(series(q_income, 'Net Income', cols))
    fcf = None
    if q_cashflow is not None and not q_cashflow.empty:
        cf_cols = [_match_date(c, list(q_cashflow.columns)) for c in cols]
        if all(c is not None for c in cf_cols):
            ocf = compute_ttm(series(q_cashflow, 'Operating Cash Flow', cf_cols))
            capex = compute_ttm(series(q_cashflow, 'Capital Expenditure', cf_cols))
            fcf = ocf + capex if ocf is not None and capex is not None else None
    return {'rev': scaled(rev, scale), 'fcf': scaled(fcf, scale), 'gm': pct(gp, rev),
            'om': pct(oi, rev), 'nm': pct(ni, rev), 'period_end': pd.Timestamp(cols[0]).date().isoformat(),
            '_rev_raw': rev, '_ni_raw': ni, '_fcf_raw': fcf}


def etf_annual_returns(history, today=None):
    """Rendimiento anual cierre a cierre (año anterior → año actual). El año en curso es 'YTD'.
    Un año sin cierre del año anterior se descarta (sería un año parcial engañoso)."""
    today = today or date.today()
    if history is None or history.empty:
        return [], []
    closes = history['Close'].dropna()
    closes.index = pd.to_datetime(closes.index)
    year_end = closes.groupby(closes.index.year).last()
    labels, values = [], []
    years = list(year_end.index)
    for i in range(1, len(years)):
        yr = years[i]
        prev = num(year_end.iloc[i - 1])
        cur = num(year_end.iloc[i])
        if not prev or cur is None:
            continue
        labels.append('YTD' if yr == today.year else f'FY{str(yr)[2:]}')
        values.append(round((cur / prev - 1) * 100, 1))
    return labels[-5:], values[-5:]


# ── Frescura (también la usa la Fase 2) ──────────────────────────────────────

def next_check_date(latest_period_end, next_earnings_date):
    """Fecha desde la que puede existir un trimestre nuevo."""
    if next_earnings_date:
        return to_date(next_earnings_date)
    if not latest_period_end:
        return date.today()
    base = pd.Timestamp(latest_period_end) + pd.DateOffset(months=3)
    return (base + pd.Timedelta(days=45)).date()


def freshness_state(latest_period_end, next_earnings_date, today=None, newest_available_period_end=None):
    today = today or date.today()
    latest = to_date(latest_period_end)
    newest = to_date(newest_available_period_end)
    if newest and latest and newest > latest:
        return 'available'      # la fuente manda, aunque el calendario dijera otra fecha
    if today < next_check_date(latest_period_end, next_earnings_date):
        return 'fresh'
    return 'pending'


# ── Caché en disco ───────────────────────────────────────────────────────────

def _cache_path(ticker):
    return os.path.join(CACHE_DIR, f'{ticker.upper()}.json')


def read_cache(ticker):
    try:
        with open(_cache_path(ticker), encoding='utf-8') as f:
            entry = json.load(f)
        return entry if entry.get('schema_version') == SCHEMA_VERSION else None
    except (OSError, ValueError):
        return None


def write_cache(ticker, entry):
    """Escritura atómica con archivo temporal único (varios hilos pueden escribir a la vez)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=f'.{ticker.upper()}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(entry, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _cache_path(ticker))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def ticker_lock(ticker):
    """Un lock por ticker: evita dos descargas simultáneas y que un sondeo pise una descarga."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(ticker.upper(), threading.RLock())


def needs_probe(entry, now=None):
    """True si conviene sondear la fuente para ver si salió un trimestre nuevo."""
    if not entry or entry.get('kind') != 'stock':
        return False          # ETF vence por tiempo; cripto no aplica; sin caché se descarga al abrir
    now = now or datetime.now(timezone.utc)
    newest, latest = to_date(entry.get('newest_available_period_end')), to_date(entry.get('latest_period_end'))
    if newest and latest and newest > latest:
        return False          # ya sabemos que hay datos nuevos: no hace falta volver a preguntar
    state = freshness_state(entry.get('latest_period_end'), entry.get('next_earnings_date'), now.date())
    probed = entry.get('probed_at')
    return state != 'fresh' and (not probed or now - datetime.fromisoformat(probed) > timedelta(hours=RECHECK_HOURS))


def freshness_from_entry(entry, now=None):
    """Estado de frescura a partir de la caché, sin red."""
    now = now or datetime.now(timezone.utc)
    if not entry:
        return {'state': 'unknown'}
    kind = entry.get('kind')
    if kind == 'crypto':
        return {'state': 'unsupported'}
    payload = entry.get('payload') or {}
    base = {'latest_period_end': entry.get('latest_period_end'),
            'latest_period_label': payload.get('latest_period_label'),
            'next_earnings_date': entry.get('next_earnings_date'),
            'fetched_at': entry.get('fetched_at'), 'probed_at': entry.get('probed_at')}
    if kind == 'etf':
        expires = datetime.fromisoformat(entry['fetched_at']) + timedelta(days=ETF_TTL_DAYS)
        return {**base, 'state': 'available' if now >= expires else 'fresh',
                'next_check_date': expires.date().isoformat()}
    newest = entry.get('newest_available_period_end')
    state = freshness_state(entry.get('latest_period_end'), entry.get('next_earnings_date'), now.date(), newest)
    return {**base, 'state': state,
            'next_check_date': next_check_date(entry.get('latest_period_end'), entry.get('next_earnings_date')).isoformat(),
            'newest_available_period_end': newest,
            'newest_available_label': quarter_label(newest) if newest else None}


def get_freshness(ticker, now=None):
    return freshness_from_entry(read_cache(ticker), now)


def probe_newest_period(ticker):
    """Chequeo liviano: una sola llamada (quarterly_income_stmt) y solo la fecha del último trimestre."""
    try:
        cols = data_columns(yf.Ticker(ticker.upper()).quarterly_income_stmt)
    except Exception:
        return None
    return pd.Timestamp(cols[0]).date().isoformat() if cols else None


def check_ticker(ticker, now=None):
    """Sondea la fuente si corresponde y guarda el resultado en la caché. Devuelve la frescura."""
    now = now or datetime.now(timezone.utc)
    with ticker_lock(ticker):
        entry = read_cache(ticker)
        if needs_probe(entry, now):
            newest = probe_newest_period(ticker)
            entry['probed_at'] = now.isoformat()
            if newest:
                entry['newest_available_period_end'] = newest
            write_cache(ticker, entry)
    return freshness_from_entry(entry, now)


# ── Armado del payload (único lugar con red) ─────────────────────────────────

def _safe_attr(obj, name):
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _next_earnings(t, today):
    cal = _safe_attr(t, 'calendar') or {}
    dates = [to_date(d) for d in (cal.get('Earnings Date') or []) if to_date(d)]
    upcoming = sorted(d for d in dates if d >= today)
    return upcoming[0].isoformat() if upcoming else None


def _crypto_payload(ticker):
    return {'ticker': ticker, 'kind': 'crypto', 'supported': False, 'name': ticker, 'type': 'Crypto',
            'etf': False, 'kpis': [], 'segs': [], 'sectors': [], 'quarters': [], 'years': [],
            'rev': [], 'fcf': [], 'gm': [], 'om': [], 'nm': [], 'annualReturns': [],
            'unit': '$B', 'revUnit': '$B', 'has_ttm': False, 'latest_period_end': None,
            'next_earnings_date': None, 'source': None}


def _kpis_common(info):
    price = num(info.get('currentPrice')) or num(info.get('regularMarketPrice')) or num(info.get('navPrice'))
    pe, fwd = num(info.get('trailingPE')), num(info.get('forwardPE'))
    eps, beta = num(info.get('trailingEps')), num(info.get('beta'))
    mcap = num(info.get('marketCap')) or num(info.get('totalAssets'))
    dy = dividend_yield_pct(info)
    return price, [
        ['Price', f'${price:.2f}' if price is not None else None],
        ['P/E', f'{pe:.1f}x' if pe is not None else None],
        ['Fwd P/E', f'{fwd:.1f}x' if fwd is not None else None],
        ['Mkt Cap', _fmt_large(mcap)],
        ['Div Yield', f'{dy:.2f}%' if dy is not None else None],
        ['EPS', f'${eps:.2f}' if eps is not None else None],
        ['Beta', f'{beta:.2f}' if beta is not None else None],
    ]


def _fmt_large(v, currency='USD'):
    if v is None:
        return None
    sym = '$' if currency in (None, 'USD') else f'{currency} '
    for lim, suf in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M')):
        if abs(v) >= lim:
            return f'{sym}{v / lim:.2f}{suf}'
    return f'{sym}{v:.2f}'


def _etf_payload(t, ticker, info, today):
    fd = _safe_attr(t, 'funds_data')
    segs, sectors, expense = [], [], None
    holdings = _safe_attr(fd, 'top_holdings') if fd is not None else None
    if holdings is not None and not holdings.empty:
        for sym, row in holdings.head(10).iterrows():
            v = num(row.get('Holding Percent'))
            if v is not None:
                segs.append({'l': str(row.get('Name') or sym), 'sym': str(sym), 'v': round(v * 100, 2)})
        rest = round(100 - sum(s['v'] for s in segs), 2)
        if segs and rest > 0.5:
            segs.append({'l': 'Resto', 'sym': None, 'v': rest})
    weights = _safe_attr(fd, 'sector_weightings') if fd is not None else None
    if weights:
        sectors = sorted(({'l': k, 'v': round(num(v) * 100, 2)} for k, v in weights.items() if num(v)),
                         key=lambda s: -s['v'])
    ops = _safe_attr(fd, 'fund_operations') if fd is not None else None
    if ops is not None and not ops.empty and 'Annual Report Expense Ratio' in ops.index:
        expense = num(ops.loc['Annual Report Expense Ratio'].iloc[0])

    try:
        hist = t.history(period='6y', interval='1mo')
    except Exception:
        hist = None
    labels, returns = etf_annual_returns(hist, today)
    _, kpis = _kpis_common(info)
    kpis = [k for k in kpis if k[0] not in ('P/E', 'Fwd P/E', 'EPS')]
    kpis.insert(1, ['Expense Ratio', f'{expense * 100:.2f}%' if expense is not None else None])
    return {
        'kind': 'etf', 'etf': True, 'kpis': kpis,
        'segs': segs or [{'l': info.get('category') or 'Diversified', 'sym': None, 'v': 100}],
        'segs_source': 'holdings' if segs else None,
        'sectors': sectors, 'expense_ratio_pct': round(expense * 100, 3) if expense is not None else None,
        'years': labels, 'annualReturns': returns,
        'rev': [], 'fcf': [], 'gm': [], 'om': [], 'nm': [], 'quarters': [],
        'unit': '$B', 'revUnit': '$B', 'has_ttm': False, 'currency': info.get('currency') or 'USD',
        'latest_period_end': None, 'next_earnings_date': None,
    }


def annualize_quarterly(values):
    """Anualiza trimestres (x4). Dato faltante -> None (nunca 0); un 0 real se conserva."""
    return [None if num(v) is None else num(v) * 4 for v in values]


def _stock_payload(t, info, today):
    income = _safe_attr(t, 'income_stmt')
    cashflow = _safe_attr(t, 'cashflow')
    q_income = _safe_attr(t, 'quarterly_income_stmt')
    q_cashflow = _safe_attr(t, 'quarterly_cashflow')
    eh = _safe_attr(t, 'earnings_history')

    revs = []
    if income is not None and not income.empty and 'Total Revenue' in income.index:
        revs = [num(v) for v in income.loc['Total Revenue'].tolist()]
    if not any(revs) and q_income is not None and not q_income.empty and 'Total Revenue' in q_income.index:
        revs = annualize_quarterly(q_income.loc['Total Revenue'].tolist())
    max_rev = max((abs(v) for v in revs if v is not None), default=0)
    scale, unit = (1e9, '$B') if max_rev >= 2e9 else (1e6, '$M')

    fye = fiscal_year_end_month(income)
    annual = build_annual(income, cashflow, scale)
    quarters = build_quarters(q_income, q_cashflow, eh, fye, scale)
    ttm = ttm_column(q_income, q_cashflow, scale)

    latest_annual_end = annual['period_ends'][-1] if annual['period_ends'] else None
    has_ttm = bool(ttm and (latest_annual_end is None or ttm['period_end'] > latest_annual_end))
    if has_ttm:
        annual['years'].append('TTM')
        for k in ('rev', 'fcf', 'gm', 'om', 'nm'):
            annual[k].append(ttm[k])

    bank = is_bank(info)
    if bank:
        annual['gm'] = [None] * len(annual['gm'])
        annual['fcf'] = [None] * len(annual['fcf'])
        for q in quarters:
            q['gm'] = None
            q['fcf'] = None

    _, kpis = _kpis_common(info)
    # Los estados contables pueden estar en otra moneda (p. ej. ADRs que reportan en ARS) aunque cotice en USD.
    currency = info.get('financialCurrency') or 'USD'
    if ttm:
        kpis.insert(1, ['Revenue TTM', _fmt_large(ttm['_rev_raw'], currency)])
        kpis.insert(2, ['Net Income TTM', _fmt_large(ttm['_ni_raw'], currency)])

    latest_period_end = quarters[-1]['period_end'] if quarters else latest_annual_end
    return {
        'kind': 'stock', 'etf': False, 'kpis': kpis,
        'segs': [], 'sectors': [],
        'years': annual['years'], 'rev': annual['rev'], 'fcf': annual['fcf'],
        'gm': annual['gm'], 'om': annual['om'], 'nm': annual['nm'], 'annualReturns': [],
        'quarters': quarters, 'unit': unit, 'revUnit': unit, 'has_ttm': has_ttm,
        'currency': currency,
        'bankNote': bank, 'fiscal_year_end_month': fye,
        'latest_period_end': latest_period_end,
        'latest_period_label': quarters[-1]['label'] if quarters else (annual['years'][-1] if annual['years'] else None),
        'next_earnings_date': _next_earnings(t, today),
        '_ttm_raw': {'revenue_ttm': ttm['_rev_raw'], 'net_income_ttm': ttm['_ni_raw'],
                     'fcf_ttm': ttm['_fcf_raw']} if ttm else {},
    }


def raw_metrics(info):
    """Cifras crudas de `info` (sin formatear) para cálculos en otros módulos."""
    price = num(info.get('currentPrice')) or num(info.get('regularMarketPrice')) or num(info.get('navPrice'))
    dte = num(info.get('debtToEquity'))          # Yahoo lo da en %: 150 = 1.5x
    roe = num(info.get('returnOnEquity'))
    return {
        'price': price, 'pe': num(info.get('trailingPE')), 'fwd_pe': num(info.get('forwardPE')),
        'ps': num(info.get('priceToSalesTrailing12Months')),
        'debt_to_equity': round(dte / 100, 3) if dte is not None else None,
        'current_ratio': num(info.get('currentRatio')), 'beta': num(info.get('beta')),
        'market_cap': num(info.get('marketCap')) or num(info.get('totalAssets')),
        'roe_pct': round(roe * 100, 2) if roe is not None else None,
        'dividend_yield_pct': dividend_yield_pct(info),
        'currency': info.get('currency') or 'USD',
        'financial_currency': info.get('financialCurrency') or info.get('currency') or 'USD',
    }


def build_payload(ticker, crypto_symbols=None, today=None):
    """Consulta yfinance y arma la respuesta completa del endpoint."""
    ticker = ticker.upper().strip()
    today = today or date.today()
    if asset_kind(ticker, crypto_symbols=crypto_symbols) == 'crypto':
        return _crypto_payload(ticker)
    t = yf.Ticker(ticker)
    info = _safe_attr(t, 'info') or {}
    kind = asset_kind(ticker, info, crypto_symbols)
    body = _etf_payload(t, ticker, info, today) if kind == 'etf' else _stock_payload(t, info, today)
    sector = info.get('sector') or info.get('category') or ''
    industry = info.get('industry') or ''
    # Los KPIs son texto para mostrar: un faltante se muestra 'N/A' (los números crudos quedan en None).
    body['kpis'] = [[label, value if value is not None else 'N/A'] for label, value in body['kpis']]
    body['metrics'] = {**raw_metrics(info), **body.pop('_ttm_raw', {})}
    body.update({
        'ticker': ticker, 'supported': True,
        'name': info.get('longName') or info.get('shortName') or ticker,
        'type': f'{sector} · {industry}' if sector and industry else (sector or industry or kind.upper()),
        'source': 'yfinance',
    })
    return body


def _has_data(p):
    return bool(p.get('years') or p.get('quarters') or p.get('segs_source'))


def _response(entry, now, cached, **extra):
    return {**entry['payload'], 'fetched_at': entry['fetched_at'], 'cached': cached,
            'freshness': freshness_from_entry(entry, now), **extra}


def fetch_and_store(ticker, crypto_symbols=None, now=None):
    """Descarga completa y guarda en caché. Si la fuente viene vacía, conserva lo anterior."""
    ticker = ticker.upper().strip()
    now = now or datetime.now(timezone.utc)
    with ticker_lock(ticker):
        return _fetch_and_store_locked(ticker, crypto_symbols, now)


def _fetch_and_store_locked(ticker, crypto_symbols, now):
    entry = read_cache(ticker)
    payload = build_payload(ticker, crypto_symbols, now.date())
    stamp = now.isoformat()
    if entry and not _has_data(payload) and payload.get('supported', True):
        entry['probed_at'] = stamp
        write_cache(ticker, entry)
        return _response(entry, now, cached=True, stale_source=True)
    new_entry = {
        'schema_version': SCHEMA_VERSION, 'ticker': ticker, 'kind': payload['kind'],
        'latest_period_end': payload.get('latest_period_end'),
        'next_earnings_date': payload.get('next_earnings_date'),
        'fetched_at': stamp, 'probed_at': stamp,
        'newest_available_period_end': payload.get('latest_period_end'),
        'payload': payload,
    }
    write_cache(ticker, new_entry)
    return _response(new_entry, now, cached=False)


def get_financials(ticker, crypto_symbols=None, now=None):
    """GET del endpoint: siempre desde la caché. Solo descarga si el ticker nunca se pidió."""
    ticker = ticker.upper().strip()
    now = now or datetime.now(timezone.utc)
    entry = read_cache(ticker)
    if entry:
        return _response(entry, now, cached=True)
    with ticker_lock(ticker):
        entry = read_cache(ticker)          # otro hilo pudo haberlo descargado mientras esperábamos
        if entry:
            return _response(entry, now, cached=True)
        return _fetch_and_store_locked(ticker, crypto_symbols, now)


REFRESHABLE = ('available', 'unknown')


def refresh_ticker(ticker, crypto_symbols=None, now=None):
    """Refresco pedido por el usuario. Devuelve (actualizado, respuesta).
    El servidor decide: sin datos nuevos no se descarga nada."""
    ticker = ticker.upper().strip()
    now = now or datetime.now(timezone.utc)
    with ticker_lock(ticker):
        freshness = get_freshness(ticker)
        if freshness['state'] not in REFRESHABLE:
            return False, {'updated': False, 'state': freshness['state'], 'freshness': freshness}
        data = _fetch_and_store_locked(ticker, crypto_symbols, now)
    return True, {'updated': not data.get('stale_source', False), 'state': data['freshness']['state'],
                  'freshness': data['freshness'], 'data': data}


def check_tickers(tickers, max_workers=4, now=None):
    """Sondeo en lote (lo usa el hilo de fondo). Devuelve {ticker: frescura}."""
    from concurrent.futures import ThreadPoolExecutor
    tickers = [t.upper() for t in tickers]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(zip(tickers, pool.map(lambda t: check_ticker(t, now), tickers)))


def start_background_checker(get_tickers, interval_hours=RECHECK_HOURS, initial_delay_s=30):
    """Hilo daemon: al iniciar (con una demora corta) y cada `interval_hours` sondea solo los
    tickers con caché cuyo próximo reporte ya pasó. No descarga datos completos."""
    import threading

    stop = threading.Event()

    def loop():
        if stop.wait(initial_delay_s):
            return
        while True:
            try:
                tickers = [t for t in get_tickers() if needs_probe(read_cache(t))]
                if tickers:
                    result = check_tickers(tickers)
                    new = [t for t, f in result.items() if f['state'] == 'available']
                    print(f'[financials] sondeo: {len(tickers)} tickers, con datos nuevos: {new or "ninguno"}')
            except Exception as e:
                print(f'[financials] error en el sondeo de fondo: {e}')
            if stop.wait(interval_hours * 3600):
                return

    threading.Thread(target=loop, name='financials-checker', daemon=True).start()
    return stop
