"""Fase 5: atribucion por posicion (Dietz modificado) y por operacion (lotes FIFO).
Fixtures sinteticos, sin red. Un solo test usa el libro real y los precios congelados."""
import json
import os

import pandas as pd
import pytest
from flask import Flask

from ._helpers import FIXTURES, load_lab

M = load_lab()
api, engine, attribution = M['api'], M['engine'], M['attribution']
ledger, prices = M['ledger'], M['prices']

# 7 dias habiles; 2 al 10 de enero = 8 dias de calendario
DATES = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09', '2025-01-10']
PX = {'SPY': [100] * 7,
      'A': [10, 11, 12, 13, 15, 9, 8],
      'B': [100, 100, 110, 120, 120, 130, 140]}


def op(fecha, t, q, tipo, monto=None, nota=None):
    return {'fecha': fecha, 'ticker': t, 'cantidad': q, 'tipo': tipo, 'monto_usd': monto, 'nota': nota}


# A: compra 10 (100) el dia 0, compra 10 (120) el 6/1, venta 15 (225) el 8/1.
OPS_A = [op('2025-01-02', 'A', 10, 'compra', 100), op('2025-01-06', 'A', 10, 'compra', 120),
         op('2025-01-08', 'A', -15, 'venta', 225)]
OPS_AB = OPS_A + [op('2025-01-02', 'B', 1, 'apertura'), op('2025-01-07', 'B', 2, 'compra', 230)]


def run(ops, desde=None, hasta=None, px=PX, dates=DATES):
    port = engine.portfolio_series(dates, px, ops)
    return attribution.attribute(port, desde, hasta), port


def pos(res, t):
    return next(p for p in res['posiciones'] if p['ticker'] == t)


def ops_of(res, t):
    return [o for o in res['operaciones'] if o['ticker'] == t]


# (a) a mano ------------------------------------------------------------------------------
def test_buy_partial_sell_by_hand_full_period():
    res, _ = run(OPS_A)
    p = pos(res, 'A')
    # V0 = 10 x 10; F = 120 - 225; Vfin = 5 x 8
    assert (p['valor_inicial'], p['flujo_neto'], p['valor_final']) == (100, -105, 40)
    assert p['resultado_usd'] == pytest.approx(45)
    rows = ops_of(res, 'A')
    by = {(r['fecha'], r['estado']): r for r in rows}
    # FIFO: la venta de 15 consume 10 del lote 1 (costo 10) y 5 del lote 2 (costo 12), a 15
    assert by[('2025-01-02', 'realizado')]['resultado_usd'] == pytest.approx(50)
    assert by[('2025-01-02', 'realizado')]['cantidad'] == 10
    assert by[('2025-01-06', 'realizado')]['resultado_usd'] == pytest.approx(15)
    held = by[('2025-01-06', 'no_realizado')]
    assert held['cantidad'] == 5 and held['precio_referencia'] == 8
    assert held['resultado_usd'] == pytest.approx(-20)        # 5 x (8 - 12)
    assert held['costo_unitario_usd'] == 12 and held['costo_lote_usd'] == 60
    assert held['dias_en_cartera'] == 4 and by[('2025-01-02', 'realizado')]['dias_en_cartera'] == 6
    assert sum(r['resultado_usd'] for r in rows) == pytest.approx(45)


def test_subperiod_values_existing_lots_at_close_of_from():
    res, _ = run(OPS_A, '2025-01-07', '2025-01-10')
    p = pos(res, 'A')
    assert (p['valor_inicial'], p['flujo_neto'], p['valor_final']) == (260, -225, 40)
    assert p['resultado_usd'] == pytest.approx(5)
    rows = ops_of(res, 'A')
    assert all(r['valuado_al_inicio'] and r['costo_unitario_usd'] == 13 for r in rows)
    assert sorted(r['resultado_usd'] for r in rows) == pytest.approx([-25, 10, 20])
    assert any(r['precio_ejecucion'] == 10 for r in rows)    # el costo original sigue visible


# (b) y (c) reconciliacion ------------------------------------------------------------------
@pytest.mark.parametrize('rango', [(None, None), ('2025-01-03', None), ('2025-01-06', '2025-01-09'),
                                   (None, '2025-01-08'), ('2025-01-07', '2025-01-10')])
