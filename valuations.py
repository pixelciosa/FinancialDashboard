"""
Registro de valuaciones (reemplaza a Comparar).

Clasifica cada ticker en Barata / Justa / Cara / Info no disponible con la MISMA etiqueta que
muestra el Reporte de Riesgo (`risk.compose`), leyendo su caché. Sin IA.

Actualización en lote, precisa y optimizada:
1. Estados contables: solo se sondean los tickers cuyo reporte venció (una llamada liviana) y solo se
   descargan los que tienen un trimestre nuevo o nunca se descargaron (misma compuerta que la tab).
2. Precios: UNA llamada `yf.download` para todos los símbolos (cripto con -USD), en vez de una
   por ticker. Con esos cierres se revalúan P/E, P/E futuro, P/S y rendimiento FCF al día.
3. Fechas de resultados: se reutilizan del reporte anterior si el trimestre no cambió.
4. Se escribe la caché de riesgo: registro, tab de Riesgo y sidebar muestran lo mismo.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

import financials as fin
import risk

GROUPS = ('cheap', 'fair', 'expensive', 'na')
VALUATION_KEYS = ('pe', 'fwd_vs_pe', 'ps', 'fcf_yield')
BATCH_SIZE = 60          # símbolos por llamada a yf.download
FIN_WORKERS = 4

EXECUTOR = ThreadPoolExecutor(max_workers=1)
_LOCK = threading.Lock()
JOB = {'running': False, 'total': 0, 'done': 0, 'current': None, 'started_at': None,
       'finished_at': None, 'updated': [], 'failed': [], 'download_calls': 0}


# ── Clasificación (sin red) ─────────────────────────────────────────────────

def classify(report):
    """(grupo, motivo). El grupo sale de valuation_label; sin etiqueta va a 'na' con el motivo."""
    if not report:
        return 'na', 'no_data'
    label = report.get('valuation_label')
    if label in ('cheap', 'fair', 'expensive'):
        return label, None
    kind = report.get('kind')
    if kind in ('etf', 'crypto'):
        return 'na', kind
    return 'na', report.get('valuation_reason') or 'insufficient_coverage'


def row(ticker, report, meta=None):
    meta = meta or {}
    group, reason = classify(report)
    out = {'ticker': ticker, 'name': (report or {}).get('name') or meta.get('name') or ticker,
           'category': meta.get('category'), 'kind': (report or {}).get('kind'),
           'group': group, 'reason': reason}
    if not report:
        return out
    val = next((p for p in report.get('pillars', []) if p['key'] == 'valuation'), None)
    metrics = {}
    if val:
        for m in val['metrics']:
            if m['key'] in VALUATION_KEYS:
                metrics[m['key']] = {'value': m.get('value'), 'tier': m.get('tier'), 'points': m.get('points'),
                                     'max': m.get('max'), 'missing': m.get('missing'),
                                     'excluded': m.get('excluded', False), 'note': m.get('note')}
    basis = report.get('valuation_basis') or {}
    out.update({
        'valuation_points': val['points'] if val else None, 'valuation_max': val['max'] if val else None,
        'valuation_pct': round(val['points'] / val['max'] * 100, 1) if val and val['max'] else None,
        'metrics': metrics, 'price': (report.get('market') or {}).get('price'),
        'price_date': report.get('price_date'), 'repriced': basis.get('repriced'),
        'price_ref': basis.get('price_ref'), 'price_ref_date': basis.get('price_ref_date'),
        'financials_period': report.get('financials_period'),
        'financials_period_label': report.get('financials_period_label'),
        'risk_score': report.get('risk_score'), 'level': report.get('level'),
        'computed_at': report.get('computed_at'),
    })
    return out


def table(tickers, meta_by_ticker=None):
    """Filas del registro desde la caché de riesgo (sin red)."""
    meta_by_ticker = meta_by_ticker or {}
    rows = [row(t, risk.read_cache(t), meta_by_ticker.get(t)) for t in tickers]
    counts = {g: sum(1 for r in rows if r['group'] == g) for g in GROUPS}
    stamps = [r['computed_at'] for r in rows if r.get('computed_at')]
    return {'rows': rows, 'counts': counts, 'oldest': min(stamps) if stamps else None,
            'newest': max(stamps) if stamps else None, 'refresh': status()}


# ── Precios en lote ─────────────────────────────────────────────────────────

def _split_download(df, symbols):
    """yf.download devuelve columnas (símbolo, campo) con group_by='ticker'. Con un solo símbolo
    algunas versiones devuelven columnas simples."""
    out = {}
    if df is None or getattr(df, 'empty', True):
        return out
    if isinstance(df.columns, pd.MultiIndex):
        level0 = set(df.columns.get_level_values(0))
        for s in symbols:
            if s in level0:
                sub = df[s].dropna(how='all')
                if not sub.empty:
                    out[s] = sub
    elif len(symbols) == 1:
        sub = df.dropna(how='all')
        if not sub.empty:
            out[symbols[0]] = sub
    return out


def download_histories(symbols):
    """{símbolo: DataFrame diario de 1 año con Close, Dividends y Stock Splits}. Devuelve también
    la cantidad de llamadas hechas (para medir la optimización)."""
    symbols = list(dict.fromkeys(symbols))
    out, calls = {}, 0
    for i in range(0, len(symbols), BATCH_SIZE):
        chunk = symbols[i:i + BATCH_SIZE]
        calls += 1
        try:
            df = yf.download(chunk, period='1y', interval='1d', group_by='ticker', actions=True,
                             auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f'[valuations] descarga en lote falló ({len(chunk)} símbolos): {e}')
            continue
        out.update(_split_download(df, chunk))
    return out, calls


# ── Actualización ───────────────────────────────────────────────────────────

def status():
    with _LOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in JOB.items()}


def _set(**kw):
    with _LOCK:
        JOB.update(kw)


def _sync_financials(tickers, crypto_symbols, now):
    """Sondea solo los vencidos y descarga solo lo nuevo o lo que nunca se descargó."""
    stocks = [t for t in tickers if fin.asset_kind(t, crypto_symbols=crypto_symbols) != 'crypto']
    to_probe = [t for t in stocks if fin.needs_probe(fin.read_cache(t), now)]
    if to_probe:
        fin.check_tickers(to_probe, max_workers=FIN_WORKERS, now=now)
    missing = [t for t in stocks if fin.read_cache(t) is None]
    available = [t for t in stocks if t not in missing and fin.get_freshness(t, now)['state'] == 'available']

    def work(t):
        try:
            if t in missing:
                fin.get_financials(t, crypto_symbols=crypto_symbols, now=now)
            else:
                fin.refresh_ticker(t, crypto_symbols=crypto_symbols, now=now)
        except Exception as e:
            print(f'[valuations] financials {t}: {e}')
    if missing or available:
        with ThreadPoolExecutor(max_workers=FIN_WORKERS) as pool:
            list(pool.map(work, missing + available))
    return {'probed': to_probe, 'downloaded': missing, 'refreshed': available}


def refresh_all(tickers, crypto_symbols=None, now=None):
    """Recalcula el riesgo (y la valuación) de todos los tickers con una sola descarga de precios.
    Un ticker que falla no frena al resto. Devuelve el resumen del trabajo."""
    now = now or datetime.now(timezone.utc)
    tickers = list(dict.fromkeys(t.upper() for t in tickers))
    crypto = crypto_symbols if crypto_symbols is not None else fin.CRYPTO_SYMBOLS
    _set(total=len(tickers), done=0, current='financials', updated=[], failed=[], download_calls=0)
    fin_summary = _sync_financials(tickers, crypto, now)

    _set(current='prices')
    sym_of = {t: risk.source_symbol(t, crypto) for t in tickers}
    histories, calls = download_histories(list(sym_of.values()))
    _set(download_calls=calls)

    updated, failed = [], []
    for i, t in enumerate(tickers):
        _set(current=t)
        try:
            hist = histories.get(sym_of[t])
            if hist is None or len(hist.get('Close', pd.Series(dtype=float)).dropna()) < 30:
                hist = risk._history(sym_of[t])          # el lote no lo trajo: reintento individual
            cached = risk.read_cache(t)
            with fin.ticker_lock('risk:' + t):
                report = risk.build_report(t, crypto, now, hist=hist, prev=cached)
                res = risk.store(t, report, cached)
            if res.get('stale_source') or (res['market'].get('n_days', 0) < 30):
                failed.append({'ticker': t, 'reason': 'no_prices'})
            else:
                updated.append(t)
        except Exception as e:
            print(f'[valuations] {t}: {e}')
            failed.append({'ticker': t, 'reason': type(e).__name__})
        _set(done=i + 1, updated=list(updated), failed=list(failed))
    return {'updated': updated, 'failed': failed, 'download_calls': calls, 'financials': fin_summary}


def start_refresh(tickers, crypto_symbols=None):
    """Lanza la actualización en segundo plano. False si ya hay una en curso."""
    with _LOCK:
        if JOB['running']:
            return False
        JOB.update(running=True, started_at=datetime.now(timezone.utc).isoformat(), finished_at=None,
                   total=len(tickers), done=0, current=None, updated=[], failed=[], download_calls=0)

    def run():
        try:
            refresh_all(tickers, crypto_symbols)
        except Exception as e:
            print(f'[valuations] actualización interrumpida: {e}')
            with _LOCK:
                JOB['failed'] = JOB['failed'] + [{'ticker': None, 'reason': type(e).__name__}]
        finally:
            _set(running=False, current=None, finished_at=datetime.now(timezone.utc).isoformat())
    EXECUTOR.submit(run)
    return True
