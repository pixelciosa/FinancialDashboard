"""
Modelo de riesgo determinista (risk.py, fase R1). Más alto = más riesgo.

Cubre: reglas por métrica (bordes), monotonicidad (empeorar un dato nunca baja el riesgo),
faltantes vs no aplica, estadísticas de mercado verificables a mano, eventos reales,
ETF y cripto, caché, concurrencia y ausencia total de IA.
"""
import json
import threading
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import financials as fin
import risk
from fakes import (ETF_INFO, QUARTER_DATES, QUARTER_INCOME, STOCK_INFO, FakeTicker,
                   daily_history, statement)


def strict(resp):
    def bad(c):
        raise ValueError(f'Constante JSON inválida: {c}')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return json.loads(resp.get_data(as_text=True), parse_constant=bad)


def by_key(report):
    return {m['key']: m for p in report['pillars'] for m in p['metrics']}


# ── Reglas por métrica ───────────────────────────────────────────────────────

@pytest.mark.parametrize('pe,points', [(5, 2), (14.99, 2), (15, 5), (24.99, 5), (25, 8), (39.9, 8), (40, 12), (300, 12)])
def test_pe_bands(pe, points):
    m = by_key({'pillars': [risk.valuation_pillar({'pe': pe})]})
    assert m['pe']['points'] == points


def test_missing_data_is_neutral_and_flagged():
    m = risk.metric('ps', None, 8, [(2, 1, 'low'), (None, 8, 'very_high')])
    assert m['points'] == 4 and m['missing'] is True and m['tier'] == 'unknown'


def test_losses_are_high_risk_not_missing():
    p = risk.valuation_pillar({'pe': None, 'net_income_ttm': -5e9})
    pe = p['metrics'][0]
    assert pe['points'] == 10 and pe['note'] == 'losses' and pe['missing'] is False


def test_fcf_yield_excluded_when_currencies_differ():
    """NU: flujo en ARS y capitalización en USD no se pueden dividir."""
    p = risk.valuation_pillar({'pe': 10, 'fcf_ttm': 5e11, 'market_cap': 1e10,
                               'currency': 'USD', 'financial_currency': 'ARS'})
    fy = p['metrics'][3]
    assert fy['excluded'] is True and fy['note'] == 'currency_mismatch'


def test_bank_excludes_leverage_and_fcf():
    p = risk.financial_pillar({'debt_to_equity': 9.0, 'current_ratio': 0.2, 'fcf_ttm': -1e9},
                              [], ttm_margin=25, bank=True)
    keys = {m['key']: m for m in p['metrics']}
    for k in ('debt_to_equity', 'current_ratio', 'fcf_positive'):
        assert keys[k]['excluded'] is True and keys[k]['note'] == 'bank'


def test_excluded_metrics_do_not_count():
    rep = risk.compose([risk.market_pillar({'volatility_pct': 60, 'max_drawdown_pct': -55,
                                            'above_sma200': False, 'sma200_rising': False}, None, is_crypto=True)])
    m = rep['pillars'][0]
    assert m['max'] == 24, 'Beta no aplica a cripto: 30 - 6'
    assert m['points'] == 10 + 8 + 6
    assert rep['risk_score'] == 100


@pytest.mark.parametrize('score,level', [(0, 'low'), (29, 'low'), (30, 'moderate'), (44, 'moderate'),
                                         (45, 'high'), (59, 'high'), (60, 'very_high'), (100, 'very_high')])
def test_levels(score, level):
    assert risk.level_for(score) == level


def test_insufficient_coverage():
    rep = risk.compose([risk.valuation_pillar({}), risk.financial_pillar({}, [], None, False),
                        risk.market_pillar({}, None)])
    assert rep['coverage'] == 0 and rep['level'] == 'insufficient' and rep['valuation_label'] is None


# ── Monotonicidad: empeorar cualquier dato nunca baja el riesgo ──────────────

BASE = {'pe': 18, 'fwd_pe': 16, 'ps': 3, 'fcf_ttm': 4e9, 'market_cap': 1e11, 'currency': 'USD',
        'financial_currency': 'USD', 'debt_to_equity': 0.8, 'current_ratio': 1.6, 'beta': 1.0, 'net_income_ttm': 5e9}
BASE_STATS = {'volatility_pct': 25, 'max_drawdown_pct': -20, 'above_sma200': True, 'sma200_rising': True}


def stock_score(m=None, yoy=8, margin=15, stats=None):
    m = {**BASE, **(m or {})}
    quarters = [{'rev_yoy': yoy}]
    return risk.compose([risk.valuation_pillar(m), risk.financial_pillar(m, quarters, margin, False),
                         risk.market_pillar({**BASE_STATS, **(stats or {})}, m['beta'])])['risk_score']


