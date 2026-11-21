# Financial Dashboard (demo edition)

A local, self-hosted dashboard to follow and analyze an investment portfolio (stocks, ETFs, crypto and
cash), with a deterministic risk model, quarterly fundamentals, a grounded AI narrative and chat agent, and a
"Portfolio Lab" for performance, exposure, attribution and stress testing.

**This is the public demo edition.** All portfolio data in this repository is **fictional**: a made-up
portfolio of well-known tickers in `sample_data/`, and synthetic prices for those tickers in the Lab test
fixtures. You can point the app to your own CSV files (see [Use your own data](#use-your-own-data)).

> Screenshots: _coming soon_.

## Design principle: AI is never the source of a number

Prices, metrics, quarters, scores and verdicts come from market data (yfinance, CoinGecko) or from
deterministic, tested Python code. The language model (Gemma via NVIDIA NIM) only writes prose on top of
numbers that were already computed, and its output is validated before it reaches the UI:

- Every number in an AI sentence must appear in the facts the server sent to the model (rounding tolerance).
  Sentences with unsupported figures, price targets or buy/sell instructions are dropped, and the UI says how
  many were omitted.
- The chat agent cannot write tables: it asks for one with a `[[TABLE cols=... tickers=...]]` directive and the
  server builds it from the same data.
- Without an API key (or when the model fails) the AI features report that they are unavailable; every data
  feature keeps working. Missing data is `null`, never `0`, and the JSON never contains `NaN`/`Infinity`.

These rules are enforced by tests (`tests/test_ai_guardrails.py`, `tests/test_narrative.py`, `tests/test_agent.py`).

## Features

**Dashboard** (`/`)
- Sidebar with Portfolio / Watching / Archived lists, filters by type and country, search.
- Technical view: 4H / 1D / 1W candles with EMA-20 and Bollinger bands (TradingView Lightweight Charts).
- Financials tab (no AI): annual + TTM or quarterly view, margins, free cash flow, EPS actual vs estimate, an
  8-quarter table, ETF holdings and sectors, reporting currency. Statements are cached on disk and only
  re-downloaded when a new quarter is published (`GET /api/financials/status`).
- Risk report: a 0-100 score (higher = riskier) computed without AI from valuation, financials and market data,
  with the rule behind every point, plus an AI narrative validated against the data.
- Valuation registry: Cheap / Fair / Expensive / Not available, refreshed in batch with a single price download.
- Portfolio panel: positions with live P&L, allocation, cash in several currencies.
- Portfolio Agent: a chat panel that sees positions, technicals, financials, risk and valuation, answers with
  server-built tables and verified figures.
- Explore mode: asks Yahoo's screener (NYSE/Nasdaq) for new candidates; each one goes through the same
  financials and risk pipeline, and the reasons come from templates, not from the model.
- English / Spanish UI.

**Portfolio Lab** (`/portfolio`, enabled with `PORTFOLIO_LAB=1`)
- Time-weighted performance vs a choice of indices, drawdown, monthly and yearly returns, "same contributions
  in the index" comparison.
- Correlation matrix and rolling correlation (Bitcoin, gold, Nasdaq, US dollar index).
- Exposure by risk block with 5/25 bands, position caps and risk contribution (descriptive, not advice).
- Attribution by position (modified Dietz) and by operation (FIFO lots).
- Macro traffic light (VIX, 10-year yield, yield curve, dollar index, oil, gold/silver) with hysteresis.
- Historical stress scenarios (2020, 2022, ...) applied to current weights, with index proxies for assets that
  did not exist yet.
- Decision journal with rule-based evaluation.
- Ledger reconciliation against current holdings, including an "excluded platforms" concept: operations held on
  a platform you do not want in the Lab are left out but still explain the gap (`esperada_excluida`).

## Architecture

```
Browser ── index.html (vanilla JS, single file)        lab_ui/ (Lab page, vanilla JS + SVG charts)
   │                                                      │
   ▼                                                      ▼
server.py (Flask routes, in-memory TTL caches) ──── lab/api.py (Blueprint /api/lab/*, optional)
   │                                                      │
   ├── portfolio_data.py  CSV inputs (portfolio, cash, watchlist)    lab/ledger.py, engine.py, exposure.py,
   ├── financials.py      statements, TTM, EPS, freshness            attribution.py, stress.py, macro.py,
   ├── risk.py            deterministic 0-100 risk model             journal.py, prices.py (own price cache)
   ├── valuations.py      valuation registry, batch refresh
   ├── narrative.py       AI narrative + grounding validator
   ├── agent.py           chat agent: context, server-built tables, verified sentences
   └── explore.py         screener candidates, template reasons
        │
        ▼
yfinance · CoinGecko (no key) · NVIDIA NIM (optional, text only)
```

- Pure functions for calculations (lists/DataFrames in, data out) and thin Flask routes.
- The Lab is encapsulated: it never imports the dashboard modules, has its own price adapter and cache, and the
  dashboard starts normally even if the Lab fails to import.
- Caches live in `data/` (git-ignored) and are rebuilt on demand.

| File | Responsibility |
|---|---|
| `server.py` | Flask routes, quote/OHLCV caches, portfolio, financials, risk, valuations, chat, Explore |
| `portfolio_data.py` | Reads `PORTFOLIO_CSV`, `CASH_CSV`, `WATCHLIST_CSV`; builds the ticker metadata |
| `financials.py` | Financials tab data and quarterly freshness |
| `risk.py` | Risk model (rules documented in `.claude/skills/risk-model/SKILL.md`) |
| `narrative.py` | Gemma narrative, validated against computed facts |
| `valuations.py` | Valuation labels and batch refresh |
| `agent.py` | Portfolio Agent |
| `explore.py` | Explore mode |
| `lab/` | Portfolio Lab backend; `lab/paths.py` documents its inputs |
| `config/lab/` | Lab configuration: blocks, scenarios, macro rules, excluded platforms |
| `sample_data/` | Fictional demo inputs |
| `scripts/generate_demo_data.py` | Regenerates the fictional ledger/portfolio and the synthetic price fixtures |

## Run it with the sample data

Requirements: Python 3.11+.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env              # optional: add NVIDIA_API_KEY for the AI features
python server.py                  # http://localhost:5050
PORTFOLIO_LAB=1 python server.py  # also enables the Lab at http://localhost:5050/portfolio
```

Market data is fetched live from yfinance and CoinGecko, so the dashboard needs internet access. In the Lab,
prices are not downloaded automatically: use the refresh button on the page (or `POST /api/lab/refresh`) the
first time. The AI features need an `NVIDIA_API_KEY` from [build.nvidia.com](https://build.nvidia.com); without
it, everything else works.

## Use your own data

All inputs are plain CSV files. Point the environment variables (in `.env` or your shell) to your files:

| Variable | Default | Format |
|---|---|---|
| `PORTFOLIO_CSV` | `sample_data/portfolio.csv` | `ticker,name,type,quantity,cost_basis_usd` (+ optional `sector,country,coingecko_id`) |
| `CASH_CSV` | `sample_data/cash.csv` | `currency,amount,usd_rate` (USD per unit of the currency) |
| `WATCHLIST_CSV` | `sample_data/watchlist.csv` | `ticker,name,type,category` with category `watching` or `archived` |
| `LEDGER_CSV` | `sample_data/ledger.csv` | Lab ledger, `;`-separated: `fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen[;plataforma]` |
| `LAB_CONFIG_DIR` | `config/lab` | `config.json`, `blocks.json`, `scenarios.json`, `macro_rules.json` |

Notes:
- `type` is `Stock`, `ETF` or `Crypto`. Crypto tickers are written without suffix (`SOL`); the app adds `-USD`
  for yfinance. Prices for crypto come from CoinGecko when the ticker has a CoinGecko id (built-in for a few
  common coins, or your `coingecko_id` column), otherwise from yfinance.
- `cost_basis_usd` is the total cost of the position. A position whose remaining cost is below USD 4 is shown as
  "recovered" (its investment was paid back) and its P&L % is omitted.
- Ledger `origen` values: `apertura` (opening position), `compra`, `venta`, `dividendo` (units at zero cost),
  `dividendo_efectivo` (cash income). Rows whose `plataforma` is listed in `config/lab/config.json` →
  `excluded_platforms` are left out of the Lab and only used by the reconciliation.
- Edit `config/lab/blocks.json` so the risk blocks match your tickers. The reference weights are descriptive.
- Bad rows are skipped and reported (server log, `/api/lab/ledger`), never fatal.
- The column names of the ledger and the Lab's backend messages are in Spanish (the project started in Spanish);
  the UI is bilingual.

## Testing

```bash
python -m pytest                 # offline suite: no network, no AI, never writes to data/ (default)
python -m pytest tests/lab       # only the Lab
python -m pytest -m live         # smoke tests against real yfinance / CoinGecko / NVIDIA (needs network)
python -m pytest -m e2e          # browser tests (requires playwright + Chromium)
```

- A guard in `tests/conftest.py` makes any non-`live` test fail if it tries to open a network connection, and
  redirects every cache to a temporary folder.
- Known bugs are recorded as `xfail` with a reason; `xfail_strict` turns a fix into a visible XPASS.
- The Lab golden tests (`tests/lab/test_engine_golden.py` and the "demo ledger" tests in `test_attribution.py`,
  `test_exposure.py`, `test_stress.py`) pin the engine against the fictional ledger and the synthetic price
  fixtures. Their expected values were regenerated from that fictional data (`scripts/generate_demo_data.py`).

| Test file | Protects |
|---|---|
| `test_server.py` | Endpoint regressions (quotes, OHLCV, metadata, refresh) |
| `test_portfolio_data.py` | CSV inputs, cash conversion, ticker metadata, `/api/portfolio` |
| `test_data_integrity.py` | Every number recomputed from its source statement |
| `test_ai_guardrails.py`, `test_narrative.py` | The AI cannot impose numbers, scores or tables |
| `test_asset_identity.py` | Each endpoint queries the right asset (crypto with `-USD`) |
| `test_financials*.py`, `test_freshness.py`, `test_risk.py`, `test_valuation.py` | Financials, freshness, risk, valuations |
| `test_agent.py`, `test_explore.py` | Chat agent and Explore mode |
| `test_frontend*.py` | JS syntax, EN/ES i18n, API routes used by the UI |
| `tests/lab/` | The whole Portfolio Lab |

## Portfolio Lab API reference

| Endpoint | Returns |
|---|---|
| `GET /api/lab/health` | `{ok, phase, caches:{precios,estres,macro:{datos_al}}, ultimo_refresh}` |
| `POST /api/lab/refresh` | Updates the ledger + index prices, the stress cache and the macro cache (requires `application/json`; 409 if one is running) |
| `GET /api/lab/ledger` | Normalized operations and per-row read errors |
| `GET /api/lab/reconcile` | Ledger vs `PORTFOLIO_CSV` quantities per ticker; trimmed sales |
| `GET /api/lab/performance?benchmark=` | Base-100 series, drawdown, monthly/yearly returns, stats, same contributions in the index |
| `GET /api/lab/correlation?window=` | Full matrix and rolling correlation (5 to 250 days) |
| `GET /api/lab/risk-return` | Volatility and return of the portfolio, indices and positions |
| `GET /api/lab/exposure` | Weight and risk contribution per block, bands, position caps, alerts |
| `GET /api/lab/attribution?from=&to=` | Contribution per position and per operation |
| `GET /api/lab/macro` | Macro series, lights, regime label, calibration |
| `GET /api/lab/stress?escenario=` | Each historical scenario applied to current weights |
| `GET, POST /api/lab/journal`, `PUT /api/lab/journal/<id>` | Decision journal |

Manual cache refresh (network): `python -m lab.stress_prices`, `python -m lab.macro_prices`.

Engine conventions: time-weighted return `r_t = (V_t − F_t) / V_{t−1} − 1` chained from 100; 252 trading days;
volatility = stdev of daily returns × √252; drawdown vs the running maximum; calendar = SPY trading days with
forward-filled closes; attribution by modified Dietz (it does not match the TWR, by design); same contributions in
an index: `u₀ = V₀/P₀`, then `u += F_t/P_t`.

### Error codes (`codigo`)

Every `{ok:false}` response from the Lab carries a stable `codigo` (the UI translates it) next to `error` (text).

| `codigo` | When |
|---|---|
| `benchmark_invalido` | 400: `benchmark` is not one of the indices (`validos` in the response) |
| `ventana_invalida` | 400: `window` is not an integer |
| `rango_invalido` | 400: `window` outside 5-250, or an attribution date range that is inverted, outside the calendar or shorter than two trading days |
| `fecha_invalida` | 400: `from` or `to` are not `YYYY-MM-DD` |
| `escenario_invalido` | 400: unknown `escenario` (`validos` in the response) |
| `sin_precios` | 200 with `status:'sin_precios'`: no price cache yet (use `POST /refresh`) |
| `sin_operaciones` | 200: the ledger has no operations inside the price calendar |
| `sin_precios_macro` | 200: no macro cache |
| `config_invalida` | 200: `blocks.json`, `scenarios.json` or `macro_rules.json` missing or malformed |
| `csv_portfolio_ilegible` | 404 on `/reconcile`: `PORTFOLIO_CSV` could not be read |
| `tipo_contenido_invalido` | 415: POST/PUT without `Content-Type: application/json` |
| `cuerpo_demasiado_grande` | 413: journal body larger than 16 KB |
| `cuerpo_invalido` | 400: the body is not a JSON object |
| `validacion` | 400 from the journal: an invalid field (`campo` says which) |
| `limite_alcanzado` | 409 from the journal: a limit was reached (e.g. 500 entries) |
| `entrada_no_encontrada` | 404 from the journal: unknown id |
| `journal_corrupto` | `journal.json` unreadable or with an invalid structure (left untouched) |
| `refresh_en_curso` | 409 on `POST /refresh` |
| `ruta_no_encontrada` | 404 for an unknown `/api/lab/*` route |
| `metodo_no_permitido` | 405 |
| `error_interno` | Unexpected 500 |
| `solicitud_invalida`, `no_encontrado`, `conflicto`, `error_http` | Generic Flask/Werkzeug HTTP errors (400, 404, 409 and others) |

`tests/lab/test_refresh.py` fails if `lab/api.py` emits a code that is not in this table.

## Working with Claude Code

The repository includes the agent setup used to build it (see `CLAUDE.md`, written in Spanish):

- `.claude/agents/`: `data-engineer`, `frontend-engineer`, `qa-verifier` (read-only verifier).
- `.claude/commands/`: `/arquitecto` (plan and delegate), `/fase`, `/verificar`, `/auditar <TICKER>`.
- `.claude/skills/`: project knowledge loaded on demand, e.g. `risk-model`, `quarterly-freshness`,
  `yfinance-fundamentals`, `test-guardrails`, `portfolio-lab`, `dashboard-frontend`.

## Known limitations

- Market data comes from yfinance (unofficial Yahoo Finance API, delayed quotes) and CoinGecko's free API;
  both can rate-limit or change.
- Backend messages of the Lab (reasons, notes, block names, error texts) are in Spanish even when the UI is in
  English; the `codigo` values let the UI translate errors.
- The Lab's stress and macro caches are refreshed on demand only, one refresh at a time per process.
- The synthetic prices used by the Lab tests are not real market data; the live app always uses real prices.
- This is a personal analysis tool, not investment advice.
