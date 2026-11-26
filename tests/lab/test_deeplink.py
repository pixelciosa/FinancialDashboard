"""Contrato del tercer toque permitido a index.html: deep link /?ticker=XXX.

El deep link vive en pickInitialTicker(), que init() usa para elegir el ticker inicial."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _block(signature, size=2500):
    txt = (ROOT / "index.html").read_text(encoding="utf-8")
    start = txt.index(signature)
    return txt[start:start + size]


def _pick_block():
    return _block("function pickInitialTicker()", 1200)


def test_init_reads_ticker_query_param():
    assert "URLSearchParams(location.search).get('ticker')" in _pick_block()


def test_deeplink_only_accepts_known_tickers():
    block = _pick_block()
    # solo un ticker presente en los metadatos cargados (y no oculto por el usuario)
    assert "if (known(qTicker)) return qTicker;" in block
    # el deep link tiene prioridad sobre el último ticker guardado
    assert block.index("known(qTicker)") < block.index("localStorage.getItem('fdDemo.activeTicker')")


def test_initial_ticker_is_chosen_before_rendering_sidebar():
    init = _block("async function init()")
    assert init.index("state.activeTicker = pickInitialTicker()") < init.index("renderSidebar()")
