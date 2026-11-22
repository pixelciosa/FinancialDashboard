"""Fase 7: pruebas de estres. Logica con datos SINTETICOS; un test de reproducibilidad contra el
fixture congelado (tests/lab/fixtures/stress_prices.csv) y el libro real. Sin red, sin tocar data/."""
import json
import math
import os

import pandas as pd
import pytest
from flask import Flask

from ._helpers import FIXTURES, load_lab

M = load_lab()
api, blocks, stress, stress_prices = M['api'], M['blocks'], M['stress'], M['stress_prices']
ledger, prices, engine = M['ledger'], M['prices'], M['engine']

BLOCK_DATA = {'bloques': [
    {'id': 'cripto', 'nombre': 'Cripto', 'tickers': ['SOL', 'ADA'], 'indice_proxy': 'SOL-USD', 'bandas': {'referencia_pct': 50.0}},
    {'id': 'tech', 'nombre': 'Tech', 'tickers': ['JPM', 'NEW'], 'indice_proxy': 'QQQ', 'bandas': {'referencia_pct': 30.0}},
    {'id': 'resto', 'nombre': 'Resto', 'tickers': [], 'indice_proxy': 'SPY', 'bandas': {'referencia_pct': 20.0}},
]}
CFG = None


def _cfg():
    global CFG
    if CFG is None:
        bl = blocks.validate_blocks(BLOCK_DATA)
        mapping, warns = blocks.build_map(bl)
        CFG = {'blocks': bl, 'map': mapping, 'proxies': {b['id']: b['indice_proxy'] for b in bl},
               'names': {b['id']: b['nombre'] for b in bl}, 'warnings': warns}
    return CFG


IDX = pd.to_datetime(['2020-01-02', '2020-01-03', '2020-01-06', '2020-01-07', '2020-01-08'])
SCN = {'id': 's', 'nombre': 'stress_s', 'descripcion': 'd', 'nota': 'n', 'desde': '2020-01-03', 'hasta': '2020-01-08'}


def closes(**cols):
    return pd.DataFrame(cols, index=IDX)


NAN = float('nan')
SYN = closes(**{
    'SPY': [100, 100, 98, 95, 90],
    'QQQ': [200, 200, 190, 180, 170],
    'SOL-USD': [10, 10, 11, 12, 15],
    'JPM': [50, 50, 55, 60, 45],            # -10% de 01-03 a 01-08
    'SOL-USD2': [1, 1, 1, 1, 1],
    'NEW': [NAN, NAN, NAN, 5, 6],            # no existia el 01-03 -> proxy QQQ (-15%)
    'SE': [NAN, NAN, NAN, NAN, NAN],       # sin dato en ningun lado, bloque 'resto' (SPY tiene dato)
}).drop(columns='SOL-USD2')


def positions(spec):
    """spec {ticker: valor} -> posiciones con peso."""
    total = sum(spec.values())
    return [{'ticker': t, 'cantidad': 1.0, 'valor': v, 'peso': v / total} for t, v in spec.items()]


def run(closes_df, spec, scn=SCN, cfg=None, datos_al='2020-01-08'):
    cfg = cfg or _cfg()
    return stress.run_all([scn], positions(spec), closes_df, cfg, datos_al)[0]


def by_ticker(res):
    return {p['ticker']: p for p in res['posiciones']}


def strict(obj):
    return json.loads(json.dumps(obj, allow_nan=False))   # falla si hay NaN/Infinity


# --- Retornos ---------------------------------------------------------------------
def test_simple_window_return():
    res = run(SYN, {'JPM': 100.0})
    p = res['posiciones'][0]
    assert p['retorno_pct'] == pytest.approx(-10.0)
    assert p['fuente_retorno'] == 'activo' and p['indice_proxy'] is None
    assert res['impacto_pct'] == pytest.approx(-10.0)
    assert res['impacto_usd'] == pytest.approx(-10.0)
    assert res['cobertura_pct'] == pytest.approx(100.0)


