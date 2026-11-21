"""
Identidad del activo: cada endpoint debe consultar el símbolo correcto en la fuente.
Un error acá no tira excepción: muestra datos verosímiles de OTRO activo (el peor
tipo de imprecisión, porque nadie lo nota).
"""
import pytest


@pytest.fixture
def symbol_spy(monkeypatch, server_module):
    from fakes import FakeTicker
    seen = []

    def factory(symbol, *a, **k):
        seen.append(symbol)
        return FakeTicker(symbol)

    monkeypatch.setattr(server_module.yf, 'Ticker', factory)
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices', lambda: {})
    return seen


@pytest.mark.parametrize('coin', ['SOL', 'ADA', 'DOGE'])
def test_quotes_never_query_bare_crypto_symbol(client, symbol_spy, coin):
    """Las cotizaciones cripto van por CoinGecko; si tocan yfinance, debe ser con -USD."""
    client.get(f'/api/quotes?tickers={coin}')
    assert coin not in symbol_spy, f'Se consultó "{coin}" en yfinance (es otro activo)'


@pytest.mark.parametrize('coin', ['SOL', 'ADA'])
def test_ohlcv_uses_usd_suffix_for_crypto(client, symbol_spy, coin):
    client.get(f'/api/ohlcv/{coin}/1d')
    assert symbol_spy == [f'{coin}-USD']


def test_stock_symbol_untouched(client, symbol_spy):
    client.get('/api/ohlcv/JPM/1d')
    assert symbol_spy == ['JPM']


@pytest.mark.parametrize('coin', ['SOL', 'ADA'])
def test_risk_report_uses_usd_suffix_for_crypto(client, symbol_spy, coin):
    """El Reporte de Riesgo (risk.py) consulta la cripto y no el ETF homónimo. El generador viejo
    que consultaba "SOL" se retiró en la R4."""
    client.get(f'/api/risk/{coin}')
    assert symbol_spy and all(s == f'{coin}-USD' for s in symbol_spy), symbol_spy


def test_financials_does_not_query_wrong_asset_for_crypto(client, symbol_spy):
    data = client.get('/api/financials/SOL').get_json()
    assert symbol_spy == [] or data.get('supported') is False
