"""
Regresión de endpoints del backend que ya funcionaban.

Todos corren offline: yfinance se reemplaza por FakeTicker.
"""
import pytest

from fakes import FakeTicker, ETF_INFO, STOCK_INFO, hourly_history, monthly_history


# ── Caché de cotizaciones ─────────────────────────────────────────────────────

def test_quote_cache_ttl(server_module):
    server_module.put_cached_quote('TEST_TTL', {'price': 100.0, 'chg': 1.0, 'chg_pct': 1.0})
    assert server_module.get_cached_quote('TEST_TTL', max_age_seconds=10)['price'] == 100.0
    assert server_module.get_cached_quote('TEST_TTL', max_age_seconds=0) is None


def test_ohlcv_cache_respects_interval_ttl(server_module, monkeypatch):
    server_module.put_cached_ohlcv('AAA', '1d', {'candles': [1]})
    assert server_module.get_cached_ohlcv('AAA', '1d') == {'candles': [1]}
    assert server_module.get_cached_ohlcv('AAA', '1d', max_age_seconds=0) is None


# ── Regla 4: la variación del día faltante es null, no 0 ─────────────────────

NO_CHANGE = {k: v for k, v in STOCK_INFO.items() if k not in ('regularMarketChange', 'regularMarketChangePercent')}


def test_quote_missing_change_is_null_not_zero(client, fake_yf):
    fake_yf['NOCHG'] = FakeTicker('NOCHG', info=dict(NO_CHANGE))
    q = client.get('/api/quotes?tickers=NOCHG').get_json()['quotes']['NOCHG']
    assert q['price'] == 200.0 and q['chg'] is None and q['chg_pct'] is None


def test_quote_real_zero_change_is_kept(client, fake_yf):
    fake_yf['FLAT'] = FakeTicker('FLAT', info={**STOCK_INFO, 'regularMarketChange': 0.0, 'regularMarketChangePercent': 0.0})
    q = client.get('/api/quotes?tickers=FLAT').get_json()['quotes']['FLAT']
    assert q['chg'] == 0.0 and q['chg_pct'] == 0.0


def test_refresh_market_keeps_null_and_zero_apart(client, fake_yf, server_module, monkeypatch):
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices', lambda: {})
    monkeypatch.setattr(server_module, 'ACTIVE_TICKERS', ['NOCHG', 'FLAT'])
    fake_yf['NOCHG'] = FakeTicker('NOCHG', info=dict(NO_CHANGE))
    fake_yf['FLAT'] = FakeTicker('FLAT', info={**STOCK_INFO, 'regularMarketChange': 0.0, 'regularMarketChangePercent': 0.0})
    quotes = client.get('/api/refresh/market').get_json()['quotes']
    assert quotes['NOCHG']['chg'] is None and quotes['NOCHG']['chg_pct'] is None
    assert quotes['FLAT']['chg'] == 0.0 and quotes['FLAT']['chg_pct'] == 0.0


@pytest.mark.parametrize('v,expected', [(None, None), ('', None), ('abc', None), (float('nan'), None),
                                        (float('inf'), None), (0, 0.0), ('1.5', 1.5), (-2.25, -2.25)])
def test_num_or_none(server_module, v, expected):
    assert server_module._num_or_none(v) == expected


# ── Endpoints básicos ────────────────────────────────────────────────────────

def test_api_meta(client):
    res = client.get('/api/meta')
    assert res.status_code == 200
    data = res.get_json()
    assert 'VTI' in data and 'JPM' in data          # demo portfolio (sample_data/portfolio.csv)
    assert data['DIS']['category'] == 'watching' and data['INTC']['category'] == 'archived'
    for sym, meta in data.items():
        assert meta.get('category') in ('portfolio', 'watching', 'archived'), sym


def test_api_quotes_uses_cache(client, server_module):
    server_module.put_cached_quote('ORCL', {'price': 425.0, 'chg': 5.0, 'chg_pct': 1.19})
    data = client.get('/api/quotes?tickers=ORCL').get_json()
    assert data['quotes']['ORCL']['price'] == 425.0


def test_api_quotes_fetches_with_fake(client, fake_yf):
    data = client.get('/api/quotes?tickers=ACME').get_json()
    assert data['quotes']['ACME']['price'] == 200.0


def test_api_refresh_market_offline(client, fake_yf, server_module, monkeypatch):
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices', lambda: {})
    data = client.get('/api/refresh/market').get_json()
    assert data['ok'] is True
    assert 'VTI' in data['quotes']
    assert data['quotes']['VTI']['price'] == 200.0


# ── Validación de tickers ────────────────────────────────────────────────────

def test_ticker_validation_known_and_bad_format(server_module, client):
    assert server_module._validate_ticker('JPM') == (True, None)
    ok, err = server_module._validate_ticker('INVALID_SYMBOL_12345')
    assert ok is False and err
    res = client.get('/api/validate/INVALID_SYMBOL_12345')
    assert res.status_code == 400 and res.get_json()['ok'] is False


