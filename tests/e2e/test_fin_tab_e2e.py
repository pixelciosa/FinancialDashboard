"""
E2E de la tab Análisis Financiero en Chromium headless, con el servidor real y yfinance simulado.

Opcional (no corre por defecto). Requiere Playwright:
    venv/bin/pip install playwright && venv/bin/python -m playwright install chromium
    venv/bin/python -m pytest -m e2e
Guarda capturas en tests/e2e/shots/ para revisión visual.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.e2e
sync_api = pytest.importorskip('playwright.sync_api')

SHOTS = os.path.join(os.path.dirname(__file__), 'shots')
PORT = 5097
LWC_STUB = ("(function(){const P=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?(()=>0):"
            "(k==='then'?undefined:P),apply:()=>P,construct:()=>P});window.LightweightCharts=P;})();")


@pytest.fixture(scope='module')
def app_url(tmp_path_factory):
    import json
    import financials as fin
    import narrative
    import risk
    import server
    from fakes import ETF_INFO, STOCK_INFO, FakeTicker, monthly_history

    tmp = tmp_path_factory.mktemp('e2e')
    fin.CACHE_DIR = str(tmp / 'fin')
    risk.CACHE_DIR = str(tmp / 'risk')
    narrative.CACHE_DIR = str(tmp / 'narr')
    # Gemma simulada: 3 campos válidos y una conclusión con una cifra inventada (67.8%)
    server.NVIDIA_API_KEY = 'fake'
    server._call_gemma = lambda prompt, **kw: json.dumps({
        'update': 'En el Q3 2026 los ingresos fueron de 110B USD, un 7.8% más que un año antes.',
        'catalysts': ['El margen neto se mantiene en torno al 25%.'],
        'risks': ['La deuda equivale a 1.5 veces el patrimonio.'],
        'bottom_line': 'El margen bruto de 67.8% es el mejor de la industria.'})
    server.fetch_coingecko_prices = lambda: {}
    reg = {
        'JPM': FakeTicker('JPM', info={**STOCK_INFO, 'longName': 'JPMorgan Chase & Co.'}),
        'ORCL': FakeTicker('ORCL', info={**STOCK_INFO, 'longName': 'Oracle Corporation'}),
        'NU': FakeTicker('NU', info={**STOCK_INFO, 'longName': 'NU S.A.', 'financialCurrency': 'ARS'}),
        'VTI': FakeTicker('VTI', info={**ETF_INFO, 'longName': 'Vanguard Total Stock Market ETF'},
                          history=monthly_history(start='2020-01-01', periods=82)),
    }
    server.yf.Ticker = lambda sym, *a, **k: reg.get(sym) or FakeTicker(sym)

    # ORCL: el reporte venció y la fuente ya publicó un trimestre nuevo
    fin.fetch_and_store('ORCL')
    e = fin.read_cache('ORCL')
    e.update({'latest_period_end': '2026-06-30', 'newest_available_period_end': '2026-09-30',
              'next_earnings_date': (datetime.now(timezone.utc).date() - timedelta(days=8)).isoformat()})
    fin.write_cache('ORCL', e)

    threading.Thread(target=lambda: server.app.run(port=PORT, debug=False, use_reloader=False), daemon=True).start()
    time.sleep(1.2)
    return f'http://127.0.0.1:{PORT}/'


@pytest.fixture(scope='module')
def page(app_url):
    os.makedirs(SHOTS, exist_ok=True)
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={'width': 1360, 'height': 1750}, device_scale_factor=2)   # como un Mac Retina
        pg.errors = []
        pg.route('**/lightweight-charts*', lambda r: r.fulfill(body=LWC_STUB, content_type='application/javascript'))
        pg.route('**/fonts.g*/**', lambda r: r.abort())
        pg.on('pageerror', lambda e: pg.errors.append(str(e)))
        pg.on('response', lambda r: pg.errors.append(f'HTTP {r.status} {r.url}') if r.status >= 500 else None)
        pg.add_init_script("try{localStorage.setItem('lang','es');localStorage.setItem('finView','annual')}catch(e){}")
        pg.goto(app_url)
        pg.wait_for_timeout(1200)
        yield pg
        browser.close()


def open_fin(page, sym, shot=None):
    page.evaluate(f"selectTicker('{sym}'); switchTab('financial');")
    page.wait_for_timeout(800)
    if shot:
        page.screenshot(path=os.path.join(SHOTS, shot + '.png'))


def test_stock_renders_real_data(page):
    open_fin(page, 'JPM', '1_aapl')
    html = page.inner_html('#dashContent')
    assert 'TTM' in html and 'Q3 2026' in html
    assert page.locator('.fin-qtbl tbody tr').count() == 8
    assert page.locator('.fin-badge.fin-beat').count() >= 1
    assert page.evaluate("document.getElementById('btnRefreshSingleFin').disabled") is True


def test_quarterly_toggle(page):
    open_fin(page, 'JPM')
    page.click('.fin-toggle button:nth-child(2)')
    page.wait_for_timeout(500)
    assert page.evaluate("localStorage.getItem('finView')") == 'quarterly'
    page.screenshot(path=os.path.join(SHOTS, '2_aapl_quarterly.png'))
    page.click('.fin-toggle button:nth-child(1)')


def test_refresh_enabled_only_with_new_quarter(page):
    open_fin(page, 'ORCL', '3_msft_available')
    btn = '#btnRefreshSingleFin'
    assert page.evaluate(f"document.querySelector('{btn}').disabled") is False
    page.click(btn)
    page.wait_for_timeout(1200)
    state = page.evaluate("[FD['ORCL'].latest_period_end, FD['ORCL'].freshness.state]")
    assert state == ['2026-09-30', 'fresh']
    assert page.evaluate(f"document.querySelector('{btn}').disabled") is True


def test_etf_crypto_and_foreign_currency(page):
    open_fin(page, 'VTI', '4_vti')
    assert page.locator('.fin-sector-row').count() == 3
    open_fin(page, 'SOL', '5_btc')
    assert page.locator('.fin-qtbl').count() == 0 and page.locator('.fin-note').count() == 1
    open_fin(page, 'NU', '6_ypf')
    assert 'ARS' in page.inner_text('.fin-kpi-row')


def test_menu_counts_new_quarters(page):
    page.evaluate("toggleRefreshDropdown()")
    page.wait_for_timeout(500)
    assert page.evaluate("document.getElementById('btnRefreshFinancialAll').disabled") is True
    page.evaluate("closeRefreshDropdown()")


def test_charts_keep_size_on_hover(page):
    open_fin(page, 'JPM')
    for cid in ('c-margins', 'c-rev', 'c-eps'):
        box = page.locator('#' + cid).bounding_box()
        for i in range(6):
            page.mouse.move(box['x'] + 40 + i * 50, box['y'] + box['height'] - 40)
            page.wait_for_timeout(60)
        h = page.evaluate(f"document.getElementById('{cid}').style.height")
        assert h in ('180px', '160px'), f'{cid} creció a {h} con el hover'


# ── Reporte de Riesgo (R3) ──────────────────────────────────────────────────

def open_risk(page, sym, shot=None):
    page.evaluate(f"selectTicker('{sym}'); switchTab('risk');")
    page.wait_for_timeout(1000)
    if shot:
        page.screenshot(path=os.path.join(SHOTS, shot + '.png'))


def test_risk_overview_from_model(page):
    open_risk(page, 'JPM', '7_risk_overview')
    assert page.locator('#riskBody .mcard').count() == 13, 'Acción: 4 + 5 + 4 métricas'
    score = page.evaluate("RISK['JPM'].risk_score")
    assert str(score) in page.inner_text('.gauge-card')
    html = page.inner_html('#riskBody')
    for stale in ('May 25', 'Segments', 'STRONG BUY', 'Apr 2026'):
        assert stale not in html, f'Quedó contenido viejo: {stale}'
    assert page.locator('.risk-bands div').count() == 4


def test_risk_deep_dive_shows_validated_ai(page):
    open_risk(page, 'JPM')
    page.click('#riskPageTabs button:nth-child(2)')
    page.wait_for_timeout(5000)
    page.screenshot(path=os.path.join(SHOTS, '8_risk_deep.png'))
    text = page.inner_text('#riskBody')
    assert '7.8%' in text, 'El campo válido se muestra'
    assert '67.8%' not in text, 'La cifra inventada no llega a la pantalla'
    reasons = page.evaluate("JSON.stringify(RISK_NARR['JPM:es'])")
    assert page.locator('.risk-na').count() == 1, reasons
    page.click('#riskPageTabs button:nth-child(1)')


def test_sidebar_sorted_by_real_risk(page):
    page.evaluate("state.sortBy='score_desc'; renderSidebar()")
    scores = page.evaluate("[...document.querySelectorAll('.ticker-chip')].map(c=>getTickerScore(c.textContent)).filter(s=>s!==null)")
    assert scores == sorted(scores, reverse=True) and scores
    page.evaluate("state.sortBy='default'; renderSidebar()")


def test_no_js_or_server_errors(page):
    assert page.errors == []