def test_edges_use_last_close_on_or_before_with_ffill():
    scn = dict(SCN, desde='2020-01-04', hasta='2020-01-09')   # sabado y dia sin datos
    r = stress.window_return(SYN, 'JPM', scn['desde'], scn['hasta'])
    assert r == pytest.approx(45 / 50 - 1)
    res = run(SYN, {'JPM': 1.0}, scn=scn)
    assert any(a['tipo'] == 'cierre_distinto_de_borde' for a in res['advertencias'])


def test_stale_close_does_not_count():
    v, d = stress.close_on_or_before(SYN['JPM'], '2020-02-01')   # >7 dias despues del ultimo dato
    assert (v, d) == (None, None)
    assert stress.close_on_or_before(SYN['JPM'], '2019-12-31') == (None, None)


def test_asset_without_history_uses_block_proxy():
    res = run(SYN, {'JPM': 50.0, 'NEW': 50.0})
    p = by_ticker(res)['NEW']
    assert p['fuente_retorno'] == 'proxy' and p['indice_proxy'] == 'QQQ'
    assert p['retorno_pct'] == pytest.approx(-15.0)
    assert p['bloque'] == 'tech'
    assert by_ticker(res)['JPM']['fuente_retorno'] == 'activo'
    assert any(a['tipo'] == 'proxy_usado' for a in res['advertencias'])
    assert res['cobertura_pct'] == pytest.approx(100.0)


def test_neither_asset_nor_proxy_is_null_and_coverage_reported():
    no_spy_proxy = SYN.copy()
    no_spy_proxy['QQQ'] = NAN          # el proxy de 'tech' tampoco tiene dato
    res = run(no_spy_proxy, {'JPM': 60.0, 'NEW': 40.0})
    p = by_ticker(res)['NEW']
    assert p['retorno_pct'] is None and p['aporte_pct'] is None and p['impacto_usd'] is None
    assert p['fuente_retorno'] is None
    assert res['sin_dato'] == ['NEW']
    assert res['cobertura_pct'] == pytest.approx(60.0)
    assert res['impacto_pct'] == pytest.approx(0.6 * -10.0)        # solo sobre lo valuable
    assert res['impacto_pct_sobre_valuado'] == pytest.approx(-10.0)
    blk = res['bloques'][0]
    assert blk['sin_dato'] == ['NEW'] and blk['cobertura_pct'] == pytest.approx(60.0)
    assert blk['retorno_pct'] == pytest.approx(-10.0)      # solo sobre lo valuado del bloque
    assert any(a['tipo'] == 'sin_dato' for a in res['advertencias'])
    strict(res)


def test_all_without_data_gives_null_impact_not_zero():
    res = run(SYN, {'SE': 100.0}, scn=dict(SCN, desde='2020-01-03'))
    # SE cae en 'resto' (proxy SPY con dato): usa proxy
    assert by_ticker(res)['SE']['fuente_retorno'] == 'proxy'
    only_nan = closes(SPY=[100, 100, 98, 95, 90], SE=[NAN] * 5)
    cfg = dict(_cfg(), proxies=dict(_cfg()['proxies'], resto='ZZZ'))   # proxy inexistente
    res = run(only_nan, {'SE': 100.0}, cfg=cfg)
    assert res['impacto_pct'] is None and res['impacto_usd'] is None
    assert res['peor'] is None and res['mejor'] is None
    assert res['cobertura_pct'] == 0.0 and res['sin_dato'] == ['SE']
    strict(res)


def test_one_day_window():
    scn = dict(SCN, desde='2020-01-06', hasta='2020-01-07')
    res = run(SYN, {'JPM': 100.0}, scn=scn)
    assert res['impacto_pct'] == pytest.approx((60 / 55 - 1) * 100)
    assert res['ventana'] == {'desde': '2020-01-06', 'hasta': '2020-01-07',
                              'cierre_desde': '2020-01-06', 'cierre_hasta': '2020-01-07'}


def test_ultimo_resolves_to_datos_al_not_a_hardcoded_date():
    scn = dict(SCN, hasta='ultimo')
    r1 = run(SYN, {'JPM': 100.0}, scn=scn, datos_al='2020-01-08')
    r2 = run(SYN, {'JPM': 100.0}, scn=scn, datos_al='2020-01-07')
    assert r1['ventana']['hasta'] == '2020-01-08' and r2['ventana']['hasta'] == '2020-01-07'
    assert r1['impacto_pct'] == pytest.approx(-10.0) and r2['impacto_pct'] == pytest.approx(20.0)