@pytest.mark.parametrize('field,values', [
    ('ps', [1, 3, 6, 12]),
    ('fwd_pe', [10, 15.5, 17, 25]),
    ('debt_to_equity', [0.2, 0.7, 1.5, 3]),
    ('current_ratio', [3, 1.5, 1.1, 0.7]),
    ('beta', [0.5, 1.0, 1.4, 2.0]),
    ('fcf_ttm', [8e9, 3e9, 1e9, -1e9]),
])
def test_monotonic_metric(field, values):
    scores = [stock_score({field: v}) for v in values]
    assert scores == sorted(scores), f'{field}: {list(zip(values, scores))}'
    assert scores[-1] > scores[0], f'{field} no mueve el puntaje'


def test_monotonic_pe_with_proportional_forward():
    """Más caro, con la misma expectativa de crecimiento (P/E futuro = 90% del actual), nunca es menos riesgo.
    (Mover solo el P/E cambia la relación futuro/actual, que es otra señal.)"""
    scores = [stock_score({'pe': v, 'fwd_pe': v * 0.9}) for v in (8, 15, 22, 30, 45, 90)]
    assert scores == sorted(scores) and scores[-1] > scores[0], scores


def test_forward_pe_above_trailing_raises_risk():
    """Si el mercado espera ganancias menores (P/E futuro > actual), el riesgo sube."""
    assert stock_score({'pe': 8, 'fwd_pe': 16}) > stock_score({'pe': 8, 'fwd_pe': 6})


def test_monotonic_growth_margin_and_market():
    for scores in ([stock_score(yoy=v) for v in (30, 10, 2, -5, -20)],
                   [stock_score(margin=v) for v in (30, 15, 5, -10)],
                   [stock_score(stats={'volatility_pct': v}) for v in (10, 25, 40, 70)],
                   [stock_score(stats={'max_drawdown_pct': v}) for v in (-5, -20, -40, -70)]):
        assert scores == sorted(scores) and scores[-1] > scores[0], scores


def test_trend_order():
    combos = [(True, True), (True, False), (False, True), (False, False)]
    scores = [stock_score(stats={'above_sma200': a, 'sma200_rising': r}) for a, r in combos]
    assert scores == sorted(scores) and scores[-1] > scores[0]


# ── Estadísticas de mercado verificables a mano ──────────────────────────────

def series(values):
    return pd.Series(values, index=pd.bdate_range('2025-01-01', periods=len(values)))


def test_drawdowns_known_path():
    s = series([50 + i for i in range(51)] + [100 - i for i in range(41)] + [60 + i for i in range(21)])
    st = risk.market_stats(s)
    assert st['max_drawdown_pct'] == -40.0          # 100 → 60
    assert st['drawdown_from_high_pct'] == -20.0     # 80 vs 100
    assert st['high_52w'] == 100 and st['low_52w'] == 50


def test_volatility_matches_numpy():
    rng = np.random.default_rng(7)
    s = series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 260))))
    expected = np.std(np.diff(np.log(s.values)), ddof=1) * np.sqrt(252) * 100
    assert risk.market_stats(s)['volatility_pct'] == pytest.approx(expected, abs=0.01)


def test_rsi_extremes():
    assert risk.rsi(series(list(range(1, 60))), 14) == 100.0
    assert risk.rsi(series([10, 11] * 40), 14) == pytest.approx(50, abs=3)


def test_sma200_trend_flags():
    up = risk.market_stats(series([100 + i * 0.5 for i in range(260)]))
    assert up['above_sma200'] is True and up['sma200_rising'] is True
    down = risk.market_stats(series([300 - i * 0.5 for i in range(260)]))
    assert down['above_sma200'] is False and down['sma200_rising'] is False
    short = risk.market_stats(series([100] * 100))
    assert short['above_sma200'] is None


def test_too_little_history():
    assert risk.market_stats(series([1, 2, 3]))['n_days'] == 3


def test_monthly_closes_use_real_months():
    """Reemplaza el eje fijo 'May 25 a Apr 26' que mostraba meses equivocados."""
    h = daily_history()
    m = risk.monthly_closes(h['Close'])
    assert len(m) == 12
    assert m[-1]['month'] == date.today().strftime('%Y-%m')
    assert [x['month'] for x in m] == sorted(x['month'] for x in m)
    last_of_month = h['Close'][h.index.strftime('%Y-%m') == m[-2]['month']].iloc[-1]
    assert m[-2]['close'] == pytest.approx(last_of_month)


