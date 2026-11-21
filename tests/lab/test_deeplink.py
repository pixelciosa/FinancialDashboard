"""Contrato del tercer toque permitido a index.html: deep link /?ticker=XXX."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _init_block():
    txt = (ROOT / "index.html").read_text(encoding="utf-8")
    start = txt.index("async function init()")
    return txt[start:start + 2500]


def test_init_reads_ticker_query_param():
    block = _init_block()
    assert "URLSearchParams(location.search).get('ticker')" in block


def test_deeplink_only_accepts_known_tickers():
    block = _init_block()
    assert "state.tickerMeta[qTicker]" in block
    # debe aplicarse antes de renderizar el sidebar
    assert block.index("qTicker") < block.index("renderSidebar()")
