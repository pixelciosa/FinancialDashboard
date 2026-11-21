"""
Registro de valuaciones (valuations.py) y valuación al precio del día (risk.reprice_metrics).

Qué protege:
- Precisión: P/E, P/E futuro, P/S y rendimiento FCF se llevan al precio de hoy; los splits no
  disfrazan una acción de "barata"; sin precio no se inventa nada.
- Regla de etiqueta: sin métricas reales de valuación no hay "justa" por defecto.
- Optimización: una sola descarga de precios para todos; estados contables y fechas de
  resultados no se vuelven a pedir si no cambiaron.
- Consistencia: el registro muestra la misma etiqueta que el Reporte de Riesgo.
"""
import json
from datetime import date, timedelta

import pandas as pd
import pytest

import financials as fin
import risk
import valuations as val
from fakes import ETF_INFO, FakeDownload, FakeTicker, STOCK_INFO, daily_history, is_strict_json_number


def strict(resp, status=200):
    def bad(c):
        raise ValueError(f'Constante JSON inválida: {c}')
    assert resp.status_code == status, resp.get_data(as_text=True)
    return json.loads(resp.get_data(as_text=True), parse_constant=bad)


class SyncExecutor:
    def submit(self, fn, *a, **k):
        fn(*a, **k)


@pytest.fixture(autouse=True)
def _reset_job(monkeypatch):
    monkeypatch.setattr(val, 'EXECUTOR', SyncExecutor())
    monkeypatch.setattr(val, 'JOB', {**val.JOB, 'running': False, 'done': 0, 'total': 0,
                                     'updated': [], 'failed': [], 'download_calls': 0})
    yield


@pytest.fixture
def download(monkeypatch, fake_yf):
    d = FakeDownload(fake_yf)
    monkeypatch.setattr(val.yf, 'download', d)
    return d


@pytest.fixture
def history_spy(monkeypatch):
    calls = []
    real = risk._history

    def spy(sym):
        calls.append(sym)
        return real(sym)
    monkeypatch.setattr(risk, '_history', spy)
    return calls


BASE_M = {'price': 187.35, 'pe': 31.27, 'fwd_pe': 26.41, 'ps': 7.83, 'market_cap': 2.913e12,
          'fcf_ttm': 9.87e10, 'currency': 'USD', 'financial_currency': 'USD', 'net_income_ttm': 9.3e10}


def vkeys(pillar):
    return {m['key']: m for m in pillar['metrics']}


# ── Revaluación al precio del día ───────────────────────────────────────────

def test_reprice_scales_price_dependent_metrics():
    m, basis = risk.reprice_metrics(BASE_M, 205.92)
    k = 205.92 / 187.35
    assert basis['repriced'] is True and basis['factor'] == pytest.approx(k, rel=1e-6)
    assert m['pe'] == pytest.approx(31.27 * k) and m['fwd_pe'] == pytest.approx(26.41 * k)
    assert m['ps'] == pytest.approx(7.83 * k) and m['market_cap'] == pytest.approx(2.913e12 * k)
    assert m['fcf_ttm'] == 9.87e10, 'el FCF no depende del precio'
    old, new = vkeys(risk.valuation_pillar(BASE_M)), vkeys(risk.valuation_pillar(m))
    assert new['fcf_yield']['value'] == pytest.approx(9.87e10 / (2.913e12 * k) * 100, abs=1e-4)
    assert new['fwd_vs_pe']['value'] == pytest.approx(old['fwd_vs_pe']['value'], abs=1e-4), \
        'el cociente P/E futuro / actual no cambia con el precio'


@pytest.mark.parametrize('ref,now', [(None, 205.92), (187.35, None), (0, 205.92), (187.35, -1.0)])
def test_without_valid_price_nothing_is_invented(ref, now):
    m, basis = risk.reprice_metrics({**BASE_M, 'price': ref}, now)
    assert basis['repriced'] is False
    assert m['pe'] == 31.27 and m['ps'] == 7.83


def _hist_with_split(split_days_ago, ratio, end_price):
    h = daily_history(days=260, start_price=end_price)
    h['Close'] = end_price
    pos = len(h) - 1 - split_days_ago
    h.iloc[pos, h.columns.get_loc('Stock Splits')] = ratio
    return h


def test_split_after_financials_download_is_neutralized():
    """Un split 4:1 posterior a la caché contable baja el precio a un cuarto sin cambiar la valuación."""
    h = _hist_with_split(5, 4.0, 52.31)
    fetched = (h.index[-30]).isoformat()
    f = risk.split_factor_since(h, fetched)
    assert f == 4.0
    m, basis = risk.reprice_metrics({**BASE_M, 'price': 200.12}, 52.31, f)
    assert basis['factor'] == pytest.approx(52.31 / (200.12 / 4), rel=1e-6)
    assert m['pe'] == pytest.approx(31.27 * 52.31 * 4 / 200.12)


