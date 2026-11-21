"""
Fase 1: datos reales de la tab Análisis Financiero (financials.py + /api/financials).

Cada valor esperado se recalcula desde los datos de entrada de fakes.py.
"""
import json
from datetime import date

import pandas as pd
import pytest

import financials as fin
from fakes import (EARNINGS_HISTORY, ETF_INFO, NEXT_EARNINGS, QUARTER_DATES, QUARTER_INCOME,
                   STOCK_INFO, FakeFundsData, FakeTicker, monthly_history, statement)


def get(client, sym, **params):
    q = '&'.join(f'{k}={v}' for k, v in params.items())
    resp = client.get(f'/api/financials/{sym}' + (f'?{q}' if q else ''))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


# ── Trimestres ───────────────────────────────────────────────────────────────

@pytest.fixture
def acme(client, fake_yf):
    return get(client, 'ACME')


def test_quarters_oldest_to_newest_with_correct_labels(acme):
    q = acme['quarters']
    assert len(q) == 8
    assert [r['period_end'] for r in q] == sorted(QUARTER_DATES)
    assert q[-1]['label'] == 'Q3 2026' and q[0]['label'] == 'Q4 2024'
    assert q[-1]['fiscal_label'] is None, 'Año fiscal = calendario: sin etiqueta fiscal'


def test_quarter_values_recomputable(acme):
    newest = acme['quarters'][-1]
    rev, gp, ni = (QUARTER_INCOME[k][0] for k in ('Total Revenue', 'Gross Profit', 'Net Income'))
    assert newest['rev'] == pytest.approx(rev / 1e9)
    assert newest['gm'] == round(gp / rev * 100, 1)
    assert newest['nm'] == round(ni / rev * 100, 1)
    assert newest['eps'] == QUARTER_INCOME['Diluted EPS'][0]
    assert newest['fcf'] == pytest.approx((36e9 - 8e9) / 1e9)


def test_revenue_yoy_against_same_quarter_last_year(acme):
    by_end = {r['period_end']: r for r in acme['quarters']}
    assert by_end['2026-09-30']['rev_yoy'] == round((110 / 102 - 1) * 100, 1)
    assert by_end['2024-12-31']['rev_yoy'] is None, 'Sin el trimestre de un año antes no hay YoY'


def test_eps_surprise_is_deterministic(acme):
    by_end = {r['period_end']: r for r in acme['quarters']}
    expected = {  # (actual, estimado) de EARNINGS_HISTORY
        '2025-12-31': 'BEAT', '2026-03-31': 'MISS', '2026-06-30': 'IN LINE', '2026-09-30': 'BEAT'}
    for end, verdict in expected.items():
        assert by_end[end]['beat'] == verdict, end
    q3 = by_end['2026-09-30']
    assert q3['eps_adj'] == 1.95 and q3['eps_est'] == 1.85
    assert q3['surprise_pct'] == round((1.95 - 1.85) / 1.85 * 100, 1)
    assert by_end['2025-09-30']['beat'] is None, 'Sin estimado no se inventa BEAT/MISS'


def test_latest_period_and_next_earnings(acme):
    assert acme['latest_period_end'] == '2026-09-30'
    assert acme['latest_period_label'] == 'Q3 2026'
    assert acme['next_earnings_date'] == NEXT_EARNINGS.isoformat()
    assert acme['has_ttm'] is True and acme['source'] == 'yfinance'


def test_no_ttm_when_latest_quarter_is_the_fiscal_year_end(client, fake_yf):
    q_income = {k: v[3:] for k, v in QUARTER_INCOME.items()}     # último trimestre = 2025-12-31
    fake_yf['FYQ'] = FakeTicker('FYQ', q_income=statement(q_income, QUARTER_DATES[3:]))
    data = get(client, 'FYQ')
    assert data['years'][-1] == 'FY25' and data['has_ttm'] is False


def test_no_ttm_with_less_than_four_quarters(client, fake_yf):
    q_income = {k: v[:3] for k, v in QUARTER_INCOME.items()}
    fake_yf['NEW'] = FakeTicker('NEW', q_income=statement(q_income, QUARTER_DATES[:3]))
    assert 'TTM' not in get(client, 'NEW')['years']


# ── Año fiscal desplazado ────────────────────────────────────────────────────

