"""
Smoke tests con red real. NO corren por defecto (marca `live`).

    pytest -m live                    # todo
    pytest -m live -k financials      # solo la tab financiera

Sirven para detectar que una fuente externa cambió de formato o que los datos
quedaron viejos. No llaman a Gemma (no gastan tokens de IA).
"""
import os
from datetime import date

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(scope='module')
def live_client(tmp_path_factory):
    import financials
    import risk
    import server
    financials.CACHE_DIR = str(tmp_path_factory.mktemp('fin_cache'))   # no tocar data/financials real
    risk.CACHE_DIR = str(tmp_path_factory.mktemp('risk_cache'))
    server.app.config['TESTING'] = True
    return server.app.test_client()


def test_yfinance_returns_quarterly_statements():
    import yfinance as yf
    q = yf.Ticker('JPM').quarterly_income_stmt
    assert not q.empty and 'Total Revenue' in q.index
    newest = q.columns[0].date()
    assert (date.today() - newest).days < 150, f'El último trimestre de JPM en yfinance es {newest}'


def test_financials_endpoint_matches_raw_source(live_client):
    """Anti-imprecisión: lo que devuelve el endpoint coincide con la fuente cruda."""
    import yfinance as yf
    t = yf.Ticker('ORCL')
    raw = t.income_stmt
    q = t.quarterly_income_stmt
    data = live_client.get('/api/financials/ORCL').get_json()
    fy_idx = data['years'].index(f"FY{str(raw.columns[0].year)[2:]}")
    assert data['rev'][fy_idx] == pytest.approx(float(raw.loc['Total Revenue', raw.columns[0]]) / 1e9, rel=1e-3)
    newest = data['quarters'][-1]
    assert newest['period_end'] == q.columns[0].date().isoformat()
    assert newest['rev'] == pytest.approx(float(q.loc['Total Revenue', q.columns[0]]) / 1e9, rel=1e-3)


@pytest.mark.parametrize('t', ['JPM', 'ORCL', 'CRM', 'PG'])
def test_financials_not_stale(live_client, t):
    data = live_client.get(f'/api/financials/{t}').get_json()
    latest = date.fromisoformat(data['latest_period_end'])
    assert (date.today() - latest).days < 150, f'{t}: último trimestre {latest}'
    assert data['years'][-1] in ('TTM', f'FY{str(date.today().year)[2:]}', f'FY{str(date.today().year - 1)[2:]}')
    assert data['next_earnings_date'] is None or data['next_earnings_date'] >= date.today().isoformat()


def test_financials_etf_has_real_holdings(live_client):
    data = live_client.get('/api/financials/SPY').get_json()
    assert data['etf'] and data['segs_source'] == 'holdings' and len(data['segs']) >= 5
    assert dict(data['kpis'])['Expense Ratio'] != 'N/A'
    dy = dict(data['kpis'])['Div Yield']
    assert dy != 'N/A' and 0.3 < float(dy.rstrip('%')) < 3.0, f'SPY div yield fuera de rango: {dy}'


def test_financials_crypto_unsupported(live_client):
    assert live_client.get('/api/financials/SOL').get_json()['supported'] is False


def test_dividend_yield_plausible(live_client):
    dy = dict(live_client.get('/api/financials/PG').get_json()['kpis'])['Div Yield']
    assert dy != 'N/A' and 1.0 < float(dy.rstrip('%')) < 6.0, f'PG div yield fuera de rango: {dy}'


def test_probe_matches_full_download(live_client):
    """El sondeo liviano ve el mismo último trimestre que la descarga completa."""
    import financials
    data = live_client.get('/api/financials/JPM').get_json()
    assert financials.probe_newest_period('JPM') == data['latest_period_end']
    status = live_client.get('/api/financials/status?tickers=JPM,SOL').get_json()
    assert status['JPM']['state'] in ('fresh', 'pending', 'available')
    assert status['SOL']['state'] == 'unsupported'


@pytest.mark.parametrize('t', ['SPY', 'JPM', 'SOL'])
def test_ohlcv_live(live_client, t):
    data = live_client.get(f'/api/ohlcv/{t}/1d').get_json()
    assert len(data['candles']) > 100


def test_refresh_market_live(live_client):
    data = live_client.get('/api/refresh/market').get_json()
    assert data['ok'] and 'SPY' in data['quotes']


def test_coingecko_live():
    import server
    prices = server.fetch_coingecko_prices()
    assert prices.get('SOL', {}).get('price', 0) > 1000


# ── Modelo de riesgo (R1) con datos reales ──────────────────────────────────

@pytest.mark.parametrize('t', ['JPM', 'PG', 'CRM'])
def test_risk_stock_plausible(live_client, t):
    r = live_client.get(f'/api/risk/{t}').get_json()
    assert 0 <= r['risk_score'] <= 100 and r['coverage'] >= 0.8, (r['risk_score'], r['coverage'])
    m = {x['key']: x for p in r['pillars'] for x in p['metrics']}
    assert 5 < r['market']['volatility_pct'] < 120
    dte = m['debt_to_equity']['value']
    assert dte is None or 0 <= dte < 15, f'debtToEquity fuera de rango ({dte}): ¿Yahoo dejó de informarlo en %?'
    assert len(r['prices_12m']) >= 11
    assert any(e['type'] in ('earnings', 'quarter_end') for e in r['events'])


def test_earnings_report_dates_available():
    """Fechas reales de presentación (yfinance + pandas.read_html). Si falla: pip install lxml."""
    import yfinance as yf
    df = yf.Ticker('JPM').get_earnings_dates(limit=8)
    assert df is not None and not df.empty and 'Surprise(%)' in df.columns