def test_split_before_financials_download_is_ignored():
    h = _hist_with_split(100, 4.0, 52.31)
    assert risk.split_factor_since(h, h.index[-10].isoformat()) == 1.0


def test_higher_price_never_looks_cheaper():
    pts = []
    for p in (120.5, 160.25, 187.35, 240.75, 330.1, 520.4):
        m, _ = risk.reprice_metrics(BASE_M, p)
        pts.append(risk.finalize_pillar(risk.valuation_pillar(m))['points'])
    assert pts == sorted(pts), pts


# ── Regla de la etiqueta ─────────────────────────────────────────────────────

FULL_FIN = {'debt_to_equity': 0.8, 'current_ratio': 1.6, 'fcf_ttm': 4e9, 'beta': 1.05}
FULL_STATS = {'volatility_pct': 24.3, 'max_drawdown_pct': -18.7, 'above_sma200': True, 'sma200_rising': True}
QS = [{'rev_yoy': 8.4}]


def compose_with(vm):
    return risk.compose([risk.valuation_pillar(vm), risk.financial_pillar(FULL_FIN, QS, 18.2, False),
                         risk.market_pillar(FULL_STATS, 1.05)])


def test_no_valuation_data_is_not_fair():
    """Antes: 4 faltantes = mitad de puntos = 'fair'. La cobertura total pasaba por los otros pilares."""
    rep = compose_with({'currency': 'USD', 'financial_currency': 'USD'})
    assert rep['coverage'] >= risk.MIN_COVERAGE and rep['level'] != 'insufficient'
    assert rep['valuation_label'] is None and rep['valuation_reason'] == 'insufficient_valuation'


def test_one_metric_is_not_enough_two_are():
    one = compose_with({'pe': 12.4, 'currency': 'USD', 'financial_currency': 'USD'})
    assert one['valuation_label'] is None
    two = compose_with({'pe': 12.4, 'ps': 1.3, 'currency': 'USD', 'financial_currency': 'USD'})
    assert two['valuation_label'] in ('cheap', 'fair', 'expensive') and two['valuation_reason'] is None


def test_losses_count_as_known_valuation_data():
    rep = compose_with({'pe': -8.2, 'fwd_pe': -11.5, 'currency': 'USD', 'financial_currency': 'USD'})
    assert rep['valuation_label'] == 'expensive'


@pytest.mark.parametrize('report,expected', [
    (None, ('na', 'no_data')),
    ({'kind': 'etf', 'valuation_label': None, 'valuation_reason': 'not_applicable'}, ('na', 'etf')),
    ({'kind': 'crypto', 'valuation_label': None}, ('na', 'crypto')),
    ({'kind': 'stock', 'valuation_label': None, 'valuation_reason': 'insufficient_valuation'},
     ('na', 'insufficient_valuation')),
    ({'kind': 'stock', 'valuation_label': 'cheap'}, ('cheap', None)),
    ({'kind': 'stock', 'valuation_label': 'expensive'}, ('expensive', None)),
])
def test_classify(report, expected):
    assert val.classify(report) == expected


# ── Actualización en lote ────────────────────────────────────────────────────

def cheap_ticker(sym):
    info = {**STOCK_INFO, 'trailingPE': 9.4, 'forwardPE': 7.9, 'priceToSalesTrailing12Months': 1.2,
            'marketCap': 3.1e11}
    return FakeTicker(sym, info=info)


@pytest.fixture
def universe(fake_yf):
    fake_yf['CHEAP'] = cheap_ticker('CHEAP')
    fake_yf['FETF'] = FakeTicker('FETF', info=dict(ETF_INFO))
    return ['ACME', 'CHEAP', 'FETF', 'SOL']


def test_refresh_uses_one_batch_download(client, fake_yf, download, history_spy, universe):
    body = strict(client.post('/api/valuations/refresh', json={'tickers': universe}), 202)
    assert body['started'] is True
    st = strict(client.get('/api/valuations/refresh/status'))
    assert st['running'] is False and st['done'] == 4 and st['failed'] == []
    assert sorted(st['updated']) == sorted(universe)
    assert len(download.calls) == 1 and st['download_calls'] == 1
    assert sorted(download.calls[0]) == sorted(['ACME', 'CHEAP', 'FETF', 'SOL-USD'])
    assert 'SOL' not in download.calls[0], 'cripto siempre con -USD'
    assert history_spy == [], 'no se descargan precios ticker por ticker'


def test_second_refresh_does_not_touch_financials_or_earnings(client, fake_yf, download, universe):
    client.post('/api/valuations/refresh', json={'tickers': universe})
    fake_yf.calls.clear()
    client.post('/api/valuations/refresh', json={'tickers': universe})
    assert fake_yf.calls == [], f'sin trimestre nuevo no se consulta yf.Ticker: {fake_yf.calls}'
    assert len(download.calls) == 2, 'una descarga de precios por actualización'


