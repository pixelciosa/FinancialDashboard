"""Fase 4: exposicion (bandas 5/25, tope por posicion, aporte al riesgo), correlacion movil y
ventas recortadas en la conciliacion. Logica con datos SINTETICOS y archivos temporales; los
tests con el libro real + fixture se saltan si faltan. Ningun test escribe config/lab/blocks.json."""
import json
import os

import numpy as np
import pandas as pd
import pytest
from flask import Flask

from ._helpers import FIXTURES, load_lab

M = load_lab()
api, blocks, exposure, correlation = M['api'], M['blocks'], M['exposure'], M['correlation']
ledger, prices, engine, stress = M['ledger'], M['prices'], M['engine'], M['stress']

RULE = {'nombre': '5/25', 'absoluta_pp': 5.0, 'relativa': 0.25}


def _json(resp):
    return json.loads(resp.get_data(as_text=True), parse_constant=lambda c: pytest.fail(f'{c} en JSON'))


# --- Regla 5/25 -------------------------------------------------------------------
def test_band_absolute_rule_governs_for_large_references():
    b = exposure.band(37.4, RULE)            # 25% de 37,4 = 9,35 > 5 pp: manda 5 pp
    assert b['manda'] == 'absoluta' and b['ancho_pp'] == 5.0
    assert (b['min_pct'], b['max_pct']) == (pytest.approx(32.4), pytest.approx(42.4))


def test_band_relative_rule_governs_for_small_references():
    b = exposure.band(14.9, RULE)            # 25% de 14,9 = 3,725 < 5 pp: manda el 25%
    assert b['manda'] == 'relativa' and b['ancho_pp'] == pytest.approx(3.725)
    assert (b['min_pct'], b['max_pct']) == (pytest.approx(11.175), pytest.approx(18.625))


def test_band_tie_at_20_percent_and_missing_reference():
    assert exposure.band(20.0, RULE)['ancho_pp'] == 5.0
    assert exposure.band(None, RULE) is None


def test_band_follows_the_reference_and_the_rule():
    assert exposure.band(30.0, RULE)['ancho_pp'] == 5.0
    assert exposure.band(10.0, RULE)['ancho_pp'] == 2.5          # cambiar la referencia cambia la banda
    assert exposure.band(30.0, dict(RULE, absoluta_pp=3.0))['ancho_pp'] == 3.0


@pytest.mark.parametrize('peso, estado', [(32.4, 'dentro'), (42.4, 'dentro'), (37.4, 'dentro'),
                                          (42.41, 'arriba'), (32.39, 'abajo'), (None, None)])
def test_band_state(peso, estado):
    assert exposure.band_state(peso, exposure.band(37.4, RULE)) == estado


def test_band_state_without_band_is_none():
    assert exposure.band_state(10.0, None) is None


# --- Tope por posicion ------------------------------------------------------------
CAP = {'aviso_pct': 10.0, 'alerta_pct': 20.0, 'excepciones': {'LINK-USD': 'ancla'}}


@pytest.mark.parametrize('peso, nivel', [(0.0, 'ok'), (9.99, 'ok'), (10.0, 'aviso'), (19.99, 'aviso'),
                                         (20.0, 'alerta'), (45.0, 'alerta'), (None, None)])
def test_position_cap_levels(peso, nivel):
    assert exposure.position_cap(peso, 'JPM', CAP) == (nivel, None)


def test_paxg_exception_never_triggers_even_above_alert_level():
    assert exposure.position_cap(24.96, 'LINK-USD', CAP) == ('ok', 'excepcion')
    assert exposure.position_cap(99.0, 'LINK-USD', CAP) == ('ok', 'excepcion')


# --- Datos sinteticos para compute / endpoints ------------------------------------
N = 90
DATES = [d.strftime('%Y-%m-%d') for d in pd.bdate_range('2026-01-01', periods=N)]


def _walk(seed, drift=0.0005, vol=0.01, start=100.0):
    r = np.random.default_rng(seed).normal(drift, vol, N)
    return list(start * np.cumprod(1 + r))


