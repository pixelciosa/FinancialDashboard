# ─────────────────────────────────────────────────────────────────────────────
# Financial Dashboard · Configuration
# ─────────────────────────────────────────────────────────────────────────────
# This is the only file you need to edit to adapt the dashboard to your own
# portfolio. Keep your Google Sheet publicly shared (View access, no sign-in).
# ─────────────────────────────────────────────────────────────────────────────

# ── 1. Google Sheet ───────────────────────────────────────────────────────────
# Paste the ID from your sheet URL:
#   https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit
GOOGLE_SHEET_ID = '1rP-h3pWm-UWmZcUpmKxCss2L1okl-n4eBanM5OJJhQY'

# Name of the sheet tab that holds transactions (default: first tab)
TRANSACTIONS_SHEET_NAME = 'Contabilidad-ejemplo'

# Name of the sheet tab with cash balances and pre-existing assets
CAPITAL_SHEET_NAME = 'Capital'

# Label that marks the start of the cash section in the Capital sheet
CAPITAL_CASH_LABEL = 'Cash:'

# Label that marks the start of the pre-existent assets section
CAPITAL_PREEXISTENT_LABEL = 'Pre-existent assets:'

# Stablecoins/cash tickers that should be grouped into USD_CASH
USD_CASH_TICKERS = {'USD', 'USDT', 'USDC', 'DAI', 'BUSD'}

# ── 2. Column names ───────────────────────────────────────────────────────────
# Must match your sheet's header row exactly (case-sensitive).
COL_NAME       = 'Activo'               # Human-readable asset name
COL_TICKER     = 'Símbolo'             # Ticker symbol  (e.g. AAPL, BTC)
COL_OPERATION  = 'Tipo de Operación'   # Operation type (see OPS_* below)
COL_ASSET_TYPE = 'Tipo de Activo'      # Asset category (see ASSET_TYPE_MAP)
COL_QUANTITY   = 'Cantidad'            # Signed quantity (negative = sell)
COL_INVESTMENT = 'Inversión en dólares' # Cost in USD for this row

# ── 3. Operation labels ───────────────────────────────────────────────────────
# Rows whose operation matches these labels are included in quantity totals.
OPS_QUANTITY  = {'Compra', 'Tenencia', 'Dividendos', 'Intereses'}
# Rows included in cost-basis totals (exclude dividends/interest).
OPS_COST_BASIS = {'Compra', 'Tenencia'}

# ── 4. Asset type mapping ─────────────────────────────────────────────────────
# Maps your sheet's labels → dashboard internal types (Stock | ETF | Crypto).
ASSET_TYPE_MAP = {
    'Criptomoneda': 'Crypto',
    'ETF':          'ETF',
    'Acción':       'Stock',
}

# ── 5. Number format ──────────────────────────────────────────────────────────
# Decimal separator used in your sheet.
# ',' → European format  (e.g.  1.234,56)
# '.' → US format        (e.g.  1,234.56)
SHEET_DECIMAL_SEP = ','

# ── 6. Crypto ─────────────────────────────────────────────────────────────────
# CoinGecko slug for each crypto ticker you hold.
# Find slugs at https://www.coingecko.com  (use the ID in the URL).
COINGECKO_IDS = {
    'BTC':   'bitcoin',
    'ETH':   'ethereum',
    'XRP':   'ripple',
    'XLM':   'stellar',
    'PAXG':  'pax-gold',
    'TRUMP': 'official-trump',
}

# ── 7. Dashboard defaults ─────────────────────────────────────────────────────
# Ticker shown on first load. Falls back to the first portfolio ticker if not found.
DEFAULT_TICKER = 'SPY'

# How long (seconds) to cache the Google Sheet before re-fetching.
SHEET_CACHE_TTL = 300  # 5 minutes
