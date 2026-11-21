"""Fase 2: motor de calculo con casos sinteticos (convenciones de la seccion 4 del plan)."""
import math

import pytest

from ._helpers import load_lab

E = load_lab()['engine']

# Jueves 2, viernes 3, lunes 6, martes 7, miercoles 8
DATES = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08']
PX = {'A': [10, 11, 12, 12, 14], 'B': [100, 100, 100, 110, 110]}


def op(fecha, ticker, qty, tipo='compra', monto=None):
    return {'fecha': fecha, 'ticker': ticker, 'cantidad': qty, 'tipo': tipo, 'monto_usd': monto}


def test_flow_and_time_weighted_return():
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-06', 'A', 5, 'compra', 60)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['v0'] == 100 and p['values'][:3] == [100, 110, 180]
    assert p['flows'] == [0, 0, 60, 0, 0]
    assert p['returns'][2] == pytest.approx((180 - 60) / 110 - 1)  # = 12/11 - 1
    assert p['index'][2] == pytest.approx(100 * 11 / 10 * 12 / 11)
    assert p['net_flows'] == 60 and p['v_end'] == 15 * 14


def test_sale_is_negative_flow_at_execution_amount():
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-07', 'A', -4, 'venta', 50)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['flows'][3] == -50
    assert p['returns'][3] == pytest.approx((6 * 12 + 50) / 120 - 1)  # ejecuto a 12,5 vs cierre 12


def test_non_business_day_goes_to_next_business_day():
    # sabado 4/1 -> lunes 6/1
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-04', 'A', 1, 'compra', 12)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['flows'] == [0, 0, 12, 0, 0]
    assert p['values'][1] == 110 and p['values'][2] == 11 * 12
    assert E.day_index(DATES, '2025-01-04') == 2
    assert E.day_index(DATES, '2025-01-09') is None


def test_missing_amount_uses_quantity_times_close():
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-06', 'A', 2, 'compra', None)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['flows'][2] == pytest.approx(24)


def test_sale_larger_than_holding_is_clipped():
    ops = [op('2025-01-02', 'A', 1, 'apertura'), op('2025-01-06', 'A', -1.0000001, 'venta', 12)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['holdings_end'] == {}
    assert p['values'][2:] == [0, 0, 0]
    assert p['flows'][2] == pytest.approx(-12, rel=1e-6)
    assert p['warnings'][0]['tipo'] == 'ventas_recortadas'


def test_dividend_in_units_enters_holding_with_zero_flow():
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-06', 'A', 1, 'dividendo_unidades', 0.0)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['flows'] == [0, 0, 0, 0, 0]
    assert p['holdings_end'] == {'A': 11}
    assert p['returns'][2] == pytest.approx(132 / 110 - 1)  # la unidad regalada es rendimiento


def test_cash_dividend_is_ignored():
    ops = [op('2025-01-02', 'A', 10, 'apertura'),
           {'fecha': '2025-01-06', 'ticker': 'A', 'cantidad': None, 'tipo': 'dividendo_efectivo', 'monto_usd': 5}]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['flows'] == [0] * 5


def test_swap_legs_net_to_zero_flow():
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-02', 'B', 1, 'apertura'),
           op('2025-01-06', 'B', 1, 'compra', 100), op('2025-01-06', 'A', -10, 'canje', 100)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['flows'][2] == 0 and p['holdings_end'] == {'B': 2}


def test_series_starts_at_first_operation_and_day0_flows_are_not_contributions():
    ops = [op('2025-01-03', 'A', 10, 'compra', 110)]
    p = E.portfolio_series(DATES, PX, ops)
    assert p['dates'][0] == '2025-01-03' and p['v0'] == 110 and p['net_flows'] == 0


def test_missing_price_is_carried_not_zero():
    px = {'A': [None, 11, None, 12, 14]}
    ops = [op('2025-01-03', 'A', 10, 'compra', 110)]
    p = E.portfolio_series(DATES, px, ops)
    assert p['values'] == [110, 110, 120, 140]


def test_same_contributions_in_index():
    ops = [op('2025-01-02', 'A', 10, 'apertura'), op('2025-01-06', 'A', 5, 'compra', 60)]
    p = E.portfolio_series(DATES, PX, ops)
    # u0 = 100/100 en B; el 6/1 B cotiza 100: u += 60/100; final = 1,6 x 110
    assert E.same_contributions(p['v0'], p['flows'], PX['B'], p['start_idx']) == pytest.approx(1.6 * 110)
    # en A: u0 = 10, u += 60/12 = 5 -> 15 x 14
    assert E.same_contributions(p['v0'], p['flows'], PX['A'], p['start_idx']) == pytest.approx(210)


def test_stats_and_drawdown():
    idx = [100, 110, 99, 120]
    rets = [None, 0.1, -0.1, 120 / 99 - 1]
    assert E.drawdown(idx) == pytest.approx([0, 0, -0.1, 0])
    st = E.stats(idx, rets)
    assert st['cum'] == pytest.approx(0.2) and st['max_dd'] == pytest.approx(-0.1)
    assert st['ann'] == pytest.approx(1.2 ** (252 / 3) - 1)
    import statistics
    assert st['vol'] == pytest.approx(statistics.stdev(rets[1:]) * math.sqrt(252))


def test_stats_with_too_little_data_are_none():
    st = E.stats([100], [None])
    assert st['cum'] is None and st['vol'] is None


def test_period_returns_chain_from_previous_period():
    dates = ['2025-01-30', '2025-01-31', '2025-02-03', '2025-02-28']
    idx = [100, 110, 121, 132]
    m = E.period_returns(dates, idx, 7)
    assert [x['periodo'] for x in m] == ['2025-01', '2025-02']
    assert m[0]['retorno'] == pytest.approx(0.10)
    assert m[1]['retorno'] == pytest.approx(132 / 110 - 1)


def test_correlation_perfect_and_none_when_constant():
    a = [None, 0.01, -0.02, 0.03, 0.01]
    names, m = E.correlation_matrix({'X': a, 'Y': [None] + [2 * v for v in a[1:]], 'Z': [None, 0, 0, 0, 0]})
    assert m[0][1] == pytest.approx(1.0) and m[0][0] == 1.0
    assert m[0][2] is None
    roll = E.rolling_correlation(a, a, 3)
    assert roll[:2] == [None, None] and roll[-1] == pytest.approx(1.0)


def test_analyze_outputs_have_no_nan():
    ops = [op('2025-01-02', 'A', 10, 'apertura')]
    res = E.analyze(DATES, PX, ops, ['B', 'NOPE'])
    assert res['missing'] == ['NOPE']
    import json
    json.dumps(res['series'], allow_nan=False)
    assert E.analyze(DATES, PX, [], ['B']) is None