def _px(**extra):
    px = {'SPY': _walk(1), 'BTC-USD': _walk(2, vol=0.03), 'GLD': _walk(3, vol=0.008), 'QQQ': _walk(4),
          'A': _walk(10), 'B': _walk(11, vol=0.02), 'C': _walk(12, vol=0.015),
          'LINK-USD': _walk(13, vol=0.008)}
    px.update(extra)
    return px


def _ops(**qty):
    return [{'fecha': DATES[0], 'ticker': t, 'cantidad': q, 'tipo': 'apertura', 'monto_usd': None}
            for t, q in qty.items()]


def _cfg(tmp_path, refs=(40.0, 40.0, 20.0), rule=RULE, cap=None, tickers=None):
    """Config real via el lector (JSON temporal): bloques b1(A), b2(B, LINK-USD) y resto (C...)."""
    tickers = tickers or [['A'], ['B', 'LINK'], []]
    data = {'regla_banda': dict(rule, fecha_referencia='2026-01-01'),
            'bloques': [
                {'id': 'b1', 'nombre': 'Uno', 'tickers': tickers[0], 'indice_proxy': 'QQQ',
                 'bandas': {'referencia_pct': refs[0]}},
                {'id': 'b2', 'nombre': 'Dos', 'tickers': tickers[1], 'indice_proxy': 'GLD',
                 'bandas': {'referencia_pct': refs[1]}},
                {'id': 'resto', 'nombre': 'Resto', 'tickers': tickers[2], 'indice_proxy': 'SPY',
                 'bandas': {'referencia_pct': refs[2]}}],
            'tope_posicion': cap or {'aviso_pct': 10.0, 'alerta_pct': 20.0, 'excepciones': ['LINK'],
                                     'nota': 'ancla'}}
    (tmp_path / 'blocks.json').write_text(json.dumps(data), encoding='utf-8')
    return blocks.load_blocks(str(tmp_path / 'blocks.json'))


def _compute(cfg, px, ops):
    port = engine.portfolio_series(DATES, px, ops)
    return exposure.compute(port['holdings_end'], stress.last_prices_from(px), px, port['start_idx'],
                            cfg, dates=DATES)


def _value_split(px, w):
    """Cantidades que dejan al ultimo cierre los pesos `w` ({ticker: fraccion}) sobre 10.000 USD."""
    return {t: 10_000 * f / px[t][-1] for t, f in w.items()}


def test_compute_weights_sum_to_100_and_match_the_requested_split(tmp_path):
    px = _px()
    cfg = _cfg(tmp_path)
    d = _compute(cfg, px, _ops(**_value_split(px, {'A': 0.40, 'B': 0.15, 'LINK-USD': 0.25, 'C': 0.20})))
    peso = {b['id']: b['peso_pct'] for b in d['bloques']}
    assert peso == {'b1': pytest.approx(40.0, abs=1e-6), 'b2': pytest.approx(40.0, abs=1e-6),
                    'resto': pytest.approx(20.0, abs=1e-6)}
    assert d['suma_pesos_pct'] == pytest.approx(100.0, abs=0.2)
    assert sum(p['peso_pct'] for p in d['posiciones']) == pytest.approx(100.0, abs=0.2)
    assert [b['estado'] for b in d['bloques']] == ['dentro'] * 3
    assert not [a for a in d['alertas'] if a['tipo'] == 'bloque']     # sin alertas de bloque


def test_block_alert_fires_with_test_data_and_is_a_template_code(tmp_path):
    px = _px()
    cfg = _cfg(tmp_path)                      # referencias 40/40/20; abajo mandamos pesos 60/25/15
    d = _compute(cfg, px, _ops(**_value_split(px, {'A': 0.60, 'B': 0.25, 'C': 0.15})))
    by = {b['id']: b for b in d['bloques']}
    assert by['b1']['estado'] == 'arriba' and by['b1']['desvio_pp'] == pytest.approx(20.0, abs=1e-3)
    assert by['b2']['estado'] == 'abajo' and by['b2']['desvio_pp'] == pytest.approx(-15.0, abs=1e-3)
    assert by['resto']['estado'] == 'dentro' and by['resto']['alerta'] is None   # 15 vs 20: banda 15..25
    al = [a for a in d['alertas'] if a['tipo'] == 'bloque']
    assert {(a['bloque'], a['direccion']) for a in al} == {('b1', 'arriba'), ('b2', 'abajo')}
    assert all(a['codigo'] == 'bloque_se_alejo_de_referencia' for a in al)     # codigo, no prosa
    assert 'mal' not in json.dumps(al)                                          # modo descriptivo


