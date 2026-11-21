"""Fase 9: POST /api/lab/refresh, /health ampliado, codigos de error. Sin red y sin tocar data/ real:
todo con downloaders falsos y las caches en tmp_path (se redirige prices.PRICES_DIR)."""
import glob
import json
import os
import re

import pandas as pd
import pytest
from flask import Flask

from ._helpers import ROOT, load_lab

M = load_lab()
api, prices, refresh = M['api'], M['prices'], M['refresh']
stress_prices, macro_prices, ledger = M['stress_prices'], M['macro_prices'], M['ledger']

REAL_LAB = os.path.join(ROOT, 'data', 'lab')
BOOK = [{'ticker': 'JPM', 'cantidad': 1}, {'ticker': 'SOL', 'cantidad': 1}, {'ticker': 'XYZ', 'cantidad': None}]
JSON = {'Content-Type': 'application/json'}


def _snapshot():
    out = {}
    for dp, _, fs in os.walk(REAL_LAB):
        for f in fs:
            p = os.path.join(dp, f)
            st = os.stat(p)
            out[p] = (st.st_mtime_ns, st.st_size)
    return out


def _multi(tickers, start, end, last_close=100.0):
    idx = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1))
    cols = {(t, 'Close'): pd.Series([last_close + i * 0.1 for i in range(len(idx))], index=idx) for t in tickers}
    df = pd.DataFrame(cols)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


class Down:
    def __init__(self, fn=None):
        self.calls, self.fn = [], fn or _multi

    def __call__(self, tickers, start, end):
        self.calls.append((list(tickers), start, end))
        return self.fn(list(tickers), start, end)


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    """Caches en tmp, libro falso, sin red y sin estado compartido entre tests."""
    def no_network(*a, **k):
        raise AssertionError('un test intento usar la red (yf_downloader real)')
    monkeypatch.setattr(prices, 'yf_downloader', no_network)
    monkeypatch.setattr(prices, 'PRICES_DIR', str(tmp_path))
    monkeypatch.setattr(refresh, '_book_ops', lambda: BOOK)
    monkeypatch.setattr(ledger, 'load_ledger', lambda *a, **k: {'ops': [], 'errors': [], 'counts': {}})
    monkeypatch.setattr(refresh, '_LAST', None)
    assert not refresh.REFRESH_LOCK.locked()
    before = _snapshot()
    yield tmp_path
    assert _snapshot() == before, 'un test modifico data/lab real'
    assert not refresh.REFRESH_LOCK.locked()


def client(downloader=None):
    if downloader is not None:   # el downloader por defecto de los tres pasos
        prices.yf_downloader = downloader
    app = Flask(__name__)
    api.register(app)
    return app.test_client()


@pytest.fixture
def dl(monkeypatch):
    d = Down()
    monkeypatch.setattr(prices, 'yf_downloader', d)
    return d


def post(c, **kw):
    return c.post('/api/lab/refresh', headers=JSON, data='{}', **kw)


# --- POST /refresh ---------------------------------------------------------------------------------
def test_refresh_ok_updates_the_three_caches_and_payload_shape(dl, sandbox):
    r = post(client())
    d = r.get_json()
    assert r.status_code == 200 and d['ok'] is True and d['status'] == 'ok'
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', d['iniciado'])
    assert d['terminado'] >= d['iniciado']
    assert set(d['resultados']) == {'precios', 'estres', 'macro'}
    for k, res in d['resultados'].items():
        assert res['ok'] is True and res['error'] is None and isinstance(res['advertencias'], list), k
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', res['datos_al']), k
    for name in ('closes.csv', 'stress_closes.csv', 'macro_closes.csv'):
        assert (sandbox / name).exists(), name
    # tickers del libro normalizados (SOL -> SOL-USD), indices incluidos, sin tickers sin cantidad
    first = dl.calls[0][0]
    assert 'SOL-USD' in first and 'JPM' in first and 'SPY' in first and 'XYZ' not in first and 'SOL' not in first
    assert set(prices.INDEX_TICKERS) <= set(first)
    assert not glob.glob(str(sandbox / '*.tmp'))


def test_partial_failure_updates_the_rest_and_is_not_a_500(monkeypatch, dl, sandbox):
    def boom(**k):
        raise RuntimeError('yahoo cayo')
    monkeypatch.setattr(stress_prices, 'refresh', boom)
    r = post(client())
    d = r.get_json()
    assert r.status_code == 200 and d['status'] == 'parcial' and d['ok'] is True
    assert d['resultados']['estres']['ok'] is False and d['resultados']['estres']['error'] == 'RuntimeError'
    assert d['resultados']['estres']['datos_al'] is None
    assert d['resultados']['precios']['ok'] and d['resultados']['macro']['ok']
    assert (sandbox / 'closes.csv').exists() and (sandbox / 'macro_closes.csv').exists()
    assert not (sandbox / 'stress_closes.csv').exists()


