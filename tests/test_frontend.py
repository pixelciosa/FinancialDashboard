import unittest
import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class TestFrontendLogic(unittest.TestCase):

    def setUp(self):
        html_path = os.path.join(PROJECT_DIR, 'index.html')
        self.assertTrue(os.path.exists(html_path))
        with open(html_path, encoding='utf-8') as f:
            self.content = f.read()

    def test_initial_ticker_comes_from_loaded_data(self):
        """Sin ticker fijo en el HTML ni en el estado: se elige al cargar los metadatos."""
        self.assertIn('activeTicker: null', self.content)
        self.assertIn('state.activeTicker = pickInitialTicker()', self.content)
        self.assertIn("localStorage.setItem('activeTicker', ticker)", self.content,
                      "selectTicker debe recordar el último ticker elegido")
        self.assertNotIn('<div class="tick" id="tech-tick">JPM</div>', self.content)

    def test_state_isolation(self):
        self.assertIn('state.loadedCandleTicker', self.content, "loadedCandleTicker state missing")
        self.assertIn('state.lastCandles = []', self.content, "selectTicker must reset lastCandles array")
        self.assertIn('state.loadedCandleTicker = null', self.content, "selectTicker must reset loadedCandleTicker")

    def test_ticker_switch_clears_previous_ticker_data(self):
        # Ensure selectTicker and loadChart immediately clear previous chart & BB indicator data
        self.assertIn('clearTechnicalChart()', self.content, "clearTechnicalChart must be called on ticker selection")
        self.assertIn('clearBollingerBands()', self.content, "clearBollingerBands must be defined and called")

    def test_bollinger_bands_ticker_isolation(self):
        self.assertIn('btnToggleBB', self.content, "btnToggleBB element missing")
        self.assertIn('state.loadedCandleTicker !== state.activeTicker', self.content, "updateBollingerBands must guard against ticker mismatch")

    def test_global_bollinger_scope(self):
        # Ensure updateBollingerBands and clearTechnicalChart are declared in top-level script scope so loadChart can call them
        pos_init = self.content.find('async function init()')
        pos_update_bb = self.content.find('function updateBollingerBands(candles)')
        self.assertGreater(pos_init, 0, "async function init() not found")
        self.assertGreater(pos_update_bb, 0, "function updateBollingerBands not found")
        self.assertLess(pos_update_bb, pos_init, "updateBollingerBands must be declared in global scope before init() so loadChart can access it")

    def test_risk_report_ticker_isolation(self):
        # Verify renderRiskTicker guards against showing previous ticker data
        self.assertIn('if (ticker !== state.activeTicker) return', self.content, "renderRiskTicker must validate activeTicker context")

    def test_valuations_register_replaces_compare(self):
        # Comparar (Gemma, puntaje viejo) se reemplazó por el registro de valuaciones
        self.assertIn('btnValuations', self.content, "btnValuations element missing")
        self.assertIn('valModalBackdrop', self.content, "valModalBackdrop element missing")
        self.assertIn('/api/valuations', self.content, "/api/valuations route call missing")
        self.assertIn('/api/valuations/refresh', self.content, "refresh call missing")
        self.assertNotIn('/api/compare', self.content, "Comparar sigue llamando a /api/compare")
        self.assertNotIn('compareModalBackdrop', self.content, "el modal Comparar sigue en el HTML")

    def test_print_export(self):
        self.assertIn('btnPrintReport', self.content, "btnPrintReport element missing")
        self.assertIn('@media print', self.content, "@media print CSS missing")

    def test_sidebar_sorting(self):
        self.assertIn('sortWrapper', self.content, "sortWrapper element missing")
        self.assertIn('sort_score_desc', self.content, "sort_score_desc translation key missing")
        self.assertIn('function sortSymbols', self.content, "sortSymbols sorting function missing")
        self.assertIn('function getTickerScore', self.content, "getTickerScore score calculation function missing")
        self.assertIn('function getTickerChgPct', self.content, "getTickerChgPct change calculation function missing")

    def test_live_quote_sync(self):
        self.assertIn('liveQuotes: {}', self.content, "liveQuotes state map missing")
        self.assertIn('state.liveQuotes[ticker]', self.content, "liveQuotes sync in loadChart missing")
        self.assertIn('loadInitialQuotes()', self.content, "loadInitialQuotes pre-fetch missing")

    def test_portfolio_table_sorting(self):
        self.assertIn('setPortfolioSort', self.content, "setPortfolioSort function missing")
        self.assertIn('sortPortfolioRows', self.content, "sortPortfolioRows function missing")
        self.assertIn('getPfSortArrow', self.content, "getPfSortArrow function missing")

    def test_refresh_dropdown_ui(self):
        self.assertIn('refreshDropdownWrap', self.content, "refreshDropdownWrap element missing")
        self.assertIn('btnRefreshDropdown', self.content, "btnRefreshDropdown element missing")
        self.assertIn('refreshMenu', self.content, "refreshMenu element missing")
        self.assertIn('btnRefreshSingle', self.content, "btnRefreshSingle element missing")
        self.assertIn('refreshToast', self.content, "refreshToast element missing")
        self.assertIn('/api/refresh/market', self.content, "/api/refresh/market call missing")
        self.assertIn('/api/refresh/technical', self.content, "/api/refresh/technical call missing")
        self.assertIn('fetchRisk(sym, true)', self.content, "Risk refresh must recompute via /api/risk (no Gemma batch)")

    def test_status_pill_engine_and_single_refresh_buttons(self):
        self.assertIn('function renderStatusPill', self.content, "renderStatusPill engine missing")
        self.assertIn('btnRefreshSingleTech', self.content, "btnRefreshSingleTech button missing")
        self.assertIn('tech-status', self.content, "tech-status element missing")
        self.assertIn('btnRefreshSingle2', self.content, "btnRefreshSingle2 button missing in Risk Deep Dive")
        self.assertIn('h2-status', self.content, "h2-status element missing in Risk Deep Dive")
        self.assertIn('btnRefreshSingleFin', self.content, "btnRefreshSingleFin button missing in Financial Analysis")
        self.assertIn('fin-status', self.content, "fin-status element missing in Financial Analysis")
        self.assertIn('status_updated_mins', self.content, "status_updated_mins translation missing")
        self.assertIn('status_outdated', self.content, "status_outdated translation missing")

    def test_risk_render_guards_missing_data(self):
        # El reporte de riesgo tolera pilares, eventos o precios ausentes
        self.assertIn('(d.pillars || [])', self.content, "renderRiskOverview must guard missing pillars")
        self.assertIn('(d.events || [])', self.content, "risk render must guard missing events")
        self.assertIn('d.prices_12m || []', self.content, "risk chart must guard missing prices")

    def test_refresh_ticker_isolation(self):
        self.assertIn('state.singleRefreshes', self.content, "singleRefreshes map state missing")
        self.assertIn('isCurrentlyGenerating', self.content, "isCurrentlyGenerating check in renderStatusPill missing")

    def test_add_ticker_unbaked_report_fallback(self):
        self.assertIn('(report && report.name) ? report.name : sym', self.content, "addTicker must safely handle undefined report.name")
        self.assertIn('finEsc(d.name || ticker)', self.content, "risk header must safely fall back to ticker if d.name is missing")

    def test_favicon(self):
        self.assertIn('rel="icon"', self.content, "favicon link missing in index.html")
        self.assertIn('favicon-32x32.png', self.content, "favicon-32x32.png reference missing in index.html")
        self.assertIn('apple-touch-icon', self.content, "apple-touch-icon reference missing in index.html")

if __name__ == '__main__':
    unittest.main()