def test_events_are_real_and_past_only():
    h = daily_history(dividends=(10, 200))
    from fakes import earnings_dates_df
    ev = risk.price_events(h, earnings_dates_df())
    types = [e['type'] for e in ev]
    assert types.count('dividend') == 2
    assert types.count('earnings') == 3, 'El reporte futuro (+60 días) no es un evento pasado'
    assert all(e['date'] <= date.today().isoformat() for e in ev)
    assert [e['date'] for e in ev] == sorted(e['date'] for e in ev)


def test_quarter_ends_used_when_report_dates_unavailable():
    """Sin fechas de presentación (falta lxml o Yahoo cambió la página): cierres de trimestre,
    marcados como tales, nunca como fecha de resultados."""
    h = daily_history()
    quarters = [{'period_end': (date.today() - timedelta(days=d)).isoformat(), 'label': f'Q{i}'}
                for i, d in enumerate((40, 130, 220, 400))]
    ev = risk.price_events(h, None, quarters)
    qe = [e for e in ev if e['type'] == 'quarter_end']
    assert len(qe) == 3, 'El de hace 400 días queda fuera de la ventana de 1 año'
    assert not any(e['type'] == 'earnings' for e in ev)


def test_report_dates_failure_is_reported_not_hidden(client, fake_yf, capsys, monkeypatch):
    class NoLxml(FakeTicker):
        def get_earnings_dates(self, limit=12, offset=0):
            raise ImportError("Missing optional dependency 'lxml'")
    fake_yf['NOLX'] = NoLxml('NOLX')
    monkeypatch.setattr(risk, '_REPORTED_ERRORS', set())
    r = strict(client.get('/api/risk/NOLX'))
    assert 'pip install lxml' in capsys.readouterr().out
    types = {e['type'] for e in r['events']}
    assert 'quarter_end' in types and 'earnings' not in types


# ── Endpoint: acciones ───────────────────────────────────────────────────────

RISKY_INFO = {**STOCK_INFO, 'trailingPE': None, 'forwardPE': -20.0, 'priceToSalesTrailing12Months': 18.0,
              'debtToEquity': 320.0, 'currentRatio': 0.8, 'beta': 2.1}


def risky_ticker(sym='RISKY'):
    q = {k: list(v) for k, v in QUARTER_INCOME.items()}
    q['Net Income'] = [-x for x in q['Net Income']]
    q['Total Revenue'] = [60e9, 70e9, 80e9, 90e9, 102e9, 98e9, 96e9, 95e9]
    return FakeTicker(sym, info=RISKY_INFO, q_income=statement(q, QUARTER_DATES),
                      daily=daily_history(drift=-0.003, wiggle=0.05))


def test_stock_report_consistent(client, fake_yf):
    r = strict(client.get('/api/risk/ACME'))
    assert r['kind'] == 'stock' and [p['key'] for p in r['pillars']] == ['valuation', 'financial', 'market']
    pts = sum(p['points'] for p in r['pillars'])
    mx = sum(p['max'] for p in r['pillars'])
    assert mx == 100 and r['risk_score'] == round(pts / mx * 100)
    assert r['level'] == risk.level_for(r['risk_score'])
    assert r['valuation_label'] in ('cheap', 'fair', 'expensive')
    assert r['coverage'] == 1.0


def test_same_numbers_as_financial_tab(client, fake_yf):
    r = strict(client.get('/api/risk/ACME'))
    f = strict(client.get('/api/financials/ACME'))
    m = by_key(r)
    # Las métricas que dependen del precio se llevan al cierre del día: P/E = P/E de la caché × precio
    # de hoy / precio de la caché (el BPA no cambia entre trimestres). Ver test_valuation.py.
    price_now = float(daily_history(dividends=(40, 103, 166, 229))['Close'].iloc[-1])
    assert f['metrics']['pe'] == 30.0 and f['metrics']['price'] == 200.0
    assert m['pe']['value'] == pytest.approx(30.0 * price_now / 200.0, abs=1e-4)
    assert r['valuation_basis']['repriced'] is True and r['valuation_basis']['price_ref'] == 200.0
    assert m['net_margin']['value'] == f['nm'][-1]
    assert m['debt_to_equity']['value'] == 1.5, 'Yahoo informa debtToEquity en %: 150 = 1.5x'
    assert r['financials_period'] == f['latest_period_end']