def test_failure_of_prices_does_not_stop_stress_and_macro(monkeypatch, sandbox):
    calls = []
    def dl(tickers, start, end):
        calls.append(tickers)
        if 'JPM' in tickers:      # solo el lote de precios del libro falla
            raise ConnectionError('sin red')
        return _multi(tickers, start, end)
    monkeypatch.setattr(prices, 'yf_downloader', dl)
    d = post(client()).get_json()
    assert d['status'] == 'parcial' and d['resultados']['precios']['ok'] is False
    assert 'sin red' in d['resultados']['precios']['error']
    assert d['resultados']['macro']['ok'] is True
    assert not (sandbox / 'closes.csv').exists()


def test_all_fail_is_status_error_with_200(monkeypatch, sandbox):
    def dl(tickers, start, end):
        raise ConnectionError('todo caido')
    monkeypatch.setattr(prices, 'yf_downloader', dl)
    r = post(client())
    d = r.get_json()
    assert r.status_code == 200 and d['status'] == 'error' and d['ok'] is False
    assert all(x['ok'] is False and x['error'] for x in d['resultados'].values())
    assert refresh.last_refresh()['status'] == 'error'
    assert not os.listdir(sandbox)          # ninguna cache escrita, ningun .tmp


def test_empty_download_is_reported_not_silent(monkeypatch, sandbox):
    monkeypatch.setattr(prices, 'yf_downloader', lambda t, s, e: pd.DataFrame())
    d = post(client()).get_json()
    assert d['status'] == 'error'
    assert 'vacia' in d['resultados']['macro']['error']
    assert d['resultados']['precios']['ok'] is False and d['resultados']['precios']['datos_al'] is None


def test_concurrent_refresh_returns_409_and_does_not_run(dl):
    c = client()
    assert refresh.REFRESH_LOCK.acquire(blocking=False)
    try:
        r = post(c)
    finally:
        refresh.REFRESH_LOCK.release()
    d = r.get_json()
    assert r.status_code == 409 and d == {'ok': False, 'status': 'en_curso', 'codigo': 'refresh_en_curso',
                                          'error': 'Ya hay un refresh en curso'}
    assert dl.calls == []


def test_lock_is_released_after_a_run_and_after_an_unexpected_error(monkeypatch, dl):
    c = client()
    assert post(c).status_code == 200 and not refresh.REFRESH_LOCK.locked()
    monkeypatch.setattr(refresh, 'run_refresh', lambda: (_ for _ in ()).throw(RuntimeError('x')))
    r = post(c)
    assert r.status_code == 500 and r.get_json()['codigo'] == 'error_interno'
    assert not refresh.REFRESH_LOCK.locked()


@pytest.mark.parametrize('headers', [{}, {'Content-Type': 'text/plain'},
                                     {'Content-Type': 'application/x-www-form-urlencoded'}])
def test_non_json_content_type_is_415_even_when_lock_is_taken(dl, headers):
    c = client()
    r = c.post('/api/lab/refresh', headers=headers, data='{}')
    d = r.get_json()
    assert r.status_code == 415 and d['ok'] is False and d['codigo'] == 'tipo_contenido_invalido'
    assert dl.calls == []


def test_get_on_refresh_is_405_with_code(dl):
    r = client().get('/api/lab/refresh')
    assert r.status_code == 405 and r.get_json()['codigo'] == 'metodo_no_permitido'


def test_price_warnings_are_propagated_with_ticker_and_detail(monkeypatch, dl, sandbox):
    # cache previa con un ajuste inconsistente: la descarga nueva cambia los ratios entre dias
    idx = pd.bdate_range('2026-11-02', periods=6)
    old = pd.DataFrame({t: [100.0] * 6 for t in prices.tickers_for(BOOK)}, index=idx)
    prices.write_cache(old, str(sandbox))
    last = idx[-1]

    def fn(tickers, start, end):
        df = _multi(tickers, start, end)
        for i, d in enumerate(df.index):
            if d in idx[-3:]:
                df.loc[d, ('JPM', 'Close')] = 100.0 * (1 + 0.05 * i)   # ratios nuevo/viejo distintos
        return df
    monkeypatch.setattr(prices, 'yf_downloader', fn)
    d = post(client(), query_string={}).get_json()
    adv = d['resultados']['precios']['advertencias']
    w = [a for a in adv if a['codigo'] == 'inconsistent_adjustment']
    a = next(x for x in w if x['ticker'] == 'JPM')
    assert 'inconsistentes' in a['detalle'] and set(a) == {'codigo', 'ticker', 'detalle'}
    assert last.strftime('%Y-%m-%d') <= d['resultados']['precios']['datos_al']


