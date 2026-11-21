"""
Imprecisiones numéricas: todo número que muestra el dashboard tiene que poder
recalcularse a mano desde los estados contables de origen.

Los datos de FakeTicker son redondos a propósito (ver fakes.py), así cada
aserción es una cuenta verificable y no un "snapshot" opaco.
"""
import json
import math

import pandas as pd
import pytest

from fakes import (ANNUAL_CASHFLOW, ANNUAL_DATES, ANNUAL_INCOME, FakeTicker,
                   is_strict_json_number, statement)


def strict_json(resp):
    """Parsea rechazando NaN/Infinity: el navegador no los acepta en JSON."""
    def _reject(c):
        raise ValueError(f'Constante JSON inválida en la respuesta: {c}')
    return json.loads(resp.get_data(as_text=True), parse_constant=_reject)


# ── Helpers numéricos ────────────────────────────────────────────────────────

@pytest.mark.parametrize('s,expected', [
    ('1.479', 1479.0), ('20,08 %', 20.08), ('US$ 1.234,50', 1234.5), ('', None), ('abc', None),
])
def test_parse_num_spanish_locale(server_module, s, expected):
    assert server_module._parse_num(s) == expected


def test_compute_ema_matches_reference(server_module):
    values = [float(i) for i in range(1, 31)]
    ema = server_module._compute_ema(values, 20)
    assert ema[:19] == [None] * 19
    assert ema[19] == pytest.approx(10.5)
    # Referencia independiente: pandas ewm sembrado con la SMA inicial
    k = 2 / 21
    ref = 10.5
    for v in values[20:]:
        ref = v * k + ref * (1 - k)
    assert ema[-1] == pytest.approx(ref)


def test_compute_ema_short_series(server_module):
    assert server_module._compute_ema([1.0, 2.0], 20) == [None, None]


# ── /api/financials: empresa ────────────────────────────────────────────────
# Serie anual = FY22..FY25 (de income_stmt) + TTM (suma de los últimos 4 trimestres).

from fakes import QUARTER_CASHFLOW, QUARTER_DATES, QUARTER_INCOME

ANNUAL = 4


@pytest.fixture
def acme(client, fake_yf):
    resp = client.get('/api/financials/ACME')
    assert resp.status_code == 200
    return strict_json(resp)


def ttm(row, src=QUARTER_INCOME):
    return sum(src[row][:4])


def test_years_ascending_and_aligned(acme):
    assert acme['years'] == ['FY22', 'FY23', 'FY24', 'FY25', 'TTM']
    for key in ('rev', 'fcf', 'gm', 'om', 'nm'):
        assert len(acme[key]) == len(acme['years']), key


def test_revenue_in_billions(acme):
    expected = [v / 1e9 for v in reversed(ANNUAL_INCOME['Total Revenue'])]
    assert acme['rev'][:ANNUAL] == pytest.approx(expected)
    assert acme['rev'][-1] == pytest.approx(ttm('Total Revenue') / 1e9)
    assert acme['unit'] == '$B'


def test_margins_recomputable(acme):
    rev = list(reversed(ANNUAL_INCOME['Total Revenue']))
    for key, row in (('gm', 'Gross Profit'), ('om', 'Operating Income'), ('nm', 'Net Income')):
        src = list(reversed(ANNUAL_INCOME[row]))
        assert acme[key][:ANNUAL] == [round(s / r * 100, 1) for s, r in zip(src, rev)], key
        assert acme[key][-1] == round(ttm(row) / ttm('Total Revenue') * 100, 1), f'{key} TTM'


def test_margins_within_sane_bounds(acme):
    for key in ('gm', 'om', 'nm'):
        for v in acme[key]:
            assert v is None or v <= 100, f'{key}={v} fuera de rango'
    for g, o in zip(acme['gm'], acme['om']):
        assert o <= g, 'El margen operativo no puede superar al bruto'