def test_sum_positions_and_operations_equal_portfolio_result(rango):
    res, port = run(OPS_AB, *rango)
    r = res['reconciliacion']
    assert r['ok'] and r['diferencia'] <= 0.5 and r['diferencias_por_ticker'] == []
    kf = DATES.index(res['desde']) - port['start_idx']
    kt = DATES.index(res['hasta']) - port['start_idx']
    expected = port['values'][kt] - port['values'][kf] - sum(port['flows'][kf + 1:kt + 1])
    assert r['resultado_usd_cartera'] == pytest.approx(expected, abs=0.01)
    assert sum(p['resultado_usd'] for p in res['posiciones']) == pytest.approx(expected, abs=0.02)
    assert sum(o['resultado_usd'] for o in res['operaciones']) == pytest.approx(expected, abs=0.05)


def test_full_period_matches_engine_cash_summary():
    res, port = run(OPS_AB)
    s = res['resumen']
    assert s['valor_inicial'] == pytest.approx(port['v0']) and s['valor_final'] == pytest.approx(port['v_end'])
    assert s['aportes_netos'] == pytest.approx(port['net_flows'])
    assert s['resultado_usd'] == pytest.approx(port['v_end'] - port['v0'] - port['net_flows'], abs=0.01)


def test_pct_of_total_has_sign_and_is_null_when_total_is_zero():
    res, _ = run(OPS_AB)
    tot = res['resumen']['resultado_usd']
    assert sum(p['pct_del_resultado'] for p in res['posiciones']) == pytest.approx(100, abs=0.1)
    assert pos(res, 'A')['pct_del_resultado'] == pytest.approx(pos(res, 'A')['resultado_usd'] / tot * 100, abs=0.01)
    flat = {'SPY': [100] * 7, 'A': [10] * 7}
    res0, _ = run([op('2025-01-02', 'A', 10, 'compra', 100)], px=flat)
    assert res0['resumen']['resultado_usd'] == 0
    assert all(p['pct_del_resultado'] is None for p in res0['posiciones'])


# (d) dividendo en unidades ----------------------------------------------------------------
def test_unit_dividend_is_a_zero_cost_lot():
    ops = [op('2025-01-02', 'A', 10, 'compra', 100), op('2025-01-06', 'A', 1, 'dividendo_unidades', 0.0)]
    res, port = run(ops)
    div = next(o for o in res['operaciones'] if o['tipo'] == 'dividendo_unidades')
    assert div['costo_unitario_usd'] == 0 and div['precio_ejecucion'] == 0 and div['costo_lote_usd'] == 0
    assert div['resultado_usd'] == pytest.approx(8)            # 1 x cierre final (8), costo 0
    assert pos(res, 'A')['resultado_usd'] == pytest.approx(11 * 8 - 100)
    assert pos(res, 'A')['flujo_neto'] == 0
    assert res['reconciliacion']['ok']


def test_unit_dividend_sold_by_fifo_after_older_lot():
    ops = [op('2025-01-02', 'A', 10, 'compra', 100), op('2025-01-06', 'A', 1, 'dividendo_unidades', 0.0),
           op('2025-01-08', 'A', -10, 'venta', 150)]
    res, _ = run(ops)
    rows = ops_of(res, 'A')
    # la venta consume el lote viejo; la unidad regalada sigue en cartera
    assert [r['estado'] for r in rows if r['tipo'] == 'dividendo_unidades'] == ['no_realizado']
    assert res['reconciliacion']['ok']


# (e) canje --------------------------------------------------------------------------------
def test_swap_two_legs():
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-02', 'B', 1, 'apertura'),
           op('2025-01-06', 'B', 1, 'compra', 100, 'canje contra A'),
           op('2025-01-06', 'A', -10, 'canje', 100, 'pata de venta del canje por B')]
    res, port = run(ops)
    assert port['flows'][2] == 0
    sale = next(o for o in res['operaciones'] if o['ticker'] == 'A' and o['estado'] == 'realizado')
    assert sale['precio_referencia'] == 10 and sale['costo_unitario_usd'] == 10 and sale['resultado_usd'] == 0
    buy = next(o for o in res['operaciones'] if o['ticker'] == 'B' and o['fecha'] == '2025-01-06')
    assert buy['tipo'] == 'compra' and buy['precio_ejecucion'] == 100 and 'canje' in buy['nota']
    assert buy['resultado_usd'] == pytest.approx(140 - 100)
    assert res['reconciliacion']['ok']
    assert pos(res, 'A')['resultado_usd'] == pytest.approx(0 - 100 + 100)   # V0 100, F -100, Vfin 0