# --- Pesos, impacto, bloques ------------------------------------------------------
def test_weights_sum_to_100_and_impact_is_sum_weight_times_return():
    spec = {'JPM': 300.0, 'NEW': 150.0, 'SOL': 50.0}
    syn = SYN.copy()
    syn['SOL'] = [10, 10, 11, 12, 15]     # +50%
    res = run(syn, spec)
    ps = res['posiciones']
    assert sum(p['peso_pct'] for p in ps) == pytest.approx(100.0, abs=1e-3)
    expected = sum(p['peso_pct'] * p['retorno_pct'] / 100 for p in ps)
    assert res['impacto_pct'] == pytest.approx(expected, abs=1e-3)
    assert res['impacto_usd'] == pytest.approx(sum(p['impacto_usd'] for p in ps), abs=0.02)
    assert res['impacto_usd'] == pytest.approx(500.0 * res['impacto_pct'] / 100, abs=0.02)
    assert res['mejor']['ticker'] == 'SOL' and res['peor']['ticker'] == 'JPM'   # peor por aporte (peso x retorno)
    assert sum(b['peso_pct'] for b in res['bloques']) == pytest.approx(100.0, abs=1e-3)
    assert sum(b['aporte_pct'] for b in res['bloques']) == pytest.approx(res['impacto_pct'], abs=1e-3)
    assert {b['id'] for b in res['bloques']} == {'cripto', 'tech'}


def test_current_weights_from_holdings_and_last_prices():
    rows, total, sin_precio = stress.current_weights(
        {'A': 2.0, 'B': 1.0, 'C': 5.0, 'D': 0.0}, {'A': 50.0, 'B': 100.0, 'C': None, 'D': 10.0})
    assert total == 200.0 and sin_precio == ['C']
    assert [r['ticker'] for r in rows] == ['A', 'B'] and sum(r['peso'] for r in rows) == pytest.approx(1)
    assert stress.current_weights({}, {}) == ([], None, [])


def test_scenarios_file_valid_and_no_hardcoded_last_date():
    sc = stress.load_scenarios()
    assert [s['id'] for s in sc] == ['tasas_2022', 'metales_2026', 'dia_2026_11_05', 'covid_2020']
    by = {s['id']: s for s in sc}
    assert by['metales_2026']['hasta'] == 'ultimo'
    assert (by['dia_2026_11_05']['desde'], by['dia_2026_11_05']['hasta']) == ('2026-11-04', '2026-11-05')
    assert (by['tasas_2022']['desde'], by['tasas_2022']['hasta']) == ('2022-01-03', '2022-10-12')
    assert (by['covid_2020']['desde'], by['covid_2020']['hasta']) == ('2020-02-19', '2020-03-23')
    assert 'confirmar' in by['tasas_2022']['nota'].lower() and 'confirmar' in by['covid_2020']['nota'].lower()


@pytest.mark.parametrize('mut', [
    lambda s: s.update(desde='2020-13-01'),
    lambda s: s.update(hasta='2019-01-01'),
    lambda s: s.pop('nota'),
])
def test_invalid_scenarios_raise(mut):
    s = dict(SCN)
    mut(s)
    with pytest.raises(stress.ScenariosError):
        stress.validate_scenarios({'escenarios': [s]})


# --- Adaptador de precios de estres -----------------------------------------------
def _fake_download(frame):
    calls = []

    def dl(tickers, start, end):
        calls.append((list(tickers), start, end))
        return frame
    dl.calls = calls
    return dl


def _raw(**cols):
    """Forma de yf.download(group_by='ticker')."""
    parts = {t: pd.DataFrame({'Close': v}, index=IDX) for t, v in cols.items()}
    return pd.concat(parts, axis=1)


