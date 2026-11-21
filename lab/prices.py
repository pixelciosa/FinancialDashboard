"""Precios del Lab: cierres diarios ajustados, cache en data/lab/prices/ y alineacion al
calendario de SPY. Adaptador propio (no usa server.py). Datos faltantes son None, nunca 0.

El downloader es inyectable: `downloader(tickers, start, end) -> DataFrame` con la forma de
`yf.download(..., group_by='ticker')`. Los tests usan uno falso (sin red)."""
import logging
import math
import os
from datetime import date, datetime, timedelta

import pandas as pd

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_DIR = os.path.join(ROOT, 'data', 'lab', 'prices')
CACHE_NAME = 'closes.csv'
START = '2024-12-01'
CALENDAR_TICKER = 'SPY'

# Indices de comparacion (el orden es el de la UI). Bitcoin (BTC-USD) es solo un indice de referencia; DX-Y.NYB es el indice
# del dolar (se agrega al final: correlacion movil de la Fase 4).
INDEX_TICKERS = ['SPY', 'QQQ', 'XLK', 'SMH', 'XLC', 'XLY', 'XLF', 'XLV', 'XLI', 'XLP', 'XLE',
                 'XLB', 'XLU', 'XLRE', 'GLD', 'SPYD', 'EWZ', 'ARGT', 'BTC-USD', 'DX-Y.NYB']

# Tickers del libro que son cripto: en yfinance llevan sufijo -USD (sin sufijo puede ser otro activo).
# Comunes + los de tipo Crypto del CSV de portfolio (PORTFOLIO_CSV).
COMMON_CRYPTO = {'SOL', 'ADA', 'DOGE', 'DOT', 'LINK', 'AVAX', 'LTC', 'USDT', 'USDC'}


def _portfolio_crypto():
    import csv
    from .paths import PORTFOLIO_CSV
    try:
        with open(PORTFOLIO_CSV, newline='', encoding='utf-8-sig') as f:
            return {(r.get('ticker') or '').strip().upper() for r in csv.DictReader(f)
                    if (r.get('type') or '').strip().lower() == 'crypto'}
    except OSError:
        return set()


CRYPTO = COMMON_CRYPTO | _portfolio_crypto()


def price_symbol(ticker):
    """Ticker del libro -> simbolo de yfinance ('SOL' -> 'SOL-USD', 'SPY' -> 'SPY')."""
    t = str(ticker).strip().upper()
    if t.endswith('-USD'):
        return t
    if t in CRYPTO or f'{t}-USD' in INDEX_TICKERS:   # 'BTC' -> 'BTC-USD' (benchmark index alias)
        return f'{t}-USD'
    return t


def to_price_ops(ops):
    """Copia las operaciones del libro con el ticker convertido a simbolo de precio."""
    return [dict(o, ticker=price_symbol(o['ticker'])) for o in ops]


def tickers_for(ops=None, extra=None):
    """Indices + tickers del libro (solo los que tienen cantidad), sin duplicados."""
    out = list(INDEX_TICKERS)
    for o in ops or []:
        if o.get('cantidad') is None:
            continue
        s = price_symbol(o['ticker'])
        if s not in out:
            out.append(s)
    for s in extra or []:
        if s not in out:
            out.append(s)
    return out


# --- Descarga ---------------------------------------------------------------------
def yf_downloader(tickers, start, end):
    """Una sola llamada a yf.download para todo el lote."""
    import yfinance as yf
    return yf.download(list(tickers), start=start, end=end, auto_adjust=True, progress=False,
                       group_by='ticker', threads=True)


