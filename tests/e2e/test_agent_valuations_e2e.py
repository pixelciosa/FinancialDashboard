"""
E2E del registro de valuaciones y del panel del Portfolio Agent (Chromium headless, servidor real,
yfinance y Gemma simulados). Opcional: python -m pytest -m e2e
Capturas en tests/e2e/shots/.
"""
import os
import threading
import time

import pytest

pytestmark = pytest.mark.e2e
sync_api = pytest.importorskip('playwright.sync_api')

SHOTS = os.path.join(os.path.dirname(__file__), 'shots')
PORT = 5098
LWC_STUB = ("(function(){const P=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?(()=>0):"
            "(k==='then'?undefined:P),apply:()=>P,construct:()=>P});window.LightweightCharts=P;})();")

CHAT_REPLY = ('Tu portfolio tiene **riesgo moderado** en general.\n'
              '- Las posiciones caras conviene mirarlas por valuación.\n'
              '- El precio objetivo de JPM es 999.\n'
              '[[TABLE cols=value,pnl_pct,risk_score,valuation,pe tickers=portfolio sort=-value limit=8 title="Portfolio"]]')


EXPLORE_REPLY = ('Busco empresas de sectores que todavía no tenés.\n'
                 '[[EXPLORE intent=diversify sectors=Healthcare,Utilities]]\nTambién podrías mirar XYZW.')


@pytest.fixture(scope='module')
def app_url(tmp_path_factory):
    import financials as fin
    import narrative
    import risk
    import server
    import valuations
    from fakes import ETF_INFO, STOCK_INFO, FakeDownload, FakeTicker, daily_history

    tmp = tmp_path_factory.mktemp('e2e_val')
    fin.CACHE_DIR = str(tmp / 'fin')
    risk.CACHE_DIR = str(tmp / 'risk')
    narrative.CACHE_DIR = str(tmp / 'narr')
    server.fetch_coingecko_prices = lambda: {}
    server.NVIDIA_API_KEY = 'fake'
    server._chat_call = lambda messages: (EXPLORE_REPLY if 'nuevos' in messages[-1]['content'] else CHAT_REPLY)
    import explore

    class _EQ:
        def __init__(self, op, operands):
            self.op, self.operands = op, operands
    explore.yf.EquityQuery = _EQ
    explore.yf.screen = lambda q, **kw: {'quotes': [
        {'symbol': 'HLTH', 'quoteType': 'EQUITY', 'exchange': 'NYQ', 'longName': 'Health Co',
         'averageAnalystRating': '1.0 - Strong Buy'},
        {'symbol': 'HLTHF', 'quoteType': 'EQUITY', 'exchange': 'PNK', 'longName': 'Health Co OTC'},
        {'symbol': 'UTIL', 'quoteType': 'EQUITY', 'exchange': 'NYQ', 'longName': 'Util Power'}]}
    explore.PREFS_PATH = str(tmp / 'explore_profile.json')
    server.load_portfolio_from_csv = lambda: {t: {'shares': 10.5, 'costBasis': 1234.56} for t in server.PORTFOLIO_TICKERS[:6]}
    reg = {
        'HLTH': FakeTicker('HLTH', info={**STOCK_INFO, 'longName': 'Health Co', 'sector': 'Healthcare',
                                         'industry': 'Drug Manufacturers', 'beta': 0.7}, daily=daily_history(start_price=160.0)),
        'UTIL': FakeTicker('UTIL', info={**STOCK_INFO, 'longName': 'Util Power', 'sector': 'Utilities',
                                         'industry': 'Utilities - Regulated Electric', 'dividendRate': 8.4, 'beta': 0.4},
                           daily=daily_history(start_price=160.0)),
        'JPM': FakeTicker('JPM', info={**STOCK_INFO, 'longName': 'JPMorgan Chase & Co.', 'trailingPE': 9.1, 'forwardPE': 7.4,
                                         'priceToSalesTrailing12Months': 1.1}),
        'CRM': FakeTicker('CRM', info={**STOCK_INFO, 'longName': 'Salesforce Inc.', 'trailingPE': 61.3,
                                         'forwardPE': 72.4, 'priceToSalesTrailing12Months': 26.0}),
        'VTI': FakeTicker('VTI', info={**ETF_INFO, 'longName': 'Vanguard Total Stock Market ETF'}),
    }
    server.yf.Ticker = lambda sym, *a, **k: reg.get(sym) or FakeTicker(sym, daily=daily_history(start_price=180.0))
    valuations.yf.download = FakeDownload(reg)
    threading.Thread(target=lambda: server.app.run(port=PORT, debug=False, use_reloader=False), daemon=True).start()
    time.sleep(1.2)
    return f'http://127.0.0.1:{PORT}/'