def test_changing_the_reference_changes_the_band_and_the_state(tmp_path):
    px = _px()
    ops = _ops(**_value_split(px, {'A': 0.50, 'B': 0.30, 'C': 0.20}))
    d1 = _compute(_cfg(tmp_path, refs=(50.0, 30.0, 20.0)), px, ops)
    assert [b['estado'] for b in d1['bloques']] == ['dentro'] * 3
    d2 = _compute(_cfg(tmp_path, refs=(30.0, 50.0, 20.0)), px, ops)
    by = {b['id']: b for b in d2['bloques']}
    assert by['b1']['banda']['min_pct'] == pytest.approx(25.0) and by['b1']['estado'] == 'arriba'
    assert by['b2']['banda']['max_pct'] == pytest.approx(55.0) and by['b2']['estado'] == 'abajo'


def test_position_cap_in_compute_paxg_exception_does_not_alert(tmp_path):
    px = _px()
    cfg = _cfg(tmp_path)
    d = _compute(cfg, px, _ops(**_value_split(px, {'LINK-USD': 0.55, 'A': 0.12, 'B': 0.25, 'C': 0.08})))
    pos = {p['ticker']: p for p in d['posiciones']}
    assert pos['LINK-USD']['peso_pct'] > 20 and pos['LINK-USD']['nivel'] == 'ok'
    assert pos['LINK-USD']['etiqueta'] == 'excepcion' and pos['LINK-USD']['motivo'] == 'ancla'
    assert pos['A']['nivel'] == 'aviso' and pos['B']['nivel'] == 'alerta'
    assert pos['C']['nivel'] == 'ok' and pos['C']['etiqueta'] is None
    cod = {(a['ticker'], a['codigo']) for a in d['alertas'] if a['tipo'] == 'posicion'}
    assert cod == {('A', 'posicion_sobre_aviso'), ('B', 'posicion_sobre_alerta')}   # LINK no dispara


# --- Aporte al riesgo -------------------------------------------------------------
def test_risk_contributions_sum_to_one_and_match_the_formula():
    rng = np.random.default_rng(7)
    ret = {t: list(rng.normal(0.0004, s, 200)) for t, s in (('X', 0.01), ('Y', 0.02), ('Z', 0.015))}
    w = {'X': 0.5, 'Y': 0.3, 'Z': 0.2}
    res = exposure.risk_contributions(ret, w)
    assert sum(res['aportes'].values()) == pytest.approx(1.0, abs=1e-9)
    rp = sum(w[t] * np.array(ret[t]) for t in w)
    y = w['Y'] * np.cov(ret['Y'], rp)[0, 1] / np.var(rp, ddof=1)
    assert res['aportes']['Y'] == pytest.approx(y)
    assert res['vol_anual'] == pytest.approx(np.std(rp, ddof=1) * np.sqrt(252))
    assert res['dias'] == 200


def test_risk_contributions_insufficient_or_flat_is_none_never_zero():
    short = exposure.risk_contributions({'X': [0.01] * 5}, {'X': 1.0})
    assert short['aportes'] == {'X': None} and short['vol_anual'] is None
    flat = exposure.risk_contributions({'X': [0.0] * 50, 'Y': [0.0] * 50}, {'X': 0.5, 'Y': 0.5})
    assert flat['aportes'] == {'X': None, 'Y': None}