def _naive_index(idx):
    idx = pd.to_datetime(idx)
    if getattr(idx, 'tz', None) is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def extract_closes(raw, tickers):
    """DataFrame de yf.download -> DataFrame de cierres (columnas = tickers con datos).

    Acepta MultiIndex ticker-first o field-first, y el caso de un solo simbolo sin MultiIndex."""
    tickers = list(tickers)
    if raw is None or len(raw) == 0:
        return pd.DataFrame()
    raw = raw.copy()
    raw.index = _naive_index(raw.index)
    field = 'Close'
    out = {}
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        l0, l1 = set(cols.get_level_values(0)), set(cols.get_level_values(1))
        if l0 & set(tickers):  # ticker-first (group_by='ticker')
            for t in tickers:
                if t in l0:
                    sub = raw[t]
                    f = field if field in sub.columns else ('Adj Close' if 'Adj Close' in sub.columns else None)
                    if f:
                        out[t] = sub[f]
        elif l1 & set(tickers):  # field-first
            for t in tickers:
                if t in l1 and field in l0:
                    out[t] = raw[field][t]
    else:
        if field in cols:  # un solo simbolo, columnas planas
            if len(tickers) == 1:
                out[tickers[0]] = raw[field]
        else:  # ya son cierres por ticker
            for t in tickers:
                if t in cols:
                    out[t] = raw[t]
    df = pd.DataFrame(out)
    df = df.apply(pd.to_numeric, errors='coerce')
    df = df.replace([float('inf'), float('-inf')], float('nan'))
    df = df[~df.index.duplicated(keep='last')].sort_index()
    return df.dropna(axis=1, how='all')


# --- Cache ------------------------------------------------------------------------
def cache_path(cache_dir=None):
    return os.path.join(cache_dir or PRICES_DIR, CACHE_NAME)


def read_cache(cache_dir=None):
    """Cierres crudos (sin ffill) o DataFrame vacio si no hay cache."""
    p = cache_path(cache_dir)
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = _naive_index(df.index)
    return df.sort_index()


def write_cache(df, cache_dir=None):
    d = cache_dir or PRICES_DIR
    os.makedirs(d, exist_ok=True)
    tmp = cache_path(d) + '.tmp'
    try:
        df.to_csv(tmp, index_label='date')
        os.replace(tmp, cache_path(d))
    finally:   # si la escritura falla no queda un .tmp huerfano
        if os.path.exists(tmp):
            os.remove(tmp)


RATIO_TOL = 1e-4   # tolerancia relativa: consistencia entre ratios y ruido de redondeo
REQ_MARGIN_BDAYS = 5  # dias habiles extra hacia atras en el pedido incremental
OVERLAP_DAYS = 3   # ultimos dias cacheados que se comparan con la descarga nueva


def _overlap_ratios(col_old, col_new):
    """Ratios nuevo/viejo en hasta OVERLAP_DAYS ultimos dias cacheados presentes en la descarga."""
    out = []
    for d in col_old.index[-OVERLAP_DAYS:]:
        if d in col_new.index and col_old.loc[d]:
            r = col_new.loc[d] / col_old.loc[d]
            if math.isfinite(r):
                out.append(float(r))
    return out


def _merge_incremental(old, new, warnings=None):
    """Une `new` sobre `old`. Si el precio ajustado cambio (dividendo nuevo), reescala la
    historia vieja con el ratio nuevo/viejo de los ultimos dias solapados, solo si son
    consistentes entre si. Si no lo son, el ticker conserva su cache y se descarta la descarga
    nueva (se agrega una advertencia a `warnings`)."""
    if old.empty:
        return new
    merged = old.copy()
    for t in new.columns:
        col_new = new[t].dropna()
        if col_new.empty:
            continue
        if t not in merged.columns:
            merged[t] = float('nan')
        col_old = merged[t].dropna()
        if not col_old.empty:
            last = col_old.index[-1]
            ratios = _overlap_ratios(col_old, col_new)
            if ratios:
                mean = sum(ratios) / len(ratios)
                if max(ratios) - min(ratios) > RATIO_TOL * abs(mean):
                    msg = (f'{t}: ratios nuevo/viejo inconsistentes en los ultimos {len(ratios)} dias '
                           f'solapados ({min(ratios):.6f}..{max(ratios):.6f}); se conserva la cache y '
                           'se descarta la descarga nueva')
                    log.warning(msg)
                    if warnings is not None:
                        warnings.append({'ticker': t, 'code': 'inconsistent_adjustment',
                                         'ratios': ratios, 'message': msg})
                    continue
                if abs(mean - 1) > RATIO_TOL:
                    merged.loc[merged.index < last, t] = merged.loc[merged.index < last, t] * mean
                merged.loc[merged.index >= last, t] = float('nan')
            else:  # sin solape: se conserva el ultimo cierre cacheado y se anexa lo nuevo sin reescalar
                msg = f'{t}: la descarga no solapa con la cache (ultimo dia cacheado {last:%Y-%m-%d}); se anexa sin reescalar'
                log.info(msg)
                if warnings is not None:
                    warnings.append({'ticker': t, 'code': 'no_overlap',
                                     'last_cached': last.strftime('%Y-%m-%d'), 'message': msg})
                col_new = col_new[col_new.index > last]
                if col_new.empty:
                    continue
        merged = merged.reindex(merged.index.union(col_new.index))
        merged.loc[col_new.index, t] = col_new.values
    return merged.sort_index()


