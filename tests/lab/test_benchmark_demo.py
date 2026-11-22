"""Benchmark comparison ("Benchmark index" / "Period" toolbar of the Lab) on the FICTIONAL demo
portfolio: every index the Lab offers (prices.INDEX_TICKERS) must have frozen prices in the test
fixtures and give a complete comparison offline. Index/benchmark closes are public market data;
the demo holdings use synthetic prices (scripts/generate_demo_data.py). The SPY figures below were
regenerated from the fictional demo ledger (sample_data/ledger.csv + investments_ledger.csv)."""
import json
import os

import pandas as pd
import pytest
from flask import Flask

from ._helpers import FIXTURES, load_lab

M = load_lab()
api, ledger, prices = M['api'], M['ledger'], M['prices']

MAIN = os.path.join(FIXTURES, 'prices_2026-11-13.csv')
DXY = os.path.join(FIXTURES, 'dxy_2026-11-13.csv')

pytestmark = pytest.mark.skipif(not (os.path.exists(MAIN) and os.path.exists(DXY) and os.path.exists(ledger.LEDGER_PATH)),
                                reason='demo ledger or frozen fixtures missing')


@pytest.fixture(scope='module')
def inputs():
    df = pd.read_csv(MAIN, index_col=0, parse_dates=True)
    df = df.join(pd.read_csv(DXY, index_col=0, parse_dates=True), how='outer')
    dates, px = prices.align(df)
    return dates, px, prices.to_price_ops(ledger.load_ledger()['ops']), df


@pytest.fixture
def client(inputs, monkeypatch):
    dates, px, ops, _ = inputs
    monkeypatch.setattr(api, '_load_inputs', lambda: (dates, px, ops))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    return app.test_client()


def test_every_offered_index_has_fixture_prices(inputs):
    df = inputs[3]
    missing = [t for t in prices.INDEX_TICKERS if t not in df.columns or df[t].dropna().empty]
    assert missing == []


@pytest.mark.parametrize('bench', prices.INDEX_TICKERS)
def test_performance_against_each_index_is_complete(client, bench):
    r = client.get(f'/api/lab/performance?benchmark={bench}')
    d = json.loads(r.get_data(as_text=True))            # strict JSON: no NaN/Infinity
    assert r.status_code == 200 and d['ok'] and d['status'] == 'ok' and d['benchmark'] == bench
    assert d['desde'] == '2025-01-02' and d['hasta'] == '2026-11-13'
    n = len(d['dates'])
    for side in ('portfolio', 'benchmark'):
        assert len(d['series'][side]['index']) == n and d['stats'][side]['cum_pct'] is not None
        assert d['yearly'][side] and d['monthly'][side]
    assert d['mismos_aportes']['valor_final'] is not None


def test_spy_comparison_golden(client):
    d = client.get('/api/lab/performance?benchmark=SPY').get_json()
    assert d['cash']['valor_final'] == pytest.approx(41738.5, abs=1)
    assert d['mismos_aportes']['valor_final'] == pytest.approx(39425.6, abs=1)
    assert d['mismos_aportes']['diferencia_vs_cartera'] == pytest.approx(39425.6 - 41738.5, abs=1)
    assert d['stats']['portfolio']['cum_pct'] == pytest.approx(37.08, abs=0.1)


def test_crypto_alias_and_invalid_benchmark(client):
    assert client.get('/api/lab/performance?benchmark=BTC').get_json()['benchmark'] == 'BTC-USD'
    r = client.get('/api/lab/performance?benchmark=NOPE')
    assert r.status_code == 400 and r.get_json()['codigo'] == 'benchmark_invalido'