def test_block_risk_contributions_sum_to_100_with_full_coverage(tmp_path):
    px = _px()
    d = _compute(_cfg(tmp_path), px, _ops(**_value_split(px, {'A': 0.40, 'B': 0.15, 'LINK-USD': 0.25, 'C': 0.20})))
    assert sum(b['aporte_riesgo_pct'] for b in d['bloques']) == pytest.approx(100.0, abs=0.01)
    r = d['riesgo']
    assert r['suma_aportes_pct'] == pytest.approx(100.0, abs=0.01) and r['cobertura_pct'] == pytest.approx(100.0)
    assert r['sin_dato'] == [] and r['metodo'] and 'volatilidad realizada' in r['nota']
    assert all(b['cobertura_riesgo_pct'] == pytest.approx(100.0) for b in d['bloques'])


def test_same_window_as_the_engine_volatility(tmp_path):
    px = _px()
    ops = _ops(**_value_split(px, {'A': 0.5, 'B': 0.3, 'C': 0.2}))
    ops[1]['fecha'] = DATES[10]               # B se abre despues: la ventana arranca igual en el dia 0
    port = engine.portfolio_series(DATES, px, ops)
    ret, missing = exposure.window_returns(px, port['start_idx'], ['A', 'B', 'C'])
    assert missing == [] and all(len(v) == len(port['returns']) - 1 for v in ret.values())
    d = _compute(_cfg(tmp_path), px, ops)
    assert d['riesgo']['ventana'] == {'desde': DATES[0], 'hasta': DATES[-1], 'dias': N - 1}


def test_ticker_without_history_has_null_contribution_and_coverage(tmp_path):
    new = [None] * 40 + _walk(99, start=50.0)[40:]          # cotiza recien en el dia 40 de la ventana
    px = _px(C=new)
    d = _compute(_cfg(tmp_path), px, _ops(**_value_split(_px(C=_walk(5)), {'A': 0.4, 'B': 0.15, 'LINK-USD': 0.25})) +
                 _ops(C=40.0))
    r = d['riesgo']
    assert r['sin_dato'] == ['C'] and r['cobertura_pct'] < 100.0
    by = {b['id']: b for b in d['bloques']}
    assert by['resto']['aporte_riesgo_pct'] is None                # nunca 0
    assert by['resto']['sin_dato'] == ['C'] and by['resto']['cobertura_riesgo_pct'] == 0.0
    # el resto se calcula con lo disponible y suma 100% de lo cubierto
    assert by['b1']['aporte_riesgo_pct'] + by['b2']['aporte_riesgo_pct'] == pytest.approx(100.0, abs=0.01)
    assert r['suma_aportes_pct'] == pytest.approx(100.0, abs=0.01)
    # el peso de C sigue contando en los pesos (suman 100%) aunque no tenga aporte
    assert d['suma_pesos_pct'] == pytest.approx(100.0, abs=0.2)


def test_partial_block_coverage_is_reported(tmp_path):
    new = [None] * 40 + _walk(99, start=50.0)[40:]
    px = _px(C=new, D=_walk(6))
    cfg = _cfg(tmp_path, tickers=[['A'], ['B', 'LINK'], ['C', 'D']])
    ops = _ops(A=10.0, B=10.0, **{'LINK-USD': 1.0}, C=10.0, D=10.0)
    d = _compute(cfg, px, ops)
    resto = next(b for b in d['bloques'] if b['id'] == 'resto')
    assert resto['sin_dato'] == ['C'] and 0 < resto['cobertura_riesgo_pct'] < 100
    assert resto['aporte_riesgo_pct'] is not None


# --- Endpoint /exposure -----------------------------------------------------------
@pytest.fixture
def client(monkeypatch, tmp_path):
    px = _px()
    ops = _ops(**_value_split(px, {'A': 0.60, 'B': 0.15, 'LINK-USD': 0.25}))
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, px, ops))
    monkeypatch.setattr(blocks, 'load_blocks', lambda path=None: cfg)
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    return app.test_client()


