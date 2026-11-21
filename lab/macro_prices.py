"""Precios del semaforo macro (Fase 6): cierres ajustados de ^VIX, ^TNX, ^IRX, DX-Y.NYB, CL=F, GLD
y SLV. Cache propia: data/lab/prices/macro_closes.csv (no toca closes.csv ni stress_closes.csv).

Historia desde 2024-01-01: la calibracion arranca el 2024-12-01 y necesita 200 dias habiles de
promedio movil (y 63 de cambio) ANTES de esa fecha. Una sola llamada a yf.download por lote.
Downloader inyectable (los tests no usan red). Escritura atomica; ante una falla o una descarga
vacia la cache queda intacta (nunca se borra). Los faltantes quedan como NaN en la cache (sin
ffill ni ceros); el tratamiento de feriados lo hace lab.macro.
Refresco manual: `python -m lab.macro_prices`."""
import logging
import os
from datetime import date, datetime, timedelta

import pandas as pd

from . import prices

log = logging.getLogger(__name__)

MACRO_CACHE_NAME = 'macro_closes.csv'
MACRO_START = '2024-01-01'
MACRO_SYMBOLS = ['^VIX', '^TNX', '^IRX', 'DX-Y.NYB', 'CL=F', 'GLD', 'SLV']


def _last(df):
    return df.index[-1].strftime('%Y-%m-%d') if len(df) else None


def cache_path(cache_dir=None):
    return os.path.join(cache_dir or prices.PRICES_DIR, MACRO_CACHE_NAME)


def read_macro_cache(cache_dir=None):
    """Cierres crudos macro (sin ffill) o DataFrame vacio si no hay cache."""
    p = cache_path(cache_dir)
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = prices._naive_index(df.index)
    return df.sort_index()


def write_macro_cache(df, cache_dir=None):
    d = cache_dir or prices.PRICES_DIR
    os.makedirs(d, exist_ok=True)
    tmp = cache_path(d) + '.tmp'
    try:
        df.to_csv(tmp, index_label='date')
        os.replace(tmp, cache_path(d))
    finally:   # si la escritura falla no queda un .tmp huerfano
        if os.path.exists(tmp):
            os.remove(tmp)


def update_macro_prices(symbols=None, downloader=None, cache_dir=None, start=MACRO_START, end=None,
                        today=None):
    """Descarga [start, end] de todos los simbolos en UNA llamada y actualiza la cache.

    Un simbolo con datos nuevos reemplaza su columna completa (el ajuste cambia toda la serie; no se
    mezclan niveles). Un simbolo sin datos conserva lo cacheado.
    Devuelve {'ok', 'called', 'last_date', 'missing', 'error'}."""
    downloader = downloader or prices.yf_downloader
    symbols = list(dict.fromkeys(symbols or MACRO_SYMBOLS))
    cache = read_macro_cache(cache_dir)
    last_day = datetime.fromisoformat(end[:10]) if end else datetime.combine(today or date.today(),
                                                                              datetime.min.time())
    yf_end = (last_day + timedelta(days=1)).strftime('%Y-%m-%d')  # end exclusivo
    try:
        new = prices.extract_closes(downloader(symbols, start, yf_end), symbols)
    except Exception as e:  # red caida, limite, etc.: la cache no se toca
        log.warning('Descarga de precios macro fallida: %s', e)
        return {'ok': False, 'called': True, 'last_date': _last(cache), 'missing': [],
                'error': f'No se pudieron descargar precios macro: {e}'}
    if new.empty:  # vacio = "sin datos", no "sin cambios": no se sobrescribe
        return {'ok': False, 'called': True, 'last_date': _last(cache), 'missing': symbols,
                'error': 'La descarga de precios macro llego vacia; se conserva la cache'}
    merged = cache.reindex(cache.index.union(new.index)) if not cache.empty else new.copy()
    for t in new.columns:
        merged[t] = float('nan')
        merged.loc[new.index, t] = new[t].values
    merged = merged.sort_index().dropna(how='all')
    write_macro_cache(merged, cache_dir)
    missing = [t for t in symbols if t not in merged.columns or not merged[t].notna().any()]
    return {'ok': True, 'called': True, 'last_date': _last(merged), 'missing': missing, 'error': None}


def refresh(downloader=None, cache_dir=None, today=None):
    """Uso manual: `python -m lab.macro_prices`."""
    return update_macro_prices(downloader=downloader, cache_dir=cache_dir, today=today)


if __name__ == '__main__':  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    print(refresh())
