# Financial Dashboard

A local AI-powered financial dashboard for tracking and analyzing your investment portfolio — stocks, ETFs, crypto, and cash. Runs entirely on your machine. No cloud, no subscriptions.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue) ![Flask](https://img.shields.io/badge/backend-Flask-lightgrey) ![Vanilla JS](https://img.shields.io/badge/frontend-Vanilla%20JS-yellow)

---

## Features

- **Sidebar** — collapsible ticker list organized by Portfolio / Watching / Archived, with drag-to-reorder, drag-to-delete, type/country filters, and text search
- **Technical analysis** — candlestick chart (4h / 1D / 1W) with EMA-20, volume density, and key metrics with educational tooltips
- **Risk report** — AI-generated risk score, valuation/health cards, quarterly earnings trend, catalysts and risks
- **Financial analysis** — revenue segments, margin trends, FCF, annual returns (for tickers with pre-built data)
- **Portfolio panel** — live P&L, unrealized gains, recovered positions, cash balances, asset allocation charts, and AI portfolio analysis
- **Chat agent** — floating chat panel with full portfolio context, powered by Gemma
- **EN / ES language toggle** — all UI strings and AI responses switch language
- **Google Sheets as data source** — point to your own public sheet; no CSV exports needed

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/your-username/financial-dashboard.git
cd financial-dashboard

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your Nvidia API key
cp .env.example .env
# Edit .env and paste your key from https://build.nvidia.com

# 5. Configure your portfolio (see section below)
# Edit config.py

# 6. Run by default in port 5050
python server.py --port 5051

# 7. Open in browser
open http://localhost:5051
```

---

## Configuring your portfolio

All user settings live in **`config.py`** — it's the only file you need to edit.

### 1. Google Sheet setup

Create a Google Sheet and **share it publicly** (Anyone with the link → Viewer).

The dashboard reads two tabs:

#### Transactions tab (`TRANSACTIONS_SHEET_NAME`)

A log of every buy, sell, dividend, or initial holding. One row per operation.

| Column | Example | Notes |
|--------|---------|-------|
| Asset name | Apple Inc. | Human-readable |
| Ticker | AAPL | Used as the unique ID |
| Operation type | Compra | See `OPS_QUANTITY` in config |
| Asset category | Acción | Maps to Stock/ETF/Crypto |
| Quantity | 2.5 | Negative for sells |
| Investment in USD | 437.50 | Cost basis for buys |

A template is included: **`Contabilidad-ejemplo.xlsx`**

#### Capital tab (`CAPITAL_SHEET_NAME`)

Snapshot of cash balances and any holdings that predate your transaction log.

```
Cash:
USD       6540
USDT      1990
USDC       450
ARS     3700000
Pre-existent assets:
NVDA        2
KO          3.08
```

- USD/USDT/USDC/DAI are grouped as `USD_CASH` (dollar liquidity)
- ARS is auto-converted to USD using the unofficial blue-dollar rate (Bluelytics API)
- Pre-existent assets are added only if the ticker has no rows in the transactions tab

### 2. Adapt column names

If your sheet uses different column headers, update the `COL_*` constants in `config.py`:

```python
COL_TICKER     = 'Symbol'          # or 'Ticker', 'Símbolo', etc.
COL_OPERATION  = 'Type'            # or 'Tipo de Operación', etc.
COL_ASSET_TYPE = 'Asset Type'
COL_QUANTITY   = 'Quantity'
COL_INVESTMENT = 'Cost (USD)'
```

### 3. Adapt operation labels

Tell the dashboard which operation labels count as buys vs. sells:

```python
OPS_QUANTITY   = {'Buy', 'Hold', 'Dividend', 'Interest'}   # adds to shares
OPS_COST_BASIS = {'Buy', 'Hold'}                            # adds to cost basis
```

Sells should have **negative quantity** in the sheet — no separate label needed.

### 4. Adapt asset type labels

```python
ASSET_TYPE_MAP = {
    'Stock':        'Stock',
    'ETF':          'ETF',
    'Crypto':       'Crypto',
}
```

### 5. Add crypto

For each crypto ticker you hold, add its CoinGecko slug:

```python
COINGECKO_IDS = {
    'BTC':  'bitcoin',
    'ETH':  'ethereum',
    'SOL':  'solana',       # add yours
}
```

Find the slug in the CoinGecko URL: `coingecko.com/en/coins/`**`solana`**

---

## AI features

The dashboard uses **Nvidia's Gemma 3** (free tier) for:
- Per-ticker risk reports
- Portfolio analysis
- Chat agent with portfolio context

Get a free API key at [build.nvidia.com](https://build.nvidia.com) and add it to `.env`:

```
NVIDIA_API_KEY=nvapi-your-key-here
```

No key = AI features disabled, everything else works fine.

---

## Project structure

```
├── server.py                   # Flask backend — data, AI, API routes
├── index.html                  # Complete frontend (CSS + JS embedded)
├── config.py                   # ← Your only config file
├── .env                        # API keys (never commit this)
├── .env.example                # Template for .env
├── Contabilidad-ejemplo.xlsx   # Google Sheet template
├── requirements.txt
└── reports/                    # AI-generated reports cached per ticker (JSON)
```

---

## Data sources

| Data | Source | Key required |
|------|--------|-------------|
| Stocks & ETFs price | yfinance | No |
| Crypto price | CoinGecko | No |
| ARS/USD rate | Bluelytics | No |
| AI analysis | Nvidia Gemma 3 | Yes (free) |
| Portfolio data | Google Sheets | No (public sheet) |

---

## Customizing tickers

You can add tickers at runtime via the **+ Add** button in the dashboard — no code needed. Added tickers are stored in your browser's `localStorage` and their AI reports are cached in `reports/`.

To pre-load tickers in the sidebar without running an AI report, add them to your Google Sheet transactions tab.

---

## Notes

- **Local only** — designed to run on `localhost`. Do not expose to the internet without adding authentication.
- **Sheet cache** — portfolio data is cached for 5 minutes (`SHEET_CACHE_TTL` in config). Restart the server to force a refresh.
- **Reports cache** — AI reports are saved to `reports/<TICKER>.json` and reused across restarts. Delete a file to force regeneration.
- **Financial analysis tab** — only works for tickers with pre-built data in `index.html`. The risk report tab works for any ticker.