def test_exposure_endpoint_shape_and_no_nan(client):
    r = client.get('/api/lab/exposure')
    d = _json(r)
    assert r.status_code == 200 and d['ok'] and d['status'] == 'ok' and d['datos_al'] == DATES[-1]
    assert d['modo'] == 'descriptivo' and d['regla_banda']['nombre'] == '5/25'
    assert [b['id'] for b in d['bloques']] == ['b1', 'b2', 'resto']
    b = d['bloques'][0]
    assert {'id', 'nombre', 'tickers', 'peso_pct', 'referencia_pct', 'banda', 'estado', 'desvio_pp',
            'alerta', 'aporte_riesgo_pct', 'cobertura_riesgo_pct', 'sin_dato'} <= set(b)
    assert b['nombre'] == 'Uno' and set(b['banda']) == {'min_pct', 'max_pct', 'ancho_pp', 'manda'}
    assert d['suma_pesos_pct'] == pytest.approx(100.0, abs=0.2)
    assert {'ticker', 'bloque', 'peso_pct', 'nivel', 'etiqueta', 'motivo'} <= set(d['posiciones'][0])
    assert {'metodo', 'nota', 'ventana', 'cobertura_pct', 'suma_aportes_pct', 'sin_dato'} <= set(d['riesgo'])
    assert any(a['bloque'] == 'b1' and a['direccion'] == 'arriba' for a in d['alertas'] if a['tipo'] == 'bloque')


def test_exposure_endpoint_without_prices_and_with_bad_config(monkeypatch, client):
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    d = _json(client.get('/api/lab/exposure'))
    assert d['status'] == 'sin_precios' and not d['ok']

    def boom(path=None):
        raise blocks.BlocksError('roto')
    monkeypatch.setattr(blocks, 'load_blocks', boom)
    assert _json(client.get('/api/lab/exposure'))['status'] == 'config_invalida'


# --- Correlacion movil ------------------------------------------------------------
def _series(with_dollar):
    rng = np.random.default_rng(3)
    s = {'PORT': [None] + list(rng.normal(0, 0.01, 99)), 'BTC-USD': [None] + list(rng.normal(0, 0.03, 99)),
         'GLD': [None] + list(rng.normal(0, 0.008, 99)), 'QQQ': [None] + list(rng.normal(0, 0.01, 99))}
    s['QQQ'] = [None] + [0.9 * p + 0.1 * q for p, q in zip(s['PORT'][1:], s['QQQ'][1:])]
    if with_dollar:
        s['DX-Y.NYB'] = [None] + [-p for p in s['PORT'][1:]]        # perfectamente inversa
    return s


def test_rolling_vs_references_with_dollar_present():
    out = correlation.rolling_vs_references(_series(True), 60)
    assert [o['id'] for o in out] == ['BTC-USD', 'GLD', 'QQQ', 'DX-Y.NYB']
    dxy = out[-1]
    assert dxy['estado'] == 'ok' and dxy['aviso'] is None and dxy['ultimo'] == pytest.approx(-1.0)
    assert len(dxy['valores']) == 100 and dxy['valores'][:59] == [None] * 59   # hasta tener ventana completa
    qqq = out[2]
    assert qqq['ultimo'] > 0.9 and set(qqq['nombre']) == {'es', 'en'}


def test_rolling_vs_references_without_dollar_is_null_with_warning():
    out = correlation.rolling_vs_references(_series(False), 60)
    dxy = out[-1]
    assert dxy['id'] == 'DX-Y.NYB' and dxy['estado'] == 'sin_datos'
    assert dxy['ultimo'] is None and dxy['valores'] is None and dxy['aviso'] == 'sin_datos_en_cache'
    assert all(o['estado'] == 'ok' for o in out[:3])


def test_rolling_window_longer_than_history_is_null_not_zero():
    out = correlation.rolling_vs_references(_series(True), 250)
    assert all(o['ultimo'] is None and o['aviso'] == 'ventana_insuficiente' for o in out)