def test_update_stress_prices_single_call_and_atomic_write(tmp_path):
    dl = _fake_download(_raw(SPY=[1, 2, 3, 4, 5], JPM=[5, 4, 3, 2, 1]))
    res = stress_prices.update_stress_prices(['SPY', 'JPM', 'SOL-USD'], '2020-01-01', '2020-01-08',
                                             downloader=dl, cache_dir=str(tmp_path))
    assert res['ok'] and len(dl.calls) == 1
    assert dl.calls[0][2] == '2020-01-09'            # fin exclusivo de yfinance
    assert res['missing'] == ['SOL-USD'] and res['last_date'] == '2020-01-08'
    assert sorted(os.listdir(tmp_path)) == ['stress_closes.csv']     # sin .tmp residual
    df = stress_prices.read_stress_cache(str(tmp_path))
    assert list(df['SPY']) == [1, 2, 3, 4, 5]


def test_failed_or_empty_download_keeps_cache(tmp_path):
    ok = _fake_download(_raw(SPY=[1, 2, 3, 4, 5]))
    stress_prices.update_stress_prices(['SPY'], '2020-01-01', '2020-01-08', downloader=ok, cache_dir=str(tmp_path))
    path = tmp_path / 'stress_closes.csv'
    before = path.read_text()

    def boom(*a):
        raise RuntimeError('sin red')
    r = stress_prices.update_stress_prices(['SPY'], '2020-01-01', '2020-01-08', downloader=boom, cache_dir=str(tmp_path))
    assert r['ok'] is False and 'sin red' in r['error'] and path.read_text() == before
    r = stress_prices.update_stress_prices(['SPY'], '2020-01-01', '2020-01-08',
                                           downloader=_fake_download(pd.DataFrame()), cache_dir=str(tmp_path))
    assert r['ok'] is False and path.read_text() == before


def test_tickers_needed_holdings_plus_proxies_plus_spy():
    out = stress_prices.tickers_needed(['JPM', 'SOL', 'ADA-USD'], ['SOL-USD', 'QQQ'])
    assert out == ['JPM', 'SOL-USD', 'ADA-USD', 'QQQ', 'SPY']


def test_window_bounds_uses_datos_al_for_ultimo():
    sc = [dict(SCN, desde='2022-01-03', hasta='2022-10-12'), dict(SCN, desde='2026-01-02', hasta='ultimo')]
    start, end = stress_prices.window_bounds(sc, '2026-11-13')
    assert start == '2021-12-19' and end == '2026-11-13'


# --- Endpoint ---------------------------------------------------------------------
DATES = ['2020-01-07', '2020-01-08']
PX = {'SPY': [100.0, 101.0], 'JPM': [60.0, 45.0], 'NEW': [5.0, 6.0]}
OPS = [{'fecha': '2020-01-07', 'ticker': 'JPM', 'cantidad': 2, 'tipo': 'apertura', 'monto_usd': None},
       {'fecha': '2020-01-07', 'ticker': 'NEW', 'cantidad': 10, 'tipo': 'apertura', 'monto_usd': None}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, PX, OPS))
    monkeypatch.setattr(api, '_load_stress_closes', lambda: SYN)
    monkeypatch.setattr(stress, 'load_scenarios', lambda path=None: [
        SCN, dict(SCN, id='ultimo', hasta='ultimo'), dict(SCN, id='fuera', desde='2022-01-03', hasta='2022-10-12')])
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    return app.test_client()


def _json(resp):
    return json.loads(resp.get_data(as_text=True), parse_constant=lambda c: pytest.fail(f'{c} en JSON'))


def test_stress_endpoint_shape(client):
    r = client.get('/api/lab/stress')
    d = _json(r)
    assert r.status_code == 200 and d['ok'] and d['status'] == 'ok' and d['datos_al'] == '2020-01-08'
    assert [e['id'] for e in d['escenarios']] == ['s', 'ultimo', 'fuera']
    assert sum(p['peso_pct'] for p in d['pesos_actuales']) == pytest.approx(100.0, abs=1e-3)
    assert d['valor_total_usd'] == pytest.approx(2 * 45 + 10 * 6)
    # la cache sintetica solo cubre enero de 2020: 'fuera' queda sin precios, sin 500
    st = {e['id']: e['status'] for e in d['escenarios']}
    assert st == {'s': 'ok', 'ultimo': 'ok', 'fuera': 'sin_precios_estres'}
    assert d['pesos_por_bloque'] and d['advertencias'] == []
    assert all({'nombre', 'ventana', 'descripcion', 'nota'} <= set(e) for e in d['escenarios'])