def test_no_overlap_warning_is_propagated(monkeypatch, dl, sandbox):
    idx = pd.bdate_range('2026-01-05', periods=3)
    prices.write_cache(pd.DataFrame({'JPM': [100.0] * 3}, index=idx), str(sandbox))
    monkeypatch.setattr(prices, 'yf_downloader', lambda t, s, e: _multi(['JPM'], '2026-06-01', '2026-06-10'))
    d = post(client()).get_json()
    codes = {(a['codigo'], a['ticker']) for a in d['resultados']['precios']['advertencias']}
    assert ('no_overlap', 'JPM') in codes


def test_missing_tickers_become_a_warning_not_a_zero(monkeypatch, sandbox):
    monkeypatch.setattr(prices, 'yf_downloader',
                        lambda t, s, e: _multi([x for x in t if x != 'ZZZ'], s, e))
    monkeypatch.setattr(refresh, '_book_ops', lambda: BOOK + [{'ticker': 'ZZZ', 'cantidad': 2}])
    d = post(client()).get_json()
    res = d['resultados']['precios']
    assert res['ok'] is True and res['faltantes'] == ['ZZZ']
    assert any(a['codigo'] == 'sin_datos' and 'ZZZ' in a['detalle'] for a in res['advertencias'])
    assert 'ZZZ' not in prices.read_cache(str(sandbox)).columns       # ausente, no cero


def test_refresh_response_is_strict_json(dl):
    def bad(c):
        raise AssertionError(f'constante no JSON: {c}')
    r = post(client())
    json.loads(r.data.decode(), parse_constant=bad)


# --- Caches: no hay estado en memoria que invalidar; los endpoints leen de disco ------------------
def test_endpoints_reflect_new_data_right_after_a_refresh(monkeypatch, dl):
    c = client()
    assert c.get('/api/lab/macro').get_json()['status'] == 'sin_precios_macro'     # sin cache
    assert post(c).status_code == 200
    d = c.get('/api/lab/macro').get_json()
    assert d['status'] == 'ok' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', d['datos_al'])
    h = c.get('/api/lab/health').get_json()
    assert h['caches']['macro']['datos_al'] == d['datos_al']
    # un segundo refresh con datos mas nuevos cambia lo que ven los endpoints (sin reiniciar)
    monkeypatch.setattr(prices, 'yf_downloader', lambda t, s, e: _multi(t, s, '2030-01-01'))
    post(c)
    assert c.get('/api/lab/macro').get_json()['datos_al'] > d['datos_al']


def test_no_module_level_memory_caches_in_lab():
    """Si alguien agrega una cache en memoria al Lab, este test obliga a invalidarla en refresh."""
    for p in glob.glob(os.path.join(ROOT, 'lab', '*.py')):
        txt = open(p, encoding='utf-8').read()
        assert 'lru_cache' not in txt and 'functools.cache' not in txt, p


# --- /health -----------------------------------------------------------------------------------------
def test_health_without_caches_is_null_not_zero():
    d = client().get('/api/lab/health').get_json()
    assert d['ok'] is True and d['phase'] == 0
    assert d['caches'] == {'precios': {'datos_al': None}, 'estres': {'datos_al': None},
                           'macro': {'datos_al': None}}
    assert d['ultimo_refresh'] is None


def test_health_with_caches_and_last_refresh(dl):
    c = client()
    post(c)
    d = c.get('/api/lab/health').get_json()
    for k in ('precios', 'estres', 'macro'):
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', d['caches'][k]['datos_al'])
    u = d['ultimo_refresh']
    assert u['status'] == 'ok' and set(u['resultados']) == {'precios', 'estres', 'macro'}
    assert u == refresh.last_refresh()


def test_health_survives_an_unreadable_cache(sandbox):
    (sandbox / 'macro_closes.csv').write_text('\x00\x01 no es csv', encoding='utf-8')
    d = client().get('/api/lab/health').get_json()
    assert d['ok'] is True and d['caches']['macro']['datos_al'] is None