def test_correlation_endpoint_includes_dollar_and_keeps_existing_payload(monkeypatch):
    px = _px(**{'DX-Y.NYB': _walk(21, vol=0.004)})
    ops = _ops(A=10.0, B=10.0)
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, px, ops))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    d = _json(app.test_client().get('/api/lab/correlation?window=30'))
    assert {'names', 'matrix', 'window', 'dates', 'rolling', 'missing', 'movil'} <= set(d)
    assert d['window'] == 30 and 'BTC-USD' in d['rolling'] and 'DX-Y.NYB' in d['names']
    mv = d['movil']
    assert mv['ventana'] == 30 and [r['id'] for r in mv['referencias']] == ['BTC-USD', 'GLD', 'QQQ', 'DX-Y.NYB']
    assert all(r['estado'] == 'ok' and len(r['valores']) == len(d['dates']) for r in mv['referencias'])
    # sin dolar en cache: null con aviso, sin romper el resto
    px.pop('DX-Y.NYB')
    d = _json(app.test_client().get('/api/lab/correlation?window=30'))
    dxy = d['movil']['referencias'][-1]
    assert dxy['estado'] == 'sin_datos' and dxy['ultimo'] is None and dxy['valores'] is None
    assert 'DX-Y.NYB' in d['missing']
    assert app.test_client().get('/api/lab/correlation?window=2').status_code == 400


def test_dollar_index_is_in_the_price_adapter():
    assert 'DX-Y.NYB' in prices.INDEX_TICKERS and 'DX-Y.NYB' in prices.tickers_for([])
    assert prices.price_symbol('DX-Y.NYB') == 'DX-Y.NYB'


# --- Ventas recortadas ------------------------------------------------------------
CLIPPED = [{'fecha': '2026-06-17', 'ticker': 'AAA', 'pedido': 7.3913, 'tenencia': 7.2975},    # 1,285%
           {'fecha': '2025-09-23', 'ticker': 'BBB', 'pedido': 3.02135, 'tenencia': 3.0},          # 0,712%
           {'fecha': '2025-12-04', 'ticker': 'CCC', 'pedido': 80.27, 'tenencia': 80.0},            # 0,3375%
           {'fecha': '2026-01-21', 'ticker': 'DDD', 'pedido': 20.06, 'tenencia': 20.0},           # 0,3%
           {'fecha': '2026-03-01', 'ticker': 'ZZZ', 'pedido': 1.0, 'tenencia': 0.0}]               # tenencia 0


def test_trimmed_sales_threshold_split_and_order():
    r = ledger.trimmed_sales(CLIPPED, 0.5)
    assert r['umbral_pct'] == 0.5 and r['bajo_umbral'] == 2          # CCC y DDD
    assert [(x['ticker'], x['fecha']) for x in r['lista']] == [
        ('BBB', '2025-09-23'), ('ZZZ', '2026-03-01'), ('AAA', '2026-06-17')]
    bbb, zzz, aaa = r['lista']
    assert bbb['exceso_unidades'] == pytest.approx(0.02135) and bbb['exceso_pct'] == pytest.approx(0.7117, abs=1e-3)
    assert aaa['exceso_pct'] == pytest.approx(1.2855, abs=1e-3)
    assert zzz['exceso_pct'] is None and zzz['exceso_unidades'] == 1.0      # no medible: nunca 0 ni NaN


def test_trimmed_sales_threshold_is_configurable():
    assert ledger.trimmed_sales(CLIPPED, 0.25)['bajo_umbral'] == 0
    r = ledger.trimmed_sales(CLIPPED, 1.0)          # solo AAA (1,29%) y la de tenencia 0 superan 1%
    assert [x['ticker'] for x in r['lista']] == ['ZZZ', 'AAA'] and r['bajo_umbral'] == 3


def test_trimmed_sales_exactly_at_threshold_is_below():
    r = ledger.trimmed_sales([{'fecha': '2026-01-02', 'ticker': 'X', 'pedido': 3.0, 'tenencia': 2.0}], 50.0)
    assert r['lista'] == [] and r['bajo_umbral'] == 1      # 50% exacto: no lo supera


