"""Portfolio input files (portfolio_data.py) and the /api/portfolio endpoint built on them.
Offline: the demo files in sample_data/ are fictional; other cases use temporary CSVs."""
import json

import pytest

import portfolio_data as P


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding='utf-8')
    return str(p)


def test_demo_portfolio_loads_without_errors():
    pf = P.load_portfolio()
    assert pf['errors'] == []
    tickers = [r['ticker'] for r in pf['rows']]
    assert len(tickers) == 13 and len(set(tickers)) == 13
    types = {r['meta']['type'] for r in pf['rows']}
    assert types == {'Stock', 'ETF', 'Crypto'}
    assert all(r['quantity'] > 0 and r['cost_basis_usd'] > 0 for r in pf['rows'])


def test_ticker_meta_merges_portfolio_and_watchlist():
    meta = P.build_ticker_meta()
    assert meta['JPM']['category'] == 'portfolio' and meta['JPM']['sector'] == 'Financials'
    assert meta['DIS']['category'] == 'watching' and meta['INTC']['category'] == 'archived'
    assert {m['category'] for m in meta.values()} <= {'portfolio', 'watching', 'archived'}


def test_crypto_rows_get_coingecko_ids_and_usd_suffix_set():
    ids = P.coingecko_ids()
    assert ids == {'SOL': 'solana', 'ADA': 'cardano'}
    assert {'SOL', 'ADA'} <= P.crypto_tickers()


def test_cash_is_converted_to_usd_with_generic_keys():
    cash = P.load_cash()
    assert set(cash) == {'USD_CASH', 'ARS', 'EUR'}
    assert cash['USD_CASH']['currentValue'] == 2500.0
    assert cash['ARS']['currentValue'] == pytest.approx(1500000 * 0.00069, abs=0.01)
    assert all(v['cash'] for v in cash.values())


def test_bad_rows_are_reported_and_skipped(tmp_path):
    path = _write(tmp_path, 'pf.csv', 'ticker,name,type,quantity,cost_basis_usd\n'
                  'GOOD,Good Co,Stock,2,100\n'
                  'bad ticker!,X,Stock,1,1\n'
                  'NOQTY,No qty,Stock,,1\n'
                  'GOOD,Dup,Stock,1,1\n'
                  'ODD,Odd,Bond,1,1\n'
                  'NOCOST,No cost,ETF,3,\n')
    pf = P.load_portfolio(path)
    assert [r['ticker'] for r in pf['rows']] == ['GOOD', 'NOCOST']
    assert pf['rows'][1]['cost_basis_usd'] is None            # missing is None, never 0
    assert len(pf['errors']) == 4 and all(e['row'] for e in pf['errors'])


def test_unknown_ticker_gets_defaults_and_optional_columns(tmp_path):
    path = _write(tmp_path, 'pf.csv', 'ticker,name,type,quantity,cost_basis_usd,sector,country,coingecko_id\n'
                  'ZZZZ,,Stock,1,10,,,\n'
                  'NEWC,New Coin,crypto,5,10,,,new-coin\n')
    pf = P.load_portfolio(path)
    meta = P.build_ticker_meta(pf, {'rows': [], 'errors': []})
    assert meta['ZZZZ'] == {'name': 'ZZZZ', 'type': 'Stock', 'country': 'USA', 'sector': 'Other',
                            'category': 'portfolio'}
    assert meta['NEWC']['type'] == 'Crypto' and P.coingecko_ids(pf, {'rows': [], 'errors': []}) == {'NEWC': 'new-coin'}


def test_paths_come_from_env(tmp_path, monkeypatch):
    path = _write(tmp_path, 'mine.csv', 'ticker,name,type,quantity,cost_basis_usd\nABC,Abc,Stock,1,5\n')
    monkeypatch.setenv('PORTFOLIO_CSV', path)
    assert [r['ticker'] for r in P.load_portfolio()['rows']] == ['ABC']
    monkeypatch.setenv('PORTFOLIO_CSV', str(tmp_path / 'missing.csv'))
    pf = P.load_portfolio()
    assert pf['rows'] == [] and pf['errors'][0]['error'] == 'file not found'


def test_portfolio_endpoint_uses_csv_positions_and_cash(client, fake_yf, server_module, monkeypatch):
    monkeypatch.setattr(server_module, 'fetch_coingecko_prices',
                        lambda: {'SOL': {'price': 150.0, 'change_pct': 1.0, 'change': 1.5}})
    d = json.loads(client.get('/api/portfolio').get_data(as_text=True))
    assert d['ok']
    data = d['data']
    pf = {r['ticker']: r for r in P.load_portfolio()['rows']}
    assert set(pf) <= set(data)
    assert data['JPM']['shares'] == pf['JPM']['quantity']
    assert data['JPM']['currentValue'] == pytest.approx(200.0 * pf['JPM']['quantity'])   # fake price 200
    assert data['JPM']['costBasis'] == pytest.approx(pf['JPM']['cost_basis_usd'])
    assert data['SOL']['currentPrice'] == 150.0 and data['SOL']['type'] == 'Crypto'
    assert data['USD_CASH']['type'] == 'Cash' and data['USD_CASH']['currentValue'] == 2500.0
    assert data['ARS']['currency'] == 'ARS'
    assert 'INTC' not in data and 'DIS' not in data            # archived / watching are not holdings


def test_portfolio_analysis_without_ai_is_an_error_not_canned_text(client, server_module, monkeypatch):
    monkeypatch.setattr(server_module, '_call_gemma', lambda *a, **k: None)
    r = client.post('/api/portfolio/analysis', json={'lang': 'en'})
    d = r.get_json()
    assert r.status_code == 503 and d['ok'] is False and 'analysis' not in d