@pytest.fixture(scope='module')
def page(app_url):
    os.makedirs(SHOTS, exist_ok=True)
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=2)
        pg.errors = []
        pg.route('**/lightweight-charts*', lambda r: r.fulfill(body=LWC_STUB, content_type='application/javascript'))
        pg.route('**/fonts.g*/**', lambda r: r.abort())
        pg.on('pageerror', lambda e: pg.errors.append(str(e)))
        pg.on('response', lambda r: pg.errors.append(f'HTTP {r.status} {r.url}') if r.status >= 500 else None)
        pg.add_init_script("try{localStorage.setItem('lang','es')}catch(e){}")
        pg.goto(app_url)
        pg.wait_for_selector('#btnValuations')
        pg.wait_for_selector('#chatMessages .chat-msg', state='attached')   # initChat ya corrió
        yield pg
        browser.close()


def test_valuations_register_refresh_and_groups(page):
    page.click('#btnValuations')
    page.wait_for_selector('#valModalBackdrop.open')
    page.wait_for_selector('.val-summary')
    page.click('#btnValRefresh')
    page.wait_for_function("!document.getElementById('btnValRefresh').disabled", timeout=60000)
    page.wait_for_timeout(400)
    counts = page.eval_on_selector_all('.val-card .n', 'els => els.map(e => +e.textContent)')
    total = page.evaluate("Object.entries(getAllMeta()).filter(([, m]) => m.category !== 'archived').length")
    assert sum(counts) == total and counts[0] >= 1 and counts[2] >= 1, counts      # JPM barata, CRM cara
    assert page.locator('#valGroup-cheap tr[data-t="JPM"]').count() == 1
    assert page.locator('#valGroup-expensive tr[data-t="CRM"]').count() == 1
    na = page.locator('#valGroup-na tr[data-t="VTI"]').inner_text()
    assert 'No aplica (ETF)' in na
    page.screenshot(path=os.path.join(SHOTS, 'valuations.png'))
    assert not page.errors, page.errors


def test_valuation_row_opens_risk_tab(page):
    page.click('#valGroup-expensive tr[data-t="CRM"]')
    page.wait_for_function("state.activeTab === 'risk' && state.activeTicker === 'CRM'")
    assert not page.locator('#valModalBackdrop.open').count()


def test_chat_panel_is_large_and_renders_verified_table(page):
    page.click('#chatFab')
    page.wait_for_selector('#chatPanel.open')
    page.wait_for_timeout(350)
    box = page.locator('#chatPanel').bounding_box()
    assert box['width'] >= 600 and box['height'] >= 900, box
    page.click('.chat-suggestion >> nth=0')
    page.wait_for_selector('.chat-table', timeout=20000)
    reply = page.locator('.chat-msg.assistant').last
    text = reply.inner_text()
    assert 'riesgo moderado' in text and '999' not in text and 'objetivo' not in text
    assert 'Se omitieron 1 frase' in text
    rows = page.locator('.chat-table tbody tr').count()
    assert 1 <= rows <= 8
    assert page.locator('.chat-table th').first.inner_text().startswith('Ticker')
    page.screenshot(path=os.path.join(SHOTS, 'chat_panel.png'))
    page.click('#chatExpand')
    page.wait_for_timeout(350)
    assert page.locator('#chatPanel').bounding_box()['width'] > box['width']
    page.screenshot(path=os.path.join(SHOTS, 'chat_panel_expanded.png'))
    assert not page.errors, page.errors


def test_explore_candidates_and_add_to_watching(page):
    if not page.locator('#chatPanel.open').count():
        page.click('#chatFab')
        page.wait_for_selector('#chatPanel.open')
    page.click('#chatClear')
    page.fill('#chatInput', 'Sugerime activos nuevos para diversificar')
    page.press('#chatInput', 'Enter')
    page.wait_for_selector('.exp-table', timeout=30000)
    reply = page.locator('.chat-msg.assistant').last
    text = reply.inner_text()
    assert 'XYZW' not in text and 'Strong Buy' not in text
    # El ETF simulado del portfolio ya tiene peso en Salud; Servicios públicos no está en ningún activo
    assert 'Candidatos para observar' in text and 'Sector que no tenés: Servicios públicos' in text
    assert page.locator('.exp-table tbody tr').count() == 2, 'el OTC se descarta'
    page.screenshot(path=os.path.join(SHOTS, 'explore.png'))
    page.click('.exp-add[data-t="HLTH"]')
    page.wait_for_function("getAllMeta()['HLTH'] && getAllMeta()['HLTH'].category === 'watching'")
    assert page.locator('#cloudWatching').inner_text().find('HLTH') >= 0
    assert not page.errors, page.errors


def test_explore_preferences_panel(page):
    page.click('#chatPrefsBtn')
    page.wait_for_selector('#expSave')
    page.select_option('#expLevel', 'low')
    page.check('#expSecs input[value="Energy"]')
    page.screenshot(path=os.path.join(SHOTS, 'explore_prefs.png'))
    page.click('#expSave')
    page.wait_for_selector('#chatPrefs', state='hidden')
    prefs = page.evaluate("fetch('/api/explore/preferences').then(r => r.json())")
    assert prefs['max_level'] == 'low' and prefs['exclude_sectors'] == ['Energy']
    assert not page.errors, page.errors
