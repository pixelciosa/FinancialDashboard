"""GOLDEN TEST of the Lab engine: demo ledger + frozen prices up to 2026-11-13.

DEMO EDITION: every expected value below was REGENERATED from FICTIONAL data, namely the demo
ledger (opening positions in sample_data/ledger.csv + 2025-2026 operations in
sample_data/investments_ledger.csv, spreadsheet-export format) and the synthetic demo-ticker prices in
tests/lab/fixtures/prices_2026-11-13.csv (both produced by scripts/generate_demo_data.py with a
fixed seed). They are not real portfolio figures. If you regenerate the demo data, re-derive them.
No network, no writes to data/. Tolerance: 0.1 pp on percentages and 0.5% on dollars."""
import os

import pandas as pd
import pytest

from ._helpers import FIXTURES, load_lab

M = load_lab()
ledger, prices, engine = M['ledger'], M['prices'], M['engine']

FIXTURE = os.path.join(FIXTURES, 'prices_2026-11-13.csv')


def _ledger_available():
    """The demo ledger (sample_data/ledger.csv + investments_ledger.csv, fictional) ships with the repo."""
    return os.path.exists(ledger.LEDGER_PATH)


pytestmark = pytest.mark.skipif(
    not (os.path.exists(FIXTURE) and _ledger_available()),
    reason='demo ledger or frozen price fixture missing')


@pytest.fixture(scope='module')
def golden():
    ops = prices.to_price_ops(ledger.load_ledger()['ops'])
    df = pd.read_csv(FIXTURE, index_col=0, parse_dates=True)
    dates, px = prices.align(df)
    return engine.analyze(dates, px, ops, prices.INDEX_TICKERS)


def pp(x):
    return x * 100


def test_golden_dollars(golden):
    p = golden['port']
    assert p['dates'][0] == '2025-01-02' and p['dates'][-1] == '2026-11-13'
    assert p['v0'] == pytest.approx(10765, rel=0.005)
    assert p['v_end'] == pytest.approx(41739, rel=0.005)
    assert p['net_flows'] == pytest.approx(20328, rel=0.005)
    assert golden['mw']['SPY'] == pytest.approx(39426, rel=0.005)


def test_golden_percentages(golden):
    st = golden['series']['PORT']['stats']
    assert pp(st['cum']) == pytest.approx(37.08, abs=0.1)
    assert pp(st['ann']) == pytest.approx(18.51, abs=0.1)
    assert pp(st['vol']) == pytest.approx(19.75, abs=0.1)
    assert pp(st['max_dd']) == pytest.approx(-25.98, abs=0.1)


def test_golden_calendar_years(golden):
    p = golden['port']
    yr = {r['periodo']: r['retorno'] for r in engine.period_returns(p['dates'], p['index'], 4)}
    assert pp(yr['2025']) == pytest.approx(10.5, abs=0.1)
    assert pp(yr['2026']) == pytest.approx(24.06, abs=0.1)


def test_golden_no_missing_prices_in_held_assets(golden):
    assert not [w for w in golden['port']['warnings'] if w['tipo'] == 'sin_precio']
    # el fixture dorado es anterior al indice del dolar (DX-Y.NYB, Fase 4): unico faltante admitido
    assert [m for m in golden['missing'] if m != 'DX-Y.NYB'] == []