def test_stress_endpoint_single_scenario_and_invalid_id(client):
    d = _json(client.get('/api/lab/stress?escenario=ultimo'))
    assert [e['id'] for e in d['escenarios']] == ['ultimo']
    r = client.get('/api/lab/stress?escenario=nope')
    assert r.status_code == 400 and 'fuera' in r.get_json()['validos']


def test_stress_endpoint_without_stress_prices_is_200_not_500(client, monkeypatch):
    monkeypatch.setattr(api, '_load_stress_closes', lambda: pd.DataFrame())
    r = client.get('/api/lab/stress')
    d = _json(r)
    assert r.status_code == 200 and d['status'] == 'sin_precios_estres' and d['mensaje']
    assert all(e['status'] == 'sin_precios_estres' and e['impacto_pct'] is None for e in d['escenarios'])


def test_stress_endpoint_without_main_prices(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    r = app.test_client().get('/api/lab/stress')
    assert r.status_code == 200 and r.get_json()['status'] == 'sin_precios'


# --- Reproducibilidad con fixture congelado + libro real --------------------------
STRESS_FIXTURE = os.path.join(FIXTURES, 'stress_prices.csv')
MAIN_FIXTURE = os.path.join(FIXTURES, 'prices_2026-11-13.csv')


def _ledger_available():
    """The demo ledger (sample_data/ledger.csv + investments_ledger.csv, fictional) ships with the repo."""
    return os.path.exists(ledger.LEDGER_PATH)


@pytest.mark.skipif(not (os.path.exists(STRESS_FIXTURE) and os.path.exists(MAIN_FIXTURE) and _ledger_available()),
                    reason='demo ledger or frozen fixtures missing')
def test_frozen_fixture_is_reproducible_and_all_scenarios_have_impact(monkeypatch):
    ops = prices.to_price_ops(ledger.load_ledger()['ops'])
    dates, px = prices.align(pd.read_csv(MAIN_FIXTURE, index_col=0, parse_dates=True))
    sclose = pd.read_csv(STRESS_FIXTURE, index_col=0, parse_dates=True)
    monkeypatch.setattr(api, '_load_inputs', lambda: (dates, px, ops))
    monkeypatch.setattr(api, '_load_stress_closes', lambda: sclose)
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    c = app.test_client()
    raw1, raw2 = c.get('/api/lab/stress').get_data(as_text=True), c.get('/api/lab/stress').get_data(as_text=True)
    assert raw1 == raw2                                              # dos corridas, resultado identico
    d = _json(c.get('/api/lab/stress'))
    assert d['status'] == 'ok' and d['datos_al'] == '2026-11-13'
    assert sum(p['peso_pct'] for p in d['pesos_actuales']) == pytest.approx(100.0, abs=0.01)
    assert len(d['escenarios']) == 4
    for e in d['escenarios']:
        assert e['status'] == 'ok' and e['impacto_pct'] is not None and e['impacto_usd'] is not None, e['id']
        assert e['cobertura_pct'] > 0
        assert sum(p['peso_pct'] for p in e['posiciones']) == pytest.approx(100.0, abs=0.01)
        assert e['impacto_pct'] == pytest.approx(
            sum(p['peso_pct'] * p['retorno_pct'] / 100 for p in e['posiciones'] if p['retorno_pct'] is not None),
            abs=0.02)
    metales = next(e for e in d['escenarios'] if e['id'] == 'metales_2026')
    assert metales['ventana']['hasta'] == '2026-11-13'              # 'ultimo' = datos_al
    covid = next(e for e in d['escenarios'] if e['id'] == 'covid_2020')
    assert any(p['fuente_retorno'] == 'proxy' for p in covid['posiciones'])   # activos que no existian en 2020