def test_fcf_is_ocf_plus_capex(acme):
    ocf = list(reversed(ANNUAL_CASHFLOW['Operating Cash Flow']))
    capex = list(reversed(ANNUAL_CASHFLOW['Capital Expenditure']))
    assert acme['fcf'][:ANNUAL] == pytest.approx([(o + c) / 1e9 for o, c in zip(ocf, capex)])
    fcf_ttm = sum(QUARTER_CASHFLOW['Operating Cash Flow'][:4]) + sum(QUARTER_CASHFLOW['Capital Expenditure'][:4])
    assert acme['fcf'][-1] == pytest.approx(fcf_ttm / 1e9)


def test_missing_rows_become_null_not_zero(client, fake_yf):
    income = {k: v for k, v in ANNUAL_INCOME.items() if k != 'Gross Profit'}
    q_income = {k: v for k, v in QUARTER_INCOME.items() if k != 'Gross Profit'}
    fake_yf['NOGP'] = FakeTicker('NOGP', income=statement(income, ANNUAL_DATES),
                                 q_income=statement(q_income, QUARTER_DATES))
    data = strict_json(client.get('/api/financials/NOGP'))
    assert data['gm'] == [None] * 5, 'Un dato faltante no debe mostrarse como 0%'
    assert all(q['gm'] is None for q in data['quarters'])
    assert all(v is not None for v in data['nm'])


def test_nan_cells_never_leak_to_json(client, fake_yf):
    income = {k: list(v) for k, v in ANNUAL_INCOME.items()}
    income['Net Income'][0] = float('nan')
    fake_yf['NANCO'] = FakeTicker('NANCO', income=statement(income, ANNUAL_DATES))
    data = strict_json(client.get('/api/financials/NANCO'))
    assert data['nm'][ANNUAL - 1] is None
    for key in ('rev', 'fcf', 'gm', 'om', 'nm'):
        assert all(is_strict_json_number(v) for v in data[key]), key


def test_small_company_values_are_in_millions(client, fake_yf):
    """Antes el endpoint decía '$M' pero mandaba miles de millones (IONQ se veía como $0.0M)."""
    income = {k: [x / 1000 for x in v] for k, v in ANNUAL_INCOME.items()}
    q_income = {k: [x / 1000 for x in v] if k != 'Diluted EPS' else v for k, v in QUARTER_INCOME.items()}
    q_cash = {k: [x / 1000 for x in v] for k, v in QUARTER_CASHFLOW.items()}
    cash = {k: [x / 1000 for x in v] for k, v in ANNUAL_CASHFLOW.items()}
    fake_yf['SMALL'] = FakeTicker('SMALL', income=statement(income, ANNUAL_DATES),
                                  cashflow=statement(cash, ANNUAL_DATES),
                                  q_income=statement(q_income, QUARTER_DATES),
                                  q_cashflow=statement(q_cash, QUARTER_DATES))
    data = strict_json(client.get('/api/financials/SMALL'))
    assert data['unit'] == '$M'
    assert data['rev'][-2] == pytest.approx(400.0)        # 400e6 → 400 ($M)
    assert data['quarters'][-1]['rev'] == pytest.approx(110.0)


def test_kpis_reflect_info(acme):
    kpis = dict(acme['kpis'])
    assert kpis['Price'] == '$200.00'
    assert kpis['P/E'] == '30.0x'
    assert kpis['Mkt Cap'] == '$3.00T'
    assert kpis['Revenue TTM'] == '$419.00B'
    assert kpis['Net Income TTM'] == '$105.00B'


def test_dividend_yield_from_rate_not_ambiguous_field(acme):
    """dividendYield de Yahoo pasó de fracción a porcentaje: se calcula tasa / precio."""
    assert dict(acme['kpis'])['Div Yield'] == '0.50%'     # 1.00 / 200.00


def test_empty_statements_do_not_crash(client, fake_yf):
    import pandas as pd
    fake_yf['EMPTY'] = FakeTicker('EMPTY', income=pd.DataFrame(), cashflow=pd.DataFrame(),
                                  q_income=pd.DataFrame(), q_cashflow=pd.DataFrame())
    resp = client.get('/api/financials/EMPTY')
    assert resp.status_code == 200
    data = strict_json(resp)
    assert data['rev'] == [] and data['quarters'] == []
