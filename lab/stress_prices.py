"""Precios historicos para las pruebas de estres (ventanas de 2020 y 2022 que no estan en la
cache principal, que empieza en 2024-12-01). Cache propia: data/lab/prices/stress_closes.csv.

Una sola llamada a yf.download por lote (cierres ajustados, cripto con -USD). Downloader
inyectable (los tests no usan red). Escritura atomica; ante una falla o una descarga vacia la
cache queda intacta (nunca se borra). Reutiliza prices.extract_closes y prices.price_symbol."""
import logging
import os
from datetime import date, datetime, timedelta

import pandas as pd

from . import prices

log = logging.getLogger(__name__)

STRESS_CACHE_NAME = 'stress_closes.csv'
MARGIN_DAYS = 15   # dias de margen antes de la primera ventana (feriados y fines de semana)


def _last(df):
    return df.index[-1].strftime('%Y-%m-%d') if len(df) else None


def cache_path(cache_dir=None):
    return os.path.join(cache_dir or prices.PRICES_DIR, STRESS_CACHE_NAME)


def read_stress_cache(cache_dir=None):
    """Cierres crudos de estres (sin ffill) o DataFrame vacio si no hay cache."""
    p = cache_path(cache_dir)
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = prices._naive_index(df.index)
    return df.sort_index()


def write_stress_cache(df, cache_dir=None):
    d = cache_dir or prices.PRICES_DIR
    os.makedirs(d, exist_ok=True)
    tmp = cache_path(d) + '.tmp'
    try:
        df.to_csv(tmp, index_label='date')
        os.replace(tmp, cache_path(d))
    finally:   # si la escritura falla no queda un .tmp huerfano
        if os.path.exists(tmp):
            os.remove(tmp)


def tickers_needed(holding_tickers, proxies, calendar=prices.CALENDAR_TICKER):
    """Tickers de la cartera actual + indices proxy + SPY, sin duplicados, en simbolo de precio."""
    out = []
    for t in list(holding_tickers) + list(proxies) + [calendar]:
        s = prices.price_symbol(t)
        if s not in out:
            out.append(s)
    return out


def window_bounds(scenarios, datos_al):
    """(start, end) del pedido: desde la primera 'desde' menos margen hasta la ultima 'hasta'
    resuelta (con 'ultimo' = datos_al)."""
    starts = [s['desde'] for s in scenarios]
    ends = [datos_al if s['hasta'] == 'ultimo' else s['hasta'] for s in scenarios]
    ends.append(datos_al)
    start = (datetime.fromisoformat(min(starts)) - timedelta(days=MARGIN_DAYS)).strftime('%Y-%m-%d')
    return start, max(ends)


def update_stress_prices(tickers, start, end, downloader=None, cache_dir=None):
    """Descarga [start, end] (inclusive) de todos los tickers en UNA llamada y actualiza la cache.

    Un ticker con datos nuevos reemplaza su columna completa (el ajuste por dividendos cambia
    toda la serie; no se mezclan niveles). Un ticker sin datos conserva lo cacheado.
    Devuelve {'ok', 'called', 'last_date', 'missing', 'error'}."""
    downloader = downloader or prices.yf_downloader
    tickers = list(dict.fromkeys(tickers))
    cache = read_stress_cache(cache_dir)
    yf_end = (datetime.fromisoformat(end[:10]) + timedelta(days=1)).strftime('%Y-%m-%d')  # end exclusivo
    try:
        new = prices.extract_closes(downloader(tickers, start, yf_end), tickers)
    except Exception as e:  # red caida, limite, etc.: la cache no se toca
        log.warning('Descarga de precios de estres fallida: %s', e)
        return {'ok': False, 'called': True, 'last_date': _last(cache), 'missing': [],
                'error': f'No se pudieron descargar precios de estres: {e}'}
    if new.empty:  # vacio = "sin datos", no "sin cambios": no se sobrescribe
        return {'ok': False, 'called': True, 'last_date': _last(cache), 'missing': tickers,
                'error': 'La descarga de precios de estres llego vacia; se conserva la cache'}
    merged = cache.reindex(cache.index.union(new.index)) if not cache.empty else new.copy()
    for t in new.columns:
        merged[t] = float('nan')
        merged.loc[new.index, t] = new[t].values
    merged = merged.sort_index().dropna(how='all')
    write_stress_cache(merged, cache_dir)
    missing = [t for t in tickers if t not in merged.columns or not merged[t].notna().any()]
    return {'ok': True, 'called': True, 'last_date': _last(merged), 'missing': missing, 'error': None}


def refresh(downloader=None, cache_dir=None, today=None):
    """Arma el pedido desde el libro, los bloques y los escenarios, y actualiza la cache.
    Uso manual: `python -m lab.stress_prices`."""
    from . import blocks, engine, ledger, stress
    ops = prices.to_price_ops(ledger.load_ledger()['ops'])
    dates, px = prices.load_aligned()
    datos_al = dates[-1] if dates else (today or date.today()).isoformat()
    port = engine.portfolio_series(dates, px, ops)
    held = sorted(port['holdings_end']) if port['dates'] else []
    b = blocks.load_blocks()
    scenarios = stress.load_scenarios()
    tickers = tickers_needed(held, b['proxies'].values())
    start, end = window_bounds(scenarios, datos_al)
    return update_stress_prices(tickers, start, end, downloader=downloader, cache_dir=cache_dir)


if __name__ == '__main__':  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    print(refresh())