def update_prices(tickers, downloader=None, cache_dir=None, start=START, end=None, today=None):
    """Actualiza la cache con UNA llamada por lote. Incremental: pide desde el ultimo dia
    cacheado de los tickers ya conocidos (los nuevos, desde `start`).

    Devuelve {'ok', 'called', 'last_date', 'missing', 'error', 'warnings'}. Si la descarga falla, la
    cache queda intacta."""
    downloader = downloader or yf_downloader
    tickers = list(dict.fromkeys(tickers))
    today = today or date.today()
    end = end or (today + timedelta(days=1)).isoformat()
    cache = read_cache(cache_dir)
    starts = []
    for t in tickers:
        if t in cache.columns and cache[t].notna().any():
            starts.append(cache[t].dropna().index[-1].strftime('%Y-%m-%d'))
        else:
            starts.append(start)
    first_start = min(starts) if starts else start
    req_start = first_start
    if starts and first_start != start:  # margen para que todos los tickers solapen ~3 dias
        req_start = (pd.Timestamp(first_start) - pd.offsets.BDay(REQ_MARGIN_BDAYS)).strftime('%Y-%m-%d')
    last_wanted = (datetime.fromisoformat(end[:10]) - timedelta(days=1)).strftime('%Y-%m-%d')
    if starts and first_start >= last_wanted:  # todos los tickers ya llegan al ultimo dia pedido
        return {'ok': True, 'called': False, 'last_date': _last_date(cache), 'missing': [], 'error': None, 'warnings': []}
    try:
        raw = downloader(tickers, req_start, end)
        new = extract_closes(raw, tickers)
    except Exception as e:  # red caida, limite, etc.: no se toca la cache
        log.warning('Descarga de precios fallida: %s', e)
        return {'ok': False, 'called': True, 'last_date': _last_date(cache), 'missing': [],
                'error': f'No se pudieron descargar precios: {e}', 'warnings': []}
    warnings = []
    merged = _merge_incremental(cache, new, warnings)
    if not merged.empty:
        write_cache(merged, cache_dir)
    missing = [t for t in tickers if t not in merged.columns or not merged[t].notna().any()]
    return {'ok': True, 'called': True, 'last_date': _last_date(merged), 'missing': missing,
            'error': None, 'warnings': warnings}


def _last_date(df):
    return df.index[-1].strftime('%Y-%m-%d') if len(df) else None


# --- Alineacion -------------------------------------------------------------------
def align(closes, calendar_ticker=CALENDAR_TICKER, end=None):
    """Calendario = dias con cierre de SPY. Los demas activos se alinean con arrastre (ffill).

    Devuelve (dates, prices): dates lista 'YYYY-MM-DD'; prices {ticker: [float|None]}.
    Sin SPY o sin datos devuelve ([], {})."""
    if closes is None or len(closes) == 0 or calendar_ticker not in closes.columns:
        return [], {}
    df = closes.sort_index()
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    cal = df[calendar_ticker].dropna().index
    filled = df.ffill().reindex(cal)
    dates = [d.strftime('%Y-%m-%d') for d in cal]
    prices = {}
    for t in filled.columns:
        col = filled[t]
        if not col.notna().any():
            continue
        prices[t] = [float(v) if (v == v and math.isfinite(v)) else None for v in col.tolist()]
    return dates, prices


def load_aligned(cache_dir=None, end=None):
    return align(read_cache(cache_dir), end=end)