@pytest.mark.parametrize('end,fye,label', [
    ('2026-06-27', 9, 'Q3 FY26'),    # Apple
    ('2025-12-27', 9, 'Q1 FY26'),
    ('2026-07-26', 1, 'Q2 FY27'),    # NVIDIA (cierra fin de enero)
    ('2025-08-03', 10, 'Q3 FY25'),   # Broadcom (cierra ~2 de noviembre)
    ('2026-02-01', 10, 'Q1 FY26'),
    ('2025-11-02', 10, 'Q4 FY25'),
])
def test_fiscal_quarter_label(end, fye, label):
    assert fin.fiscal_quarter_label(pd.Timestamp(end), fye) == label


def test_fiscal_year_end_detected_from_statements(client, fake_yf):
    dates = ['2025-09-27', '2024-09-28', '2023-09-30', '2022-09-24']
    from fakes import ANNUAL_INCOME
    fake_yf['APPL'] = FakeTicker('APPL', income=statement(ANNUAL_INCOME, dates),
                                 q_income=statement(QUARTER_INCOME, ['2026-06-27', '2026-03-28', '2025-12-27',
                                                                     '2025-09-27', '2025-06-28', '2025-03-29',
                                                                     '2024-12-28', '2024-09-28']))
    data = get(client, 'APPL')
    assert data['fiscal_year_end_month'] == 9
    assert data['quarters'][-1]['label'] == 'Q2 2026'
    assert data['quarters'][-1]['fiscal_label'] == 'Q3 FY26'


# ── Bancos ──────────────────────────────────────────────────────────────────

def test_bank_hides_gross_margin_and_fcf(client, fake_yf):
    info = {**STOCK_INFO, 'sector': 'Financial Services', 'industry': 'Banks - Regional'}
    fake_yf['BANK'] = FakeTicker('BANK', info=info)
    data = get(client, 'BANK')
    assert data['bankNote'] is True
    assert all(v is None for v in data['gm']) and all(v is None for v in data['fcf'])
    assert all(q['gm'] is None and q['fcf'] is None for q in data['quarters'])
    assert all(v is not None for v in data['nm'])


# ── ETF ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def etf(client, fake_yf):
    fake_yf['FETF'] = FakeTicker('FETF', info=dict(ETF_INFO),
                                 history=monthly_history(start='2021-01-01', periods=70))
    return get(client, 'FETF')


def test_etf_holdings_are_real(etf):
    segs = {s['l']: s['v'] for s in etf['segs']}
    assert segs['Alpha Inc'] == 7.1 and segs['Beta Corp'] == 6.5
    assert segs['Resto'] == round(100 - 7.1 - 6.5 - 5.2, 2)
    assert etf['segs_source'] == 'holdings'


def test_etf_sectors_sorted(etf):
    assert [s['l'] for s in etf['sectors']] == ['technology', 'financial_services', 'healthcare']
    assert etf['sectors'][0]['v'] == 31.5


def test_etf_expense_ratio_is_not_pe(etf):
    kpis = dict(etf['kpis'])
    assert kpis['Expense Ratio'] == '0.09%'
    assert 'P/E' not in kpis


def test_etf_dividend_yield_from_fund_yield(etf):
    assert dict(etf['kpis'])['Div Yield'] == '1.30%'      # ETF_INFO['yield'] = 0.013


def test_etf_annual_returns_close_to_close(etf):
    # 70 meses desde ene-2021 (+1% mensual): cierres de diciembre consecutivos difieren 12 meses.
    assert etf['years'][:4] == ['FY22', 'FY23', 'FY24', 'FY25']
    assert etf['annualReturns'][0] == round((1.01 ** 12 - 1) * 100, 1)


def test_etf_current_year_is_ytd():
    hist = monthly_history(start='2024-01-01', periods=34)       # hasta oct-2026
    labels, values = fin.etf_annual_returns(hist, today=date(2026, 11, 6))
    assert labels == ['FY25', 'YTD']
    assert values[-1] == round((1.01 ** 10 - 1) * 100, 1)


def test_etf_without_funds_data_falls_back(client, fake_yf):
    empty = FakeFundsData(holdings=[], sectors={})
    fake_yf['NOFD'] = FakeTicker('NOFD', info=dict(ETF_INFO), funds_data=empty)
    data = get(client, 'NOFD')
    assert data['segs'] == [{'l': 'Large Blend', 'sym': None, 'v': 100}]
    assert data['segs_source'] is None