def test_trim_threshold_reads_config_with_default():
    assert ledger.trim_threshold_pct({'umbral_recorte_pct': 0.75}) == 0.75
    assert ledger.trim_threshold_pct({}) == 0.5
    assert ledger.trim_threshold_pct({'umbral_recorte_pct': 'x'}) == 0.5
    assert ledger.trim_threshold_pct({'umbral_recorte_pct': -1}) == 0.5
    with open(ledger.CONFIG_PATH, encoding='utf-8') as f:
        assert json.load(f)['umbral_recorte_pct'] == 0.5        # guardado en config/lab/config.json


def test_reconcile_block_uses_engine_warnings(monkeypatch):
    dates = ['2026-01-02', '2026-01-05', '2026-01-06']
    px = {'SPY': [100.0, 100.0, 100.0], 'AAA': [10.0, 10.0, 10.0]}
    ops = [{'fecha': '2026-01-02', 'ticker': 'AAA', 'cantidad': 10.0, 'tipo': 'compra', 'monto_usd': 100.0},
           {'fecha': '2026-01-03', 'ticker': 'AAA', 'cantidad': -10.1, 'tipo': 'venta', 'monto_usd': 101.0}]
    monkeypatch.setattr(api, '_load_inputs', lambda: (dates, px, ops))
    monkeypatch.setattr(ledger, 'trim_threshold_pct', lambda config=None: 0.5)
    b = api._trimmed_sales_block()
    assert b['status'] == 'ok' and b['bajo_umbral'] == 0 and b['umbral_pct'] == 0.5
    assert b['lista'] == [{'ticker': 'AAA', 'fecha': '2026-01-03', 'exceso_unidades': pytest.approx(0.1),
                           'exceso_pct': pytest.approx(1.0, abs=1e-3), 'pedido': 10.1, 'tenencia': 10.0}]
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    b = api._trimmed_sales_block()
    assert b['status'] == 'sin_precios' and b['lista'] is None and b['bajo_umbral'] is None


# --- Demo ledger (fictional) + fixtures (skipped if missing) -----------------------------------
MAIN_FIXTURE = os.path.join(FIXTURES, 'prices_2026-11-13.csv')
DXY_FIXTURE = os.path.join(FIXTURES, 'dxy_2026-11-13.csv')


def _ledger_available():
    """The demo ledger (sample_data/ledger.csv + investments_ledger.csv, fictional) ships with the repo."""
    return os.path.exists(ledger.LEDGER_PATH)


real = pytest.mark.skipif(not (os.path.exists(MAIN_FIXTURE) and os.path.exists(DXY_FIXTURE) and _ledger_available()),
                          reason='demo ledger or frozen fixtures missing')


@pytest.fixture(scope='module')
def real_inputs():
    ops = prices.to_price_ops(ledger.load_ledger()['ops'])
    df = pd.read_csv(MAIN_FIXTURE, index_col=0, parse_dates=True)
    dates, px = prices.align(df)
    return dates, px, ops, df


@real
def test_real_block_weights_at_2026_11_13_match_references_within_half_point(real_inputs):
    dates, px, ops, _ = real_inputs
    cfg = blocks.load_blocks()
    port = engine.portfolio_series(dates, px, ops)
    d = exposure.compute(port['holdings_end'], stress.last_prices_from(px), px, port['start_idx'], cfg, dates=dates)
    assert dates[-1] == '2026-11-13' and d['fecha_referencia'] == '2026-11-13'
    for b in d['bloques']:
        assert abs(b['peso_pct'] - b['referencia_pct']) <= 0.5, (b['id'], b['peso_pct'], b['referencia_pct'])
        assert b['estado'] == 'dentro'
    assert d['suma_pesos_pct'] == pytest.approx(100.0, abs=0.2)
    assert d['alertas'] == []
    assert d['riesgo']['sin_dato'] == [] and d['riesgo']['suma_aportes_pct'] == pytest.approx(100.0, abs=0.01)
    # the demo has no position in the catch-all block: its contribution is null, never 0
    assert sum(b['aporte_riesgo_pct'] for b in d['bloques'] if b['aporte_riesgo_pct'] is not None) == pytest.approx(100.0, abs=0.01)
    vti = next(p for p in d['posiciones'] if p['ticker'] == 'VTI')
    assert vti['peso_pct'] > 10 and vti['nivel'] == 'ok' and vti['etiqueta'] == 'excepcion'
    assert not [p for p in d['posiciones'] if p['nivel'] in ('aviso', 'alerta')]
    assert d['posiciones'][0]['ticker'] == 'NU'      # sorted by value


