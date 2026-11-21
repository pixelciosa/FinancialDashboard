"""Refresco manual de las cachés de precios del Lab (POST /api/lab/refresh).

Orden fijo y fallas aisladas: (a) precios del libro + índices, (b) caché de estrés, (c) caché macro.
Una falla de un paso no impide los demás ni lanza (queda en su `error`). Un lock NO bloqueante a
nivel proceso evita dos refrescos a la vez (el caller responde 409). El último resultado se guarda
en memoria. Los endpoints del Lab leen siempre de disco, así que no hay cachés en memoria que
invalidar: después de un refresh exitoso reflejan los datos nuevos en la siguiente petición.

Seams para tests (sin red): `downloader` (se pasa a los tres pasos) y `cache_dir`."""
import logging
import threading
from datetime import datetime

from . import macro_prices, prices, stress_prices

log = logging.getLogger(__name__)

REFRESH_LOCK = threading.Lock()
_LAST = None
_LAST_GUARD = threading.Lock()


def _now():
    return datetime.now().isoformat(timespec='seconds')


def last_refresh():
    """Resumen del último refresh de este proceso, o None si nunca corrió."""
    with _LAST_GUARD:
        return None if _LAST is None else dict(_LAST)


def _book_ops():
    """Operaciones del libro (los tests lo reemplazan)."""
    from . import ledger
    return ledger.load_ledger()['ops']


def _norm_warnings(ws):
    return [{'codigo': w.get('code'), 'ticker': w.get('ticker'), 'detalle': w.get('message')}
            for w in ws or []]


def _step(fn, warnings_of=None):
    """Corre un paso aislando cualquier excepción. Devuelve el dict normalizado del paso."""
    try:
        res = fn()
    except Exception as e:  # noqa: BLE001 - cada caché falla por separado, nunca produce 500
        log.warning('Refresh del Lab: paso fallido: %s: %s', type(e).__name__, e, exc_info=True)
        return {'ok': False, 'datos_al': None, 'advertencias': [], 'faltantes': [],
                'error': type(e).__name__}
    adv = _norm_warnings(res.get('warnings'))
    faltan = list(res.get('missing') or [])
    if faltan:
        adv.append({'codigo': 'sin_datos', 'ticker': None,
                    'detalle': 'Sin datos descargados para: ' + ', '.join(faltan)})
    ok, error = bool(res.get('ok')), res.get('error')
    if error:
        from .api import _redact
        error = _redact(error)
    if ok and res.get('last_date') is None:   # "ok" sin ningun dato no es un exito: la cache sigue vacia
        ok, error = False, 'La descarga llego sin datos; la cache sigue vacia'
    return {'ok': ok, 'datos_al': res.get('last_date'), 'advertencias': adv,
            'faltantes': faltan, 'error': error}


def summarize_status(results):
    oks = [r['ok'] for r in results.values()]
    if all(oks):
        return 'ok'
    return 'parcial' if any(oks) else 'error'


def run_refresh(downloader=None, cache_dir=None, today=None):
    """Ejecuta los tres pasos (el caller ya tomó REFRESH_LOCK) y guarda el resumen.

    Devuelve {'ok', 'status', 'iniciado', 'terminado', 'resultados': {precios, estres, macro}}.
    `ok` es True si al menos una caché se actualizó (status 'ok' o 'parcial')."""
    global _LAST
    iniciado = _now()

    def precios():
        tickers = prices.tickers_for(_book_ops())   # índices + libro, con price_symbol
        return prices.update_prices(tickers, downloader=downloader, cache_dir=cache_dir, today=today)

    results = {
        'precios': _step(precios),
        'estres': _step(lambda: stress_prices.refresh(downloader=downloader, cache_dir=cache_dir, today=today)),
        'macro': _step(lambda: macro_prices.refresh(downloader=downloader, cache_dir=cache_dir, today=today)),
    }
    status = summarize_status(results)
    out = {'ok': status != 'error', 'status': status, 'iniciado': iniciado, 'terminado': _now(),
           'resultados': results}
    with _LAST_GUARD:
        _LAST = out
    return out


def cache_dates(cache_dir=None):
    """Último día de cada caché ({'precios','estres','macro'}: 'YYYY-MM-DD' o None). Solo lectura."""
    def last(reader):
        try:
            df = reader(cache_dir)
            return df.index[-1].strftime('%Y-%m-%d') if len(df) else None
        except Exception as e:  # noqa: BLE001 - una caché ilegible es 'sin dato', no un 500
            log.warning('No se pudo leer una caché del Lab: %s', e)
            return None
    return {'precios': last(prices.read_cache), 'estres': last(stress_prices.read_stress_cache),
            'macro': last(macro_prices.read_macro_cache)}