def test_risk_low_vol_vs_high_vol(live_client):
    """Sanidad del modelo: PG (defensiva) debería tener menos riesgo de mercado que CRM."""
    def market(t):
        r = live_client.get(f'/api/risk/{t}').get_json()
        return next(p for p in r['pillars'] if p['key'] == 'market')['points']
    assert market('PG') < market('CRM')


def test_risk_etf_and_crypto(live_client):
    spy = live_client.get('/api/risk/SPY').get_json()
    assert [p['key'] for p in spy['pillars']] == ['cost', 'market']
    sol = live_client.get('/api/risk/SOL').get_json()
    assert sol['source_symbol'] == 'SOL-USD' and sol['market']['volatility_pct'] > 20


# ── Narrativa IA (R2) con Gemma real ────────────────────────────────────────

@pytest.mark.parametrize('lang', ['es', 'en'])
def test_gemma_narrative_validated(live_client, lang):
    """Llama a Gemma una vez por idioma. No exige que apruebe todo (es un modelo chico):
    exige que lo que se muestra haya pasado el validador y que lo descartado tenga motivo."""
    import financials
    import narrative
    import risk
    import server
    if not server.NVIDIA_API_KEY:
        pytest.skip('Sin NVIDIA_API_KEY en .env')
    rep = risk.get_risk('JPM')
    fp = financials.get_financials('JPM')
    call = lambda p: server._call_gemma(p, timeout=narrative.MODEL_TIMEOUT_S, enable_thinking=False)
    out = narrative.generate(rep, fp, lang, call, server.NVIDIA_MODEL)
    allowed = narrative.extract_numbers(narrative.build_facts(rep, fp))
    shown = {k: v for k, v in out['fields'].items() if v}
    print(f"\n[{lang}] aprobados: {sorted(shown)} · descartados: {out['unavailable']}")
    for k, v in shown.items():
        for txt in (v if isinstance(v, list) else [v]):
            assert narrative.check_text(txt, allowed, lang, 10_000)[1] is None
    assert set(out['unavailable']) | set(shown) == set(narrative.FIELDS)


def test_valuations_batch_refresh_live(live_client, monkeypatch):
    """Una sola descarga real de precios para varios activos (cripto con -USD), etiqueta igual al
    Reporte de Riesgo y P/E revaluado coherente con el precio de Yahoo. Mide el tiempo."""
    import time
    import valuations
    import yfinance as yf

    class Sync:
        def submit(self, fn, *a, **k):
            fn(*a, **k)
    monkeypatch.setattr(valuations, 'EXECUTOR', Sync())
    calls = []
    real = yf.download
    monkeypatch.setattr(valuations.yf, 'download', lambda syms, **k: calls.append(list(syms)) or real(syms, **k))
    tickers = ['JPM', 'PG', 'NU', 'SPY', 'SOL']
    t0 = time.time()
    assert live_client.post('/api/valuations/refresh', json={'tickers': tickers}).status_code == 202
    elapsed = time.time() - t0
    st = valuations.status()
    print(f'\n[valuations] {len(tickers)} activos en {elapsed:.1f} s · descargas de precios: {len(calls)}')
    assert len(calls) == 1 and 'SOL-USD' in calls[0] and 'SOL' not in calls[0]
    assert sorted(st['updated']) == sorted(tickers), st['failed']
    reg = live_client.get('/api/valuations?tickers=' + ','.join(tickers)).get_json()
    rows = {r['ticker']: r for r in reg['rows']}
    for t in ('JPM', 'PG'):
        rep = live_client.get(f'/api/risk/{t}').get_json()
        assert rows[t]['group'] == rep['valuation_label']
        info = yf.Ticker(t).info
        pe_now = info.get('trailingPE')
        if pe_now and rows[t]['metrics']['pe']['value']:
            # P/E revaluado con el último cierre vs P/E que Yahoo calcula ahora: deben coincidir de cerca
            assert rows[t]['metrics']['pe']['value'] == pytest.approx(pe_now, rel=0.05)
    assert rows['SPY']['reason'] == 'etf' and rows['SOL']['reason'] == 'crypto'


def test_explore_live(live_client, monkeypatch, tmp_path):
    """Modo Explorar contra Yahoo real: screener + cálculo propio de cada candidato. Mide el tiempo."""
    import explore
    monkeypatch.setattr(explore, 'PREFS_PATH', str(tmp_path / 'prefs.json'))
    out = live_client.post('/api/explore', json={'intent': 'dividends', 'sectors': ['Utilities', 'Consumer Defensive'],
                                                 'owned': ['PG']}).get_json()
    print(f"\n[explore] {out.get('screened')} resultados del screener · {len(out.get('candidates', []))} candidatos "
          f"· {out.get('seconds')} s · descartados {out.get('dropped')} · filtrados {out.get('skipped')}")
    assert out['ok'] is True and out['candidates'], out
    assert out['seconds'] < 60
    for c in out['candidates']:
        assert c['ticker'] != 'PG' and '.' not in c['ticker']
        assert c['sector'] in ('Utilities', 'Consumer Defensive')
        assert c['risk_score'] is not None and c['reasons']
        if c['dividend_yield'] is not None:
            assert 0 < c['dividend_yield'] < 25, f"dividendo fuera de escala: {c['ticker']} {c['dividend_yield']}"
    import json as _json
    assert 'averageAnalystRating' not in _json.dumps(out)