# ── Cripto ──────────────────────────────────────────────────────────────────

def test_crypto_is_unsupported_and_never_queried(client, fake_yf):
    data = get(client, 'SOL')
    assert data['supported'] is False and data['kind'] == 'crypto'
    assert fake_yf.calls == []


# ── Caché en disco ───────────────────────────────────────────────────────────

def test_second_request_served_from_disk(client, fake_yf):
    first = get(client, 'ACME')
    assert first['cached'] is False
    calls = len(fake_yf.calls)
    second = get(client, 'ACME')
    assert second['cached'] is True
    assert len(fake_yf.calls) == calls, 'Con caché no se consulta yfinance'
    assert second['quarters'] == first['quarters']


def test_force_does_not_bypass_freshness_gate(client, fake_yf):
    get(client, 'ACME')
    calls = len(fake_yf.calls)
    data = get(client, 'ACME', force='true')
    assert data['cached'] is True and len(fake_yf.calls) == calls, 'Sin datos nuevos, force no descarga'


def test_empty_source_does_not_overwrite_cache(client, fake_yf):
    good = get(client, 'ACME')
    fin.write_cache('ACME', {**fin.read_cache('ACME'), 'newest_available_period_end': '2026-12-31'})
    fake_yf['ACME'] = FakeTicker('ACME', income=pd.DataFrame(), cashflow=pd.DataFrame(),
                                 q_income=pd.DataFrame(), q_cashflow=pd.DataFrame())
    res = client.post('/api/financials/ACME/refresh').get_json()
    assert res['updated'] is False and res['data']['stale_source'] is True
    assert get(client, 'ACME')['quarters'] == good['quarters']


def test_cache_file_records_freshness_fields(client, fake_yf):
    get(client, 'ACME')
    entry = fin.read_cache('ACME')
    assert entry['latest_period_end'] == '2026-09-30'
    assert entry['next_earnings_date'] == NEXT_EARNINGS.isoformat()
    assert entry['schema_version'] == fin.SCHEMA_VERSION


def test_corrupt_or_old_schema_cache_is_ignored(client, fake_yf):
    import os
    os.makedirs(fin.CACHE_DIR, exist_ok=True)
    with open(os.path.join(fin.CACHE_DIR, 'ACME.json'), 'w') as f:
        f.write('{roto')
    assert get(client, 'ACME')['cached'] is False
    with open(os.path.join(fin.CACHE_DIR, 'ACME.json'), 'w') as f:
        json.dump({'schema_version': 1, 'payload': {}}, f)
    assert get(client, 'ACME')['cached'] is False


def test_foreign_currency_statements_are_labeled(client, fake_yf):
    """NU cotiza en USD pero reporta en pesos: no se puede mostrar '$' para cifras en ARS."""
    fake_yf['YPFX'] = FakeTicker('YPFX', info={**STOCK_INFO, 'financialCurrency': 'ARS', 'currency': 'USD'})
    data = get(client, 'YPFX')
    assert data['currency'] == 'ARS'
    assert dict(data['kpis'])['Revenue TTM'] == 'ARS 419.00B'
    assert dict(data['kpis'])['Price'] == '$200.00', 'El precio sí está en USD'


def test_empty_quarter_columns_are_skipped(client, fake_yf):
    q = {k: [float('nan')] + v for k, v in QUARTER_INCOME.items()}
    fake_yf['GAP'] = FakeTicker('GAP', q_income=statement(q, ['2026-12-31'] + QUARTER_DATES))
    data = get(client, 'GAP')
    assert data['quarters'][-1]['period_end'] == '2026-09-30'
    assert len(data['quarters']) == 8
    assert data['years'][-1] == 'TTM', 'Una columna vacía no debe anular el TTM'


def test_missing_kpi_shows_na_not_null(client, fake_yf):
    info = {k: v for k, v in STOCK_INFO.items() if k not in ('trailingPE', 'beta')}
    fake_yf['NOPE'] = FakeTicker('NOPE', info=info)
    kpis = dict(get(client, 'NOPE')['kpis'])
    assert kpis['P/E'] == 'N/A' and kpis['Beta'] == 'N/A'


def test_invalid_ticker_rejected(client):
    assert client.get('/api/financials/<x>').status_code in (400, 404)