# (f) venta recortada ----------------------------------------------------------------------
def test_clipped_sale_uses_engine_clipped_amount():
    ops = [op('2025-01-02', 'A', 10, 'compra', 100), op('2025-01-08', 'A', -12, 'venta', 180)]
    res, port = run(ops)
    assert any(w['tipo'] == 'ventas_recortadas' for w in res['warnings'])
    sold = next(o for o in res['operaciones'] if o['estado'] == 'realizado')
    assert sold['cantidad'] == 10 and sold['precio_referencia'] == 15     # 180 / 12
    assert sold['resultado_usd'] == pytest.approx(50)
    assert pos(res, 'A')['flujo_neto'] == pytest.approx(-150)             # 180 x 10/12
    assert res['reconciliacion']['ok']


# (g) validacion HTTP ----------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, PX, OPS_AB))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    return app.test_client()


def _strict(resp):
    return json.loads(resp.get_data(as_text=True), parse_constant=lambda c: pytest.fail(f'{c} en JSON'))


@pytest.mark.parametrize('qs', ['from=2025-13-40', 'to=hoy', 'from=2025-01-09&to=2025-01-03',
                                'from=2026-01-01', 'to=2024-01-01', 'from=2025-01-10&to=2025-01-10'])
def test_invalid_ranges_return_400_with_message(client, qs):
    r = client.get(f'/api/lab/attribution?{qs}')
    d = _strict(r)
    assert r.status_code == 400 and d['ok'] is False and len(d['error']) > 10


def test_default_range_is_full_period_and_clips_to_calendar(client):
    full = _strict(client.get('/api/lab/attribution'))
    assert full['ok'] and full['status'] == 'ok' and full['desde'] == '2025-01-02' and full['hasta'] == '2025-01-10'
    clipped = _strict(client.get('/api/lab/attribution?from=2024-01-01&to=2030-01-01'))
    assert (clipped['desde'], clipped['hasta']) == (full['desde'], full['hasta'])
    assert clipped['resumen'] == full['resumen']
    inner = _strict(client.get('/api/lab/attribution?from=2025-01-04&to=2025-01-09'))
    assert (inner['desde'], inner['hasta']) == ('2025-01-06', '2025-01-09')
    assert full['metodo'] == 'Dietz modificado' and 'Dietz' in full['nota'] and full['reconciliacion']['ok']