@pytest.mark.parametrize('sym,expected', [
    ('JPM', True), ('SOL', True), ('TOTAL', False), ('PESOS', False),
    ('TOO LONG', False), ('', False), ('ABCDEFG', False), ('A-B', False),
])
def test_is_valid_ticker_symbol(server_module, sym, expected):
    assert server_module._is_valid_ticker_symbol(sym) is expected


# ── R4: generador de reportes viejo retirado ────────────────────────────────

RETIRED_ROUTES = ['/api/generate-report/<ticker>', '/api/report/<ticker>', '/api/reports',
                  '/api/refresh/risk', '/api/refresh/risk/status', '/api/compare']


def test_retired_routes_are_gone(server_module, client):
    rules = {r.rule for r in server_module.app.url_map.iter_rules()}
    assert not rules & set(RETIRED_ROUTES), rules & set(RETIRED_ROUTES)
    for name in ('_yf_fundamentals', '_build_report', '_calculate_fallback_score', '_async_generate_report',
                 'report_executor', 'GENERATING_TICKERS', 'REPORTS_DIR', 'RISK_REFRESH_STATUS',
                 '_portfolio_context_rows'):
        assert not hasattr(server_module, name), name
    assert client.post('/api/generate-report/JPM').status_code == 404
    assert client.get('/api/reports').status_code == 404


def test_risk_tab_endpoints_still_work(client, fake_yf):
    """Lo que reemplazó al generador viejo: riesgo por ticker y resumen para el sidebar."""
    assert client.get('/api/risk/ACME').status_code == 200
    assert 'ACME' in client.get('/api/risk/summary?tickers=ACME').get_json()


# ── OHLCV / análisis técnico ─────────────────────────────────────────────────

def test_ohlcv_reports_when_candles_were_fetched(client, fake_yf):
    """fetched_at alimenta la píldora de estado de Technical Analysis; la caché conserva la hora original."""
    from datetime import datetime
    fake_yf['ACME'] = FakeTicker('ACME', history=monthly_history(periods=30))
    first = client.get('/api/ohlcv/ACME/1d').get_json()
    assert datetime.fromisoformat(first['fetched_at']).tzinfo is not None
    again = client.get('/api/ohlcv/ACME/1d').get_json()
    assert again['fetched_at'] == first['fetched_at']


def test_ohlcv_daily_shape_and_ema(client, fake_yf):
    fake_yf['ACME'] = FakeTicker('ACME', history=monthly_history(periods=30))
    data = client.get('/api/ohlcv/ACME/1d').get_json()
    candles = data['candles']
    assert len(candles) == 30
    for c in candles:
        assert set(c) >= {'time', 'open', 'high', 'low', 'close', 'volume', 'ema20'}
        assert c['low'] <= c['close'] <= c['high']
    assert [c['ema20'] for c in candles[:19]] == [None] * 19
    assert candles[19]['ema20'] == pytest.approx(sum(c['close'] for c in candles[:20]) / 20, rel=1e-6)
    times = [c['time'] for c in candles]
    assert times == sorted(times), 'Las velas deben venir en orden cronológico'


def test_ohlcv_4h_resample(client, fake_yf):
    fake_yf['ACME'] = FakeTicker('ACME', history=hourly_history(hours=8))
    candles = client.get('/api/ohlcv/ACME/4h').get_json()['candles']
    assert len(candles) == 2
    first = candles[0]
    assert first['open'] == 100 and first['close'] == 103.25
    assert first['high'] == 103.5 and first['low'] == 99.5 and first['volume'] == 40


def test_ohlcv_rejects_bad_input(client):
    assert client.get('/api/ohlcv/ACME/5m').status_code == 400
    assert client.get('/api/ohlcv/<script>/1d').status_code in (400, 404)


def test_ohlcv_crypto_uses_usd_suffix(client, fake_yf, server_module):
    seen = []
    original = server_module.yf.Ticker

    def spy(symbol, *a, **k):
        seen.append(symbol)
        return original(symbol)

    server_module.yf.Ticker = spy
    try:
        client.get('/api/ohlcv/SOL/1d')
    finally:
        server_module.yf.Ticker = original
    assert seen == ['SOL-USD']


# ── ETF en el endpoint financiero ────────────────────────────────────────────

def test_financials_etf_branch(client, fake_yf):
    fake_yf['FETF'] = FakeTicker('FETF', info=dict(ETF_INFO), history=monthly_history(start='2021-01-01', periods=60))
    data = client.get('/api/financials/FETF').get_json()
    assert data['etf'] is True
    # 2021 no tiene cierre del año anterior: se descarta en vez de mostrar un año parcial
    assert data['years'] == ['FY22', 'FY23', 'FY24', 'FY25']
    assert data['annualReturns'][0] == pytest.approx(round((1.01 ** 12 - 1) * 100, 1))