@real
def test_real_golden_numbers_unchanged_and_stress_figures_preserved(real_inputs):
    dates, px, ops, df = real_inputs
    res = engine.analyze(dates, px, ops, [b for b in prices.INDEX_TICKERS if b != 'DX-Y.NYB'])
    st = res['series']['PORT']['stats']
    # demo edition: figures regenerated from the fictional ledger (ledger.csv + investments_ledger.csv)
    # and the synthetic fixture prices
    assert st['cum'] * 100 == pytest.approx(37.08, abs=0.1) and st['vol'] * 100 == pytest.approx(19.75, abs=0.1)
    assert res['port']['v_end'] == pytest.approx(41739, rel=0.005)
    # las cifras del estres no cambian con el esquema nuevo de blocks.json ni con los proxies aparte
    sc = os.path.join(FIXTURES, 'stress_prices.csv')
    if not os.path.exists(sc):
        pytest.skip('falta stress_prices.csv')
    cfg = blocks.load_blocks()
    port = engine.portfolio_series(dates, px, ops)
    pos, _, _ = stress.current_weights(port['holdings_end'], stress.last_prices_from(px))
    out = stress.run_all(stress.load_scenarios(), pos, pd.read_csv(sc, index_col=0, parse_dates=True), cfg, dates[-1])
    imp = {e['id']: e['impacto_pct'] for e in out}
    assert imp['tasas_2022'] == pytest.approx(-22.35, abs=0.01) and imp['metales_2026'] == pytest.approx(40.27, abs=0.01)
    assert imp['dia_2026_11_05'] == pytest.approx(-4.08, abs=0.01) and imp['covid_2020'] == pytest.approx(-33.85, abs=0.01)


@real
def test_real_rolling_correlation_with_dxy_fixture(real_inputs, monkeypatch):
    dates, px, ops, df = real_inputs
    dxy = pd.read_csv(DXY_FIXTURE, index_col=0, parse_dates=True)
    dates2, px2 = prices.align(df.join(dxy, how='outer'))
    assert dates2 == dates                              # el dolar no altera el calendario (SPY)
    monkeypatch.setattr(api, '_load_inputs', lambda: (dates2, px2, ops))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    d = _json(app.test_client().get('/api/lab/correlation?window=60'))
    refs = {r['id']: r for r in d['movil']['referencias']}
    assert set(refs) == {'BTC-USD', 'GLD', 'QQQ', 'DX-Y.NYB'} and d['movil']['ventana'] == 60
    assert all(r['estado'] == 'ok' and -1 <= r['ultimo'] <= 1 for r in refs.values())
    # sin el fixture del dolar (fixture dorado solo): null con aviso
    monkeypatch.setattr(api, '_load_inputs', lambda: (dates, px, ops))
    d = _json(app.test_client().get('/api/lab/correlation?window=60'))
    dxy_ref = next(r for r in d['movil']['referencias'] if r['id'] == 'DX-Y.NYB')
    assert dxy_ref['ultimo'] is None and dxy_ref['aviso'] == 'sin_datos_en_cache'


@real
def test_real_trimmed_sales_over_threshold(real_inputs):
    dates, px, ops, _ = real_inputs
    port = engine.portfolio_series(dates, px, ops)
    # The fictional demo ledger never sells more than it holds: no trimmed sales.
    clipped = next((w['detalle'] for w in port['warnings'] if w['tipo'] == 'ventas_recortadas'), [])
    r = ledger.trimmed_sales(clipped, ledger.trim_threshold_pct())
    assert clipped == [] and r['lista'] == [] and r['bajo_umbral'] == 0