def test_riskier_company_scores_higher(client, fake_yf):
    fake_yf['RISKY'] = risky_ticker()
    safe = strict(client.get('/api/risk/ACME'))
    bad = strict(client.get('/api/risk/RISKY'))
    assert bad['risk_score'] > safe['risk_score'] + 20
    order = ['low', 'moderate', 'high', 'very_high']
    assert order.index(bad['level']) > order.index(safe['level'])
    m = by_key(bad)
    assert m['pe']['note'] == 'losses' and m['fwd_vs_pe']['note'] == 'expected_losses'
    assert m['revenue_growth']['value'] < -10 and m['revenue_growth']['points'] == 8


def test_market_section_and_chart_data(client, fake_yf):
    r = strict(client.get('/api/risk/ACME'))
    assert set(r['market']) >= {'volatility_pct', 'max_drawdown_pct', 'drawdown_from_high_pct', 'rsi14', 'sma200'}
    assert len(r['prices_12m']) == 12
    assert {e['type'] for e in r['events']} == {'dividend', 'earnings'}


# ── ETF y cripto ─────────────────────────────────────────────────────────────

def test_etf_uses_cost_and_market(client, fake_yf):
    fake_yf['FETF'] = FakeTicker('FETF', info=dict(ETF_INFO))
    r = strict(client.get('/api/risk/FETF'))
    assert [p['key'] for p in r['pillars']] == ['cost', 'market']
    m = by_key(r)
    assert m['expense_ratio']['value'] == 0.09 and m['top10_weight']['value'] == pytest.approx(18.8)
    assert m['max_sector_weight']['value'] == 31.5
    assert r['valuation_label'] is None


def test_crypto_uses_usd_symbol_and_market_only(client, fake_yf):
    r = strict(client.get('/api/risk/SOL'))
    assert r['source_symbol'] == 'SOL-USD' and r['kind'] == 'crypto'
    assert set(fake_yf.calls) == {'SOL-USD'}, 'Nunca consultar "SOL" (es un ETF de Grayscale)'
    assert [p['key'] for p in r['pillars']] == ['market'] and r['pillars'][0]['max'] == 24


# ── Caché ────────────────────────────────────────────────────────────────────

def test_cached_within_ttl(client, fake_yf):
    client.get('/api/risk/ACME')
    n = len(fake_yf.calls)
    assert strict(client.get('/api/risk/ACME'))['cached'] is True
    assert len(fake_yf.calls) == n


def test_recomputed_after_ttl(client, fake_yf):
    first = risk.get_risk('ACME')
    later = datetime.now(timezone.utc) + timedelta(hours=risk.TTL_HOURS + 1)
    again = risk.get_risk('ACME', now=later)
    assert again['cached'] is False and again['computed_at'] != first['computed_at']


def test_recomputed_when_financials_change(client, fake_yf):
    risk.get_risk('ACME')
    e = fin.read_cache('ACME')
    e['fetched_at'] = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    fin.write_cache('ACME', e)
    assert risk.get_risk('ACME')['cached'] is False


def test_empty_prices_keep_previous_report(client, fake_yf):
    risk.get_risk('ACME')
    fake_yf['ACME'] = FakeTicker('ACME', daily=pd.DataFrame(columns=['Close']))
    later = datetime.now(timezone.utc) + timedelta(hours=risk.TTL_HOURS + 1)
    r = risk.get_risk('ACME', now=later)
    assert r.get('stale_source') is True and r['market']['n_days'] > 30


def test_concurrent_requests_build_once(client, fake_yf):
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(client.get('/api/risk/ACME').status_code)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes == [200] * 8
    assert fake_yf.calls.count('ACME') == 3, 'Una construcción: financials + precios + fechas de resultados'


def test_summary_is_offline(client, fake_yf):
    client.get('/api/risk/ACME')
    fake_yf.calls.clear()
    body = client.get('/api/risk/summary?tickers=ACME,NOPE').get_json()
    assert fake_yf.calls == []
    assert body['ACME']['risk_score'] >= 0 and body['NOPE'] is None


# ── Sin IA ───────────────────────────────────────────────────────────────────

def test_no_ai_involved(client, fake_yf, server_module, monkeypatch):
    def boom(*a, **k):
        raise AssertionError('El modelo de riesgo no debe llamar a la IA')
    monkeypatch.setattr(server_module, '_call_gemma', boom)
    monkeypatch.setattr(server_module.http_requests, 'post', boom)
    assert client.get('/api/risk/ACME').status_code == 200


def test_identical_inputs_identical_hash(client, fake_yf):
    a = risk.build_report('ACME')
    b = risk.build_report('ACME')
    assert a['inputs_hash'] == b['inputs_hash'] and a['risk_score'] == b['risk_score']