def test_new_quarter_is_downloaded_before_valuing(client, fake_yf, download, monkeypatch):
    client.post('/api/valuations/refresh', json={'tickers': ['ACME']})
    entry = fin.read_cache('ACME')
    entry['newest_available_period_end'] = (date.today() + timedelta(days=1)).isoformat()
    fin.write_cache('ACME', entry)
    assert fin.get_freshness('ACME')['state'] == 'available'
    refreshed = []
    real = fin.refresh_ticker
    monkeypatch.setattr(fin, 'refresh_ticker', lambda t, **k: refreshed.append(t) or real(t, **k))
    client.post('/api/valuations/refresh', json={'tickers': ['ACME']})
    assert refreshed == ['ACME']


def test_ticker_missing_from_batch_is_retried_alone(client, fake_yf, monkeypatch, history_spy):
    d = FakeDownload(fake_yf, missing={'CHEAP'})
    monkeypatch.setattr(val.yf, 'download', d)
    fake_yf['CHEAP'] = cheap_ticker('CHEAP')
    client.post('/api/valuations/refresh', json={'tickers': ['ACME', 'CHEAP']})
    assert history_spy == ['CHEAP']
    assert sorted(val.status()['updated']) == ['ACME', 'CHEAP']


def test_one_failure_does_not_stop_the_rest(client, fake_yf, download, monkeypatch):
    real = risk.build_report

    def flaky(t, *a, **k):
        if t == 'CHEAP':
            raise RuntimeError('fuente rota')
        return real(t, *a, **k)
    monkeypatch.setattr(risk, 'build_report', flaky)
    fake_yf['CHEAP'] = cheap_ticker('CHEAP')
    client.post('/api/valuations/refresh', json={'tickers': ['CHEAP', 'ACME']})
    st = val.status()
    assert st['updated'] == ['ACME'] and st['failed'] == [{'ticker': 'CHEAP', 'reason': 'RuntimeError'}]


def test_empty_prices_keep_previous_valuation(client, fake_yf, download, monkeypatch):
    client.post('/api/valuations/refresh', json={'tickers': ['ACME']})
    before = risk.read_cache('ACME')
    monkeypatch.setattr(val.yf, 'download', lambda *a, **k: pd.DataFrame())
    fake_yf['ACME'] = FakeTicker('ACME', daily=daily_history(days=5))
    client.post('/api/valuations/refresh', json={'tickers': ['ACME']})
    assert risk.read_cache('ACME')['computed_at'] == before['computed_at']
    assert val.status()['failed'] == [{'ticker': 'ACME', 'reason': 'no_prices'}]


def test_refresh_rejected_while_running(client):
    val.JOB['running'] = True
    body = strict(client.post('/api/valuations/refresh', json={'tickers': ['ACME']}), 409)
    assert body['started'] is False and body['status']['running'] is True


# ── Registro (lectura) ───────────────────────────────────────────────────────

def test_registry_is_offline_and_matches_risk_tab(client, fake_yf, download, universe):
    client.post('/api/valuations/refresh', json={'tickers': universe})
    fake_yf.calls.clear()
    reg = strict(client.get('/api/valuations?tickers=' + ','.join(universe + ['NODATA'])))
    assert fake_yf.calls == [] and len(download.calls) == 1, 'GET /api/valuations no consulta la red'
    rows = {r['ticker']: r for r in reg['rows']}
    for t in ('ACME', 'CHEAP'):
        rep = risk.read_cache(t)
        assert rows[t]['group'] == rep['valuation_label'], 'misma etiqueta que el Reporte de Riesgo'
        assert rows[t]['metrics']['pe']['value'] == next(
            m['value'] for m in rep['pillars'][0]['metrics'] if m['key'] == 'pe')
        assert rows[t]['repriced'] is True and rows[t]['price_date']
    assert rows['CHEAP']['group'] == 'cheap'
    assert (rows['FETF']['group'], rows['FETF']['reason']) == ('na', 'etf')
    assert (rows['SOL']['group'], rows['SOL']['reason']) == ('na', 'crypto')
    assert (rows['NODATA']['group'], rows['NODATA']['reason']) == ('na', 'no_data')
    assert sum(reg['counts'].values()) == 5


def test_registry_json_is_strict(client, fake_yf, download, universe):
    client.post('/api/valuations/refresh', json={'tickers': universe})
    reg = strict(client.get('/api/valuations?tickers=' + ','.join(universe)))

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, float):
            assert is_strict_json_number(x)
    walk(reg)


def test_risk_tab_also_uses_todays_price(client, fake_yf):
    """El tab de Riesgo (GET /api/risk) aplica la misma revaluación que el registro."""
    fake_yf['CHEAP'] = cheap_ticker('CHEAP')
    r = strict(client.get('/api/risk/CHEAP'))
    price_now = r['market']['price']
    pe = next(m for m in r['pillars'][0]['metrics'] if m['key'] == 'pe')
    assert pe['value'] == pytest.approx(9.4 * price_now / STOCK_INFO['currentPrice'], abs=1e-4)