# --- Escritura atomica ---------------------------------------------------------------------------------
@pytest.mark.parametrize('mod,fn', [(prices, 'write_cache'), (stress_prices, 'write_stress_cache'),
                                    (macro_prices, 'write_macro_cache')])
def test_failed_write_leaves_no_orphan_tmp(monkeypatch, tmp_path, mod, fn):
    df = pd.DataFrame({'A': [1.0]}, index=pd.bdate_range('2026-01-05', periods=1))
    monkeypatch.setattr(os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disco lleno')))
    with pytest.raises(OSError):
        getattr(mod, fn)(df, str(tmp_path))
    assert os.listdir(tmp_path) == []


# --- Codigos de error ------------------------------------------------------------------------------------
def _with_inputs(monkeypatch):
    dates = ['2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13']
    px = {'SPY': [500.0, 501.0, 502.0, 503.0], 'QQQ': [400.0, 401.0, 402.0, 403.0],
          'JPM': [100.0, 101.0, 102.0, 103.0]}
    ops = [{'fecha': '2026-11-10', 'ticker': 'JPM', 'cantidad': 1.0, 'precio': 100.0, 'monto': 100.0,
            'tipo': 'compra', 'plataforma': 'x', 'titular': 't', 'origen': 'o'}]
    monkeypatch.setattr(api, '_load_inputs', lambda: (dates, px, ops))


def test_error_codes_on_400s(monkeypatch):
    _with_inputs(monkeypatch)
    c = client()
    cases = [('/api/lab/performance?benchmark=NOPE', 'benchmark_invalido'),
             ('/api/lab/correlation?window=abc', 'ventana_invalida'),
             ('/api/lab/correlation?window=2', 'rango_invalido'),
             ('/api/lab/attribution?from=2026-13-99', 'fecha_invalida'),
             ('/api/lab/attribution?to=ayer', 'fecha_invalida'),
             ('/api/lab/attribution?from=2026-11-12&to=2026-11-10', 'rango_invalido'),
             ('/api/lab/attribution?from=2030-01-01', 'rango_invalido'),
             ('/api/lab/stress?escenario=nada', 'escenario_invalido')]
    for url, code in cases:
        r = c.get(url)
        d = r.get_json()
        assert r.status_code == 400 and d['ok'] is False and d['codigo'] == code and d['error'], url


def test_error_codes_on_empty_states_and_http_errors(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    c = client()
    for url in ('/api/lab/performance', '/api/lab/correlation', '/api/lab/attribution',
                '/api/lab/exposure', '/api/lab/stress', '/api/lab/risk-return'):
        d = c.get(url).get_json()
        assert d['status'] == 'sin_precios' and d['codigo'] == 'sin_precios' and d['ok'] is False, url
    d = c.get('/api/lab/nada').get_json()
    assert d['codigo'] == 'ruta_no_encontrada' and d['error']
    r = c.post('/api/lab/journal', data='{}')
    assert r.status_code == 415 and r.get_json()['codigo'] == 'tipo_contenido_invalido'


def test_every_error_code_is_documented_in_the_readme():
    """Todo `codigo` que emite api.py debe estar en la lista del README (la UI traduce por codigo)."""
    src = open(os.path.join(ROOT, 'lab', 'api.py'), encoding='utf-8').read()
    emitted = set(re.findall(r"_err\(\s*'([a-z_]+)'", src)) | set(re.findall(r"'[0-9]+: '([a-z_]+)'", src))
    emitted |= set(api.HTTP_CODES.values()) | {'error_http'}
    readme = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    missing = sorted(c for c in emitted if f'`{c}`' not in readme)
    assert not missing, f'codigos sin documentar en README.md: {missing}'


# --- Sin red, sin escribir en data/ real -----------------------------------------------------------------
def test_lab_tests_never_call_yf_download():
    for p in glob.glob(os.path.join(ROOT, 'tests', 'lab', '*.py')):
        for n, line in enumerate(open(p, encoding='utf-8'), 1):
            code = line.split('#')[0]
            if re.search(r'(=|return|\()\s*yf\.download\s*\(|\bimport yfinance|\bfrom yfinance', code):
                if os.path.basename(p) == 'test_refresh.py' and 'test_lab_tests_never' in code:
                    continue
                pytest.fail(f'{os.path.basename(p)}:{n} usa yfinance directo: {line.strip()}')


def test_full_refresh_does_not_touch_real_data_dir(dl):
    # el fixture `sandbox` compara mtimes/tamanos de data/lab antes y despues
    assert post(client()).status_code == 200
    assert post(client()).status_code == 200