def test_no_prices_status(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    d = app.test_client().get('/api/lab/attribution').get_json()
    assert d['ok'] is False and d['status'] == 'sin_precios'


def test_missing_values_are_null_not_zero_and_no_nan():
    px = {'SPY': [100] * 7}                          # A sin precios: el motor usa el implicito de la compra
    res, _ = run([op('2025-01-02', 'A', 10, 'compra', 100), op('2025-01-03', 'Z', 1, 'apertura')], px=px)
    text = json.dumps(res, allow_nan=False)
    assert 'NaN' not in text
    z = next(o for o in res['operaciones'] if o['ticker'] == 'Z')
    assert z['precio_ejecucion'] is None and z['precio_referencia'] is None
    assert any(w['tipo'] == 'sin_precio_en_rango' and 'Z' in w['tickers'] for w in res['warnings'])


# (h) Dietz modificado ---------------------------------------------------------------------
def test_dietz_contributions_sum_to_portfolio_dietz_by_hand():
    res, _ = run(OPS_A)
    # span 8 dias; flujos: +120 el 6/1 (w = 4/8), -225 el 8/1 (w = 2/8); V0 = 100; resultado 45
    base = 100 + 120 * 0.5 - 225 * 0.25
    assert res['resumen']['base_dietz'] == pytest.approx(base)
    assert res['resumen']['rentabilidad_dietz_pct'] == pytest.approx(45 / base * 100, abs=1e-3)
    assert pos(res, 'A')['aporte_pp'] == pytest.approx(45 / base * 100, abs=1e-3)


def test_dietz_sum_of_pp_equals_portfolio_return_two_tickers():
    res, _ = run(OPS_AB, '2025-01-03', '2025-01-10')
    total_pp = sum(p['aporte_pp'] for p in res['posiciones'])
    assert total_pp == pytest.approx(res['resumen']['rentabilidad_dietz_pct'], abs=2e-3)
    assert res['resumen']['rentabilidad_dietz_pct'] == pytest.approx(
        res['resumen']['resultado_usd'] / res['resumen']['base_dietz'] * 100, abs=1e-3)


# Libro real + precios congelados ---------------------------------------------------------
FIXTURE = os.path.join(FIXTURES, 'prices_2026-11-13.csv')


def _ledger_available():
    """The demo ledger (sample_data/ledger.csv + investments_ledger.csv, fictional) ships with the repo."""
    return os.path.exists(ledger.LEDGER_PATH)


@pytest.mark.skipif(not (os.path.exists(FIXTURE) and _ledger_available()),
                    reason='faltan el libro real o el fixture de precios congelados')
def test_real_ledger_full_period_reconciles(monkeypatch):
    """Expected values regenerated from the FICTIONAL demo ledger (sample_data/ledger.csv +
    investments_ledger.csv) and synthetic fixture prices."""
    ops = prices.to_price_ops(ledger.load_ledger()['ops'])
    dates, px = prices.align(pd.read_csv(FIXTURE, index_col=0, parse_dates=True))
    monkeypatch.setattr(api, '_load_inputs', lambda: (dates, px, ops))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    d = _strict(app.test_client().get('/api/lab/attribution'))
    assert d['ok'] and d['hasta'] == '2026-11-13'
    assert d['resumen']['valor_final'] == pytest.approx(41738.50, abs=1)
    assert d['resumen']['resultado_usd'] == pytest.approx(10645.52, abs=1)
    assert d['reconciliacion']['ok'], d['reconciliacion']['diferencias_por_ticker']
    assert d['reconciliacion']['diferencia'] <= 0.5


# Faltantes: null, nunca 0 (regla de oro 4) -------------------------------------------------
def _port_with_gaps(drop):
    """Cartera sintetica: C con precios; A, B, Z son aperturas. `drop` = {ticker: dias a quitar de
    ticker_prices ('all' = todos)}. Los valores del motor se recalculan como los calcula el motor real
    (sin precio no suma valor)."""
    ops = [op('2025-01-02', 'C', 5, 'compra', 250), op('2025-01-02', 'A', 1, 'apertura'),
           op('2025-01-02', 'B', 2, 'apertura'), op('2025-01-02', 'Z', 1, 'apertura')]
    px = dict(PX, C=[50, 51, 52, 53, 54, 55, 60])
    port = engine.portfolio_series(DATES, px, ops)
    hold = {'C': 5, 'A': 1, 'B': 2, 'Z': 1}
    for t, days in drop.items():
        tp = port['ticker_prices'].setdefault(t, {})
        for k in (list(tp) if days == 'all' else days):
            tp.pop(k, None)
    port['values'] = [sum(h * port['ticker_prices'].get(t, {}).get(k, 0.0) for t, h in hold.items())
                      for k in range(len(DATES))]
    return port


def test_missing_prices_are_null_and_reconciliation_excludes_them():
    port = _port_with_gaps({'A': [0], 'B': [6], 'Z': 'all'})
    res = attribution.attribute(port)
    json.dumps(res, allow_nan=False)                       # sin NaN/Infinity
    a, b, z, c = (pos(res, t) for t in ('A', 'B', 'Z', 'C'))
    assert a['valor_inicial'] is None and a['valor_final'] is not None
    assert b['valor_inicial'] is not None and b['valor_final'] is None
    assert z['valor_inicial'] is None and z['valor_final'] is None
    for p in (a, b, z):
        assert p['resultado_usd'] is None and p['pct_del_resultado'] is None and p['aporte_pp'] is None
    # el resto sigue correcto: C 5 x (60 - 50) = 50
    assert c['resultado_usd'] == pytest.approx(50.0) and c['valor_final'] == pytest.approx(300.0)
    # operaciones: costo / resultado dependientes del precio faltante son null
    oa, ob, oz = ops_of(res, 'A')[0], ops_of(res, 'B')[0], ops_of(res, 'Z')[0]
    assert oa['costo_lote_usd'] is None and oa['resultado_usd'] is None
    assert ob['costo_lote_usd'] is not None and ob['precio_referencia'] is None and ob['resultado_usd'] is None
    assert oz['costo_lote_usd'] is None and oz['precio_referencia'] is None and oz['resultado_usd'] is None
    assert ops_of(res, 'C')[0]['resultado_usd'] == pytest.approx(50.0)
    # warning con los tickers
    w = next(w for w in res['warnings'] if w['tipo'] == 'sin_precio_en_rango')
    assert {'A', 'B', 'Z'} <= set(w['tickers']) and 'C' not in w['tickers']
    # reconciliacion: solo lo valuable, con excluidos y ok False
    rec = res['reconciliacion']
    assert rec['excluidos_sin_precio'] == ['A', 'B', 'Z']
    assert rec['ok'] is False
    assert rec['suma_posiciones'] == pytest.approx(50.0) and rec['suma_operaciones'] == pytest.approx(50.0)
    assert rec['resultado_usd_valuable'] == pytest.approx(50.0) and rec['diferencia'] < 0.01