def test_portfolio_exposes_crypto_symbols_from_server_source(client, server_module, monkeypatch):
    # demo edition: load_portfolio_from_csv rebuilds COINGECKO_IDS from the CSV; stub it (and the
    # CoinGecko call) so the test stays offline and sees the patched registry
    monkeypatch.setattr(server_module, 'load_portfolio_from_csv', lambda *a, **k: {})
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices', lambda *a, **k: {})
    res = client.get('/api/portfolio')
    assert res.status_code == 200
    assert res.get_json()['cryptoSymbols'] == sorted(set(server_module.COINGECKO_IDS) | set(server_module.CRYPTO_YF_SUFFIX))


def test_portfolio_crypto_symbols_include_coingecko_only(client, server_module, monkeypatch):
    # demo edition: load_portfolio_from_csv rebuilds COINGECKO_IDS from the CSV; stub it (and the
    # CoinGecko call) so the test stays offline and sees the patched registry
    monkeypatch.setattr(server_module, 'load_portfolio_from_csv', lambda *a, **k: {})
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices', lambda *a, **k: {})
    monkeypatch.setitem(server_module.COINGECKO_IDS, 'ZZZONLYCG', 'zzz-only-cg')
    assert 'ZZZONLYCG' not in server_module.CRYPTO_YF_SUFFIX
    syms = client.get('/api/portfolio').get_json()['cryptoSymbols']
    assert 'ZZZONLYCG' in syms
    assert syms == sorted(syms) and all(isinstance(x, str) for x in syms)


@pytest.mark.parametrize('sym', ['USD_CASH', 'ARS', 'EUR'])
def test_portfolio_cash_pnl_is_null_not_zero(client, server_module, monkeypatch, sym):
    # demo edition: cash rows come from CASH_CSV with the 'cash' flag (USD_CASH + ISO codes)
    monkeypatch.setattr(server_module, 'load_portfolio_from_csv',
                        lambda *a, **k: {sym: {'currentValue': 100.37, 'cash': True, 'currency': sym[:3]}})
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices', lambda *a, **k: {})
    data = client.get('/api/portfolio').get_json()['data'][sym]
    assert data['unrealizedPnl'] is None
    assert data['unrealizedPnlPct'] is None
    assert data['changePct'] is None and data['change'] is None and data['currentPrice'] is None


def _portfolio_with_failing_yf(client, server_module, monkeypatch, pos):
    class Boom:
        def __init__(self, *a, **k):
            pass

        @property
        def info(self):
            raise RuntimeError('yahoo caido')

    monkeypatch.setattr(server_module, 'load_portfolio_from_csv', lambda *a, **k: {'ZZERR': pos})
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices', lambda *a, **k: {})
    monkeypatch.setattr(server_module.yf, 'Ticker', Boom)
    resp = client.get('/api/portfolio')
    return resp, resp.get_json()['data']['ZZERR']


def test_portfolio_fetch_error_branch_emits_null_not_zero(client, server_module, monkeypatch):
    pos = {'shares': 3.5, 'costBasis': 412.37}
    resp, row = _portfolio_with_failing_yf(client, server_module, monkeypatch, pos)
    assert 'yahoo caido' in row['error']
    # lo que viene del CSV se conserva (demo: sin precio, el valor se muestra al costo)
    assert row['shares'] == 3.5 and row['costBasis'] == 412.37 and row['currentValue'] == 412.37
    # lo que dependia del precio en vivo es null (clave presente), nunca 0
    for k in ('currentPrice', 'change', 'changePct'):
        assert k in row and row[k] is None, k
    for k in ('unrealizedPnl', 'unrealizedPnlPct'):
        assert k in row and row[k] is None, k
    body = resp.get_data(as_text=True)
    assert 'NaN' not in body and 'Infinity' not in body


def test_portfolio_fetch_error_branch_without_csv_values_is_null(client, server_module, monkeypatch):
    resp, row = _portfolio_with_failing_yf(client, server_module, monkeypatch,
                                           {'shares': 2.25, 'costBasis': None})
    assert row['costBasis'] is None and row['currentValue'] is None
    assert row['unrealizedPnl'] is None and row['unrealizedPnlPct'] is None
    assert 'NaN' not in resp.get_data(as_text=True)


def test_portfolio_prompt_missing_value_is_na_not_zero():
    import server
    lines, total = server._portfolio_prompt_lines({
        'AAA': {'shares': 1, 'costBasis': 100.0}, 'BBB': {'shares': 2}, 'CCC': {'currentValue': 50.0}})
    assert total == 150.0
    assert 'value: n/a' in lines[1] and 'value: $0' not in ''.join(lines)
