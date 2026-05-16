#!/usr/bin/env python3
"""
Financial Dashboard · Local Server
────────────────────────────────────
Install deps (once):
    pip install flask yfinance pandas

Run:
    python server.py

Then open:
    http://localhost:5050
"""

from flask import Flask, jsonify, send_from_directory, request
import yfinance as yf
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import csv
import io
import re
import time
import urllib.request
import json as _json
import json
import requests as http_requests
from dotenv import load_dotenv
from config import (
    GOOGLE_SHEET_ID, TRANSACTIONS_SHEET_NAME, CAPITAL_SHEET_NAME,
    CAPITAL_CASH_LABEL, CAPITAL_PREEXISTENT_LABEL, USD_CASH_TICKERS,
    COL_NAME, COL_TICKER, COL_OPERATION, COL_ASSET_TYPE,
    COL_QUANTITY, COL_INVESTMENT, OPS_QUANTITY, OPS_COST_BASIS,
    ASSET_TYPE_MAP, SHEET_DECIMAL_SEP, COINGECKO_IDS,
    DEFAULT_TICKER, SHEET_CACHE_TTL,
)

load_dotenv()

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
os.makedirs(REPORTS_DIR, exist_ok=True)

NVIDIA_API_URL  = 'https://integrate.api.nvidia.com/v1/chat/completions'
NVIDIA_MODEL    = 'google/gemma-3n-e4b-it'
NVIDIA_API_KEY  = os.environ.get('NVIDIA_API_KEY', '')

# ─── TICKER METADATA (populated from Google Sheet at startup) ────────────────

CRYPTO_YF_SUFFIX = set(COINGECKO_IDS.keys())

TICKER_META: dict = {}          # filled by _init_ticker_meta()
PORTFOLIO_TICKERS: list = []
WATCHING_TICKERS:  list = []
ALL_TICKERS:       list = []

def _refresh_ticker_lists():
    global PORTFOLIO_TICKERS, WATCHING_TICKERS, ALL_TICKERS
    PORTFOLIO_TICKERS = [t for t, m in TICKER_META.items() if m.get('category') == 'portfolio']
    WATCHING_TICKERS  = [t for t, m in TICKER_META.items() if m.get('category') == 'watching']
    ALL_TICKERS       = list(TICKER_META.keys())


# ─── GOOGLE SHEET READER ────────────────────────────────────────────────────

_sheet_cache: dict = {'rows': None, 'ts': 0.0}

def _parse_sheet_num(s: str):
    """Parse a number from the sheet, handling European decimal separator."""
    if not s:
        return None
    s = s.strip()
    if SHEET_DECIMAL_SEP == ',':
        s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None

def _fetch_sheet_csv(sheet_name: str) -> str:
    """Fetch a named sheet tab as raw CSV text."""
    url = (f'https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}'
           f'/gviz/tq?tqx=out:csv&sheet={sheet_name}')
    resp = http_requests.get(url, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    return resp.content.decode('utf-8-sig')

def _fetch_sheet_rows() -> list:
    """Return parsed transaction rows, using a TTL cache."""
    now = time.time()
    if _sheet_cache['rows'] is not None and now - _sheet_cache['ts'] < SHEET_CACHE_TTL:
        return _sheet_cache['rows']
    try:
        text = _fetch_sheet_csv(TRANSACTIONS_SHEET_NAME)
        rows = list(csv.DictReader(io.StringIO(text)))
        _sheet_cache['rows'] = rows
        _sheet_cache['ts']   = now
        print(f'[sheet] fetched {len(rows)} transaction rows')
        return rows
    except Exception as e:
        print(f'[sheet] transactions fetch error: {e}')
        return _sheet_cache['rows'] or []

_capital_cache: dict = {'data': None, 'ts': 0.0}

def _fetch_capital_data() -> dict:
    """
    Parse the Capital sheet and return:
      {
        'cash': {'ARS': float, 'USD_CASH': float},   # values in USD
        'preexistent': {ticker: shares}               # holdings not in transactions
      }
    """
    now = time.time()
    if _capital_cache['data'] is not None and now - _capital_cache['ts'] < SHEET_CACHE_TTL:
        return _capital_cache['data']

    result = {'cash': {}, 'preexistent': {}}
    try:
        text   = _fetch_sheet_csv(CAPITAL_SHEET_NAME)
        reader = csv.reader(io.StringIO(text))
        section = None   # 'cash' | 'preexistent' | None

        usd_cash_total = 0.0
        ars_amount     = 0.0

        for row in reader:
            if not row:
                continue
            label = row[0].strip().strip('"')
            if not label:
                continue

            if label.startswith(CAPITAL_CASH_LABEL):
                section = 'cash'
                continue
            if label.startswith(CAPITAL_PREEXISTENT_LABEL):
                section = 'preexistent'
                continue

            val_raw = row[1].strip().strip('"') if len(row) > 1 else ''
            val = _parse_sheet_num(val_raw)
            if val is None:
                continue

            ticker = label.upper()

            if section == 'cash':
                if ticker == 'ARS':
                    ars_amount = val
                elif ticker in USD_CASH_TICKERS:
                    usd_cash_total += val
            elif section == 'preexistent':
                result['preexistent'][ticker] = val

        # Convert ARS → USD using unofficial rate (best-effort)
        if ars_amount > 0:
            try:
                r = http_requests.get(
                    'https://api.bluelytics.com.ar/v2/latest', timeout=5)
                rate = r.json()['blue']['value_sell']
                result['cash']['ARS'] = round(ars_amount / rate, 2)
            except Exception:
                result['cash']['ARS'] = None   # can't convert, skip

        if usd_cash_total > 0:
            result['cash']['USD_CASH'] = round(usd_cash_total, 2)

        _capital_cache['data'] = result
        _capital_cache['ts']   = now
        print(f'[capital] cash={result["cash"]}, '
              f'preexistent tickers={list(result["preexistent"].keys())}')
    except Exception as e:
        print(f'[capital] fetch error: {e}')
        result = _capital_cache['data'] or result

    return result

def load_portfolio_from_sheet() -> dict:
    """
    Aggregate sheet transactions + Capital sheet into net positions.
    Returns {ticker: {shares, costBasis}} plus cash special entries.
    """
    rows = _fetch_sheet_rows()
    positions: dict = {}

    for row in rows:
        ticker = (row.get(COL_TICKER) or '').strip().upper()
        op     = (row.get(COL_OPERATION) or '').strip()
        if not ticker or op not in OPS_QUANTITY:
            continue

        qty = _parse_sheet_num(row.get(COL_QUANTITY, '')) or 0.0
        inv = _parse_sheet_num(row.get(COL_INVESTMENT, '')) or 0.0

        if ticker not in positions:
            positions[ticker] = {'shares': 0.0, 'costBasis': 0.0}

        positions[ticker]['shares'] += qty
        if op in OPS_COST_BASIS and inv > 0:
            positions[ticker]['costBasis'] += inv

    # Drop fully-sold positions
    positions = {t: v for t, v in positions.items() if v['shares'] > 1e-8}

    # Merge pre-existent assets from Capital sheet (only if NOT already tracked)
    capital = _fetch_capital_data()
    for ticker, shares in capital.get('preexistent', {}).items():
        if ticker not in positions and shares > 1e-8:
            positions[ticker] = {'shares': shares, 'costBasis': 0.0}

    # Attach cash balances as special entries (currentValue in USD)
    for sym, val in capital.get('cash', {}).items():
        if val and val > 0:
            positions[sym] = {'currentValue': val}

    return positions

def _init_ticker_meta():
    """Build TICKER_META from the Google Sheet on startup."""
    rows = _fetch_sheet_rows()
    seen: dict = {}  # ticker → {name, type, has_position}

    for row in rows:
        ticker = (row.get(COL_TICKER) or '').strip().upper()
        if not ticker:
            continue
        name   = (row.get(COL_NAME) or ticker).strip()
        atype  = ASSET_TYPE_MAP.get((row.get(COL_ASSET_TYPE) or '').strip(), 'Stock')
        seen.setdefault(ticker, {'name': name, 'type': atype})

    # Compute net quantities to determine which tickers are active positions
    positions = load_portfolio_from_sheet()

    for ticker, info in seen.items():
        country = 'Global' if info['type'] == 'Crypto' else ''
        sector  = 'Crypto' if info['type'] == 'Crypto' else ''
        cat     = 'portfolio' if ticker in positions else 'archived'
        TICKER_META[ticker] = {
            'name':     info['name'],
            'type':     info['type'],
            'country':  country,
            'sector':   sector,
            'category': cat,
        }

    _refresh_ticker_lists()
    print(f'[sheet] TICKER_META: {len(TICKER_META)} tickers '
          f'({len(PORTFOLIO_TICKERS)} portfolio, {len(WATCHING_TICKERS)} watching)')

def fetch_coingecko_prices():
    """Fetch all crypto prices in one CoinGecko request. Returns {ticker: {price, change_pct}}."""
    ids = ','.join(COINGECKO_IDS.values())
    url = f'https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'FinancialDashboard/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        result = {}
        id_to_ticker = {v: k for k, v in COINGECKO_IDS.items()}
        for cg_id, vals in data.items():
            ticker = id_to_ticker.get(cg_id)
            if ticker:
                price = vals.get('usd')
                change_pct = vals.get('usd_24h_change')
                result[ticker] = {
                    'price':      price,
                    'change_pct': round(change_pct, 2) if change_pct is not None else None,
                    'change':     round(price * change_pct / 100, 4) if price and change_pct else None,
                }
        return result
    except Exception:
        return {}

def _parse_num(s):
    """Parse yfinance/general numeric strings."""
    if not s:
        return None
    s = str(s).strip().rstrip('%').strip().replace('US$', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/api/meta')
def meta():
    """Return all ticker metadata + categories."""
    return jsonify(TICKER_META)


@app.route('/api/portfolio')
def portfolio():
    """
    Return portfolio positions from CSV merged with live price data.
    Only portfolio-category tickers are included.
    """
    positions    = load_portfolio_from_sheet()
    crypto_prices = fetch_coingecko_prices()

    def fetch_ticker(ticker):
        pos    = positions.get(ticker, {})
        shares = pos.get('shares')
        try:
            if ticker in COINGECKO_IDS:
                cg = crypto_prices.get(ticker, {})
                price      = cg.get('price')
                change_pct = cg.get('change_pct')
                change     = cg.get('change')
            else:
                info       = yf.Ticker(ticker).info
                price      = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice')
                change_pct = info.get('regularMarketChangePercent')
                change     = info.get('regularMarketChange')

            current_value = round(float(price) * float(shares), 2) if price and shares else None
            cost_basis    = pos.get('costBasis')
            unrealized_pnl = round(current_value - cost_basis, 2) if current_value and cost_basis else None

            # Cost basis ≤ 0 means the original investment was fully recovered;
            # dividing by a negative denominator would flip the sign and produce
            # a misleading percentage, so we flag the position instead.
            recovered = bool(cost_basis is not None and cost_basis <= 0)
            if recovered or cost_basis is None or cost_basis == 0:
                unrealized_pnl_pct = None
            else:
                unrealized_pnl_pct = round((unrealized_pnl / cost_basis) * 100, 2) if unrealized_pnl else None

            return ticker, {
                **TICKER_META[ticker],
                'shares':           shares,
                'costBasis':        cost_basis,
                'currentPrice':     round(float(price), 2) if price else None,
                'currentValue':     current_value,
                'unrealizedPnl':    unrealized_pnl,
                'unrealizedPnlPct': unrealized_pnl_pct,
                'recovered':        recovered,
                'changePct':        round(float(change_pct), 2) if change_pct else None,
                'change':           round(float(change), 2) if change else None,
            }
        except Exception as exc:
            return ticker, {
                **TICKER_META.get(ticker, {}),
                'error':      str(exc),
                'shares':     shares,
                'costBasis':  pos.get('costBasis'),
            }

    result = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_ticker, t): t for t in PORTFOLIO_TICKERS}
        for future in as_completed(futures):
            ticker, data = future.result()
            result[ticker] = data

    # Cash positions (no market data needed)
    cash_entries = [
        ('ARS', 'Pesos — Money Market (MEP)', 'Argentina'),
        ('USD_CASH', 'Dólares — Money Market', 'Global'),
    ]
    for sym, name, country in cash_entries:
        pos = positions.get(sym, {})
        val = pos.get('currentValue')
        if val:
            result[sym] = {
                'name':             name,
                'type':             'Cash',
                'country':          country,
                'sector':           'Liquidez',
                'shares':           None,
                'currentPrice':     None,
                'currentValue':     round(val, 2),
                'costBasis':        round(val, 2),
                'unrealizedPnl':    0,
                'unrealizedPnlPct': 0,
                'recovered':        False,
                'changePct':        None,
                'change':           None,
            }

    return jsonify({
        'data':      result,
        'timestamp': datetime.now().strftime('%b %d %Y, %I:%M %p'),
    })


@app.route('/api/refresh')
def refresh():
    """Full market data refresh for all tickers (risk report + watching)."""
    results = {}
    errors  = []

    for ticker in ALL_TICKERS:
        try:
            t    = yf.Ticker(ticker)
            info = t.info

            hist   = t.history(period='1y', interval='1mo')
            prices = []
            if not hist.empty:
                prices = [round(float(p), 2) for p in hist['Close'].tolist()][-12:]

            price      = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice')
            change     = info.get('regularMarketChange')
            change_pct = info.get('regularMarketChangePercent')
            mkt_cap    = info.get('marketCap') or info.get('totalAssets')
            div_yield  = info.get('dividendYield') or info.get('trailingAnnualDividendYield')

            results[ticker] = {
                'price':      round(float(price), 2)      if price      is not None else None,
                'change':     round(float(change), 2)     if change     is not None else None,
                'changePct':  round(float(change_pct), 2) if change_pct is not None else None,
                'pe':         round(float(info['trailingPE']), 2) if info.get('trailingPE') else None,
                'forwardPe':  round(float(info['forwardPE']),  2) if info.get('forwardPE')  else None,
                'beta':       round(float(info['beta']), 2)       if info.get('beta')        else None,
                'marketCap':  int(mkt_cap) if mkt_cap else None,
                'week52High': round(float(info['fiftyTwoWeekHigh']), 2) if info.get('fiftyTwoWeekHigh') else None,
                'week52Low':  round(float(info['fiftyTwoWeekLow']),  2) if info.get('fiftyTwoWeekLow')  else None,
                'dividendYield': round(float(div_yield), 4) if div_yield else None,
                'prices': prices,
                **TICKER_META.get(ticker, {}),
            }

        except Exception as exc:
            errors.append({'ticker': ticker, 'error': str(exc)})
            results[ticker] = {'error': str(exc)}

    ok_count = sum(1 for v in results.values() if 'error' not in v)
    return jsonify({
        'data':      results,
        'timestamp': datetime.now().strftime('%b %d %Y, %I:%M %p'),
        'fetched':   ok_count,
        'total':     len(ALL_TICKERS),
        'errors':    errors,
    })


@app.route('/api/ohlcv/<ticker>/<interval>')
def ohlcv(ticker, interval):
    """
    OHLCV data for candlestick charts.
    interval: '4h' | '1d' | '1w'
    Returns list of {time, open, high, low, close, volume}
    """
    ticker = ticker.upper()
    interval_map = {
        '4h': ('60d',  '1h'),   # get 1h, will resample to 4h client-side or here
        '1d': ('1y',   '1d'),
        '1w': ('5y',   '1wk'),
    }
    if interval not in interval_map:
        return jsonify({'error': 'interval must be 4h, 1d, or 1w'}), 400

    period, yf_interval = interval_map[interval]

    try:
        yf_ticker = f'{ticker}-USD' if ticker in CRYPTO_YF_SUFFIX else ticker
        t    = yf.Ticker(yf_ticker)
        hist = t.history(period=period, interval=yf_interval)

        if hist.empty:
            return jsonify({'candles': [], 'ticker': ticker, 'interval': interval})

        if interval == '4h':
            # Resample 1h → 4h
            hist = hist.resample('4h').agg({
                'Open':   'first',
                'High':   'max',
                'Low':    'min',
                'Close':  'last',
                'Volume': 'sum',
            }).dropna()

        candles = []
        for ts, row in hist.iterrows():
            # TradingView Lightweight Charts expects Unix timestamps (seconds)
            time_val = int(ts.timestamp())
            candles.append({
                'time':   time_val,
                'open':   round(float(row['Open']),   4),
                'high':   round(float(row['High']),   4),
                'low':    round(float(row['Low']),    4),
                'close':  round(float(row['Close']),  4),
                'volume': int(row['Volume']),
            })

        # Compute EMA-20 on close prices
        closes = [c['close'] for c in candles]
        ema20  = _compute_ema(closes, 20)
        for i, c in enumerate(candles):
            c['ema20'] = round(ema20[i], 4) if ema20[i] is not None else None

        meta = TICKER_META.get(ticker, {})
        return jsonify({
            'ticker':   ticker,
            'name':     meta.get('name', ticker),
            'interval': interval,
            'candles':  candles,
        })

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


def _compute_ema(values, period):
    """Compute EMA for a list of floats. Returns list of same length (None for warmup period)."""
    result = [None] * len(values)
    if len(values) < period:
        return result
    k = 2 / (period + 1)
    # Seed with SMA of first `period` values
    sma = sum(values[:period]) / period
    result[period - 1] = sma
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


# ─── REPORT GENERATION ──────────────────────────────────────────────────────

def _safe(val, decimals=2, pct=False):
    try:
        v = float(val)
        return round(v * 100, decimals) if pct else round(v, decimals)
    except Exception:
        return None

def _fmt_large(val):
    """Format large numbers: 1_200_000_000 → '$1.20B'"""
    try:
        v = float(val)
        if abs(v) >= 1e12: return f'${v/1e12:.2f}T'
        if abs(v) >= 1e9:  return f'${v/1e9:.2f}B'
        if abs(v) >= 1e6:  return f'${v/1e6:.2f}M'
        return f'${v:.2f}'
    except Exception:
        return 'N/A'

def _yf_fundamentals(ticker):
    """Pull all quantitative data from yfinance for the report."""
    t = yf.Ticker(ticker)
    info = t.info

    # ── Price ──────────────────────────────────────────────────────
    price      = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice') or 0
    chg        = info.get('regularMarketChange') or 0
    chg_pct    = info.get('regularMarketChangePercent') or 0
    beta       = info.get('beta')
    pe         = info.get('trailingPE')
    fwd_pe     = info.get('forwardPE')
    mkt_cap    = info.get('marketCap') or info.get('totalAssets')
    w52_hi     = info.get('fiftyTwoWeekHigh')
    w52_lo     = info.get('fiftyTwoWeekLow')
    div_yield  = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
    eps        = info.get('trailingEps')
    sector     = info.get('sector') or info.get('category') or ''
    industry   = info.get('industry') or ''
    name       = info.get('longName') or info.get('shortName') or ticker
    quote_type = info.get('quoteType', '')
    is_etf     = quote_type in ('ETF', 'MUTUALFUND') or bool(info.get('totalAssets'))

    # ── 12-month monthly closes (May25–Apr26 approx) ───────────────
    hist_1y = t.history(period='1y', interval='1mo')
    prices_12 = []
    if not hist_1y.empty:
        prices_12 = [round(float(p), 2) for p in hist_1y['Close'].tolist()][-12:]
    while len(prices_12) < 12:
        prices_12.insert(0, prices_12[0] if prices_12 else round(float(price), 2))

    # ── Annual financials (last 4 FY) ─────────────────────────────
    rev_arr, gm_arr, om_arr, nm_arr, fcf_arr = [], [], [], [], []
    try:
        inc = t.income_stmt          # columns = fiscal year dates, newest first
        cf  = t.cashflow
        years = list(inc.columns[:4])[::-1]   # oldest→newest, up to 4 years

        for col in years:
            rev  = _safe(inc.loc['Total Revenue', col]) if 'Total Revenue' in inc.index else None
            gp   = _safe(inc.loc['Gross Profit', col])  if 'Gross Profit'  in inc.index else None
            oi   = _safe(inc.loc['Operating Income', col]) if 'Operating Income' in inc.index else None
            ni   = _safe(inc.loc['Net Income', col])    if 'Net Income'    in inc.index else None
            op_cf = _safe(cf.loc['Operating Cash Flow', col]) if cf is not None and 'Operating Cash Flow' in cf.index else None
            capex = _safe(cf.loc['Capital Expenditure', col]) if cf is not None and 'Capital Expenditure' in cf.index else None
            fcf  = round(op_cf + capex, 2) if op_cf is not None and capex is not None else None

            rev_b = round(rev / 1e9, 2) if rev else None
            fcf_b = round(fcf / 1e9, 2) if fcf else None
            gm_p  = round(gp  / rev * 100, 1) if gp  and rev else None
            om_p  = round(oi  / rev * 100, 1) if oi  and rev else None
            nm_p  = round(ni  / rev * 100, 1) if ni  and rev else None

            rev_arr.append(rev_b); gm_arr.append(gm_p)
            om_arr.append(om_p);   nm_arr.append(nm_p); fcf_arr.append(fcf_b)

        # Latest-year KPIs
        last = inc.columns[0]
        rev_ttm = inc.loc['Total Revenue', last]    if 'Total Revenue' in inc.index else None
        ni_ttm  = inc.loc['Net Income',    last]    if 'Net Income'    in inc.index else None
        gp_ttm  = inc.loc['Gross Profit',  last]    if 'Gross Profit'  in inc.index else None
        gm_ttm  = round(float(gp_ttm) / float(rev_ttm) * 100, 1) if gp_ttm and rev_ttm else None
        fcf_latest = fcf_arr[-1] if fcf_arr else None
        roe = None
        try:
            bs = t.balance_sheet
            equity = float(bs.loc['Stockholders Equity', bs.columns[0]])
            roe = round(float(ni_ttm) / equity * 100, 1) if ni_ttm and equity else None
        except Exception:
            pass
    except Exception:
        rev_ttm = ni_ttm = gm_ttm = fcf_latest = roe = None
        last = None

    # ── Quarterly table (last 4 Q) ────────────────────────────────
    qt_rows = []
    try:
        qi = t.quarterly_income_stmt
        for col in list(qi.columns[:4])[::-1]:
            q_label = col.strftime('Q%m %Y') if hasattr(col, 'strftime') else str(col)[:7]
            q_rev = qi.loc['Total Revenue', col] if 'Total Revenue' in qi.index else None
            q_ni  = qi.loc['Net Income',    col] if 'Net Income'    in qi.index else None
            qt_rows.append({
                'q': q_label,
                'rev': _fmt_large(q_rev) if q_rev else '—',
                'ni':  _fmt_large(q_ni)  if q_ni  else '—',
            })
    except Exception:
        pass

    return {
        'ticker': ticker, 'name': name, 'sector': sector, 'industry': industry,
        'is_etf': is_etf, 'price': round(float(price), 2),
        'chg': round(float(chg), 2), 'chg_pct': round(float(chg_pct), 2),
        'beta': _safe(beta), 'pe': _safe(pe), 'fwd_pe': _safe(fwd_pe),
        'mkt_cap': _fmt_large(mkt_cap), 'w52_hi': _safe(w52_hi), 'w52_lo': _safe(w52_lo),
        'div_yield': round(float(div_yield)*100, 2) if div_yield else None,
        'eps': _safe(eps), 'gm_ttm': gm_ttm, 'fcf': fcf_latest, 'roe': roe,
        'rev_fmt': _fmt_large(rev_ttm), 'ni_fmt': _fmt_large(ni_ttm),
        'prices_12': prices_12,
        'rev': rev_arr, 'gm': gm_arr, 'om': om_arr, 'nm': nm_arr, 'fcf_arr': fcf_arr,
        'qt_rows': qt_rows,
    }


def _call_gemma(prompt):
    """Call Nvidia Gemma API and return the text response."""
    if not NVIDIA_API_KEY:
        return None
    try:
        resp = http_requests.post(
            NVIDIA_API_URL,
            headers={'Authorization': f'Bearer {NVIDIA_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': NVIDIA_MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 1200,
                'temperature': 0.3,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f'Gemma error: {e}')
        return None


def _extract_json(text):
    """Extract first JSON object from a text string."""
    if not text:
        return None
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except Exception:
        return None


def _build_report(fd, ai):
    """Merge yfinance fundamentals with Gemma AI analysis into final report JSON."""
    ai = ai or {}
    ticker = fd['ticker']

    # ── Type label ──────────────────────────────────────────────────
    type_label = ai.get('type') or (
        f"ETF · {fd['sector']}" if fd['is_etf'] else
        f"{fd['sector']} · {fd['industry']}" if fd['sector'] else ticker
    )

    # ── KPIs ────────────────────────────────────────────────────────
    if fd['is_etf']:
        kpis = [
            ['AUM',           fd['mkt_cap']],
            ['Expense Ratio', f"{fd['pe']}x" if fd['pe'] else 'N/A'],
            ['Div. Yield',    f"{fd['div_yield']}%" if fd['div_yield'] else '0%'],
            ['52W High',      f"${fd['w52_hi']}" if fd['w52_hi'] else 'N/A'],
            ['52W Low',       f"${fd['w52_lo']}" if fd['w52_lo'] else 'N/A'],
            ['Beta',          str(fd['beta']) if fd['beta'] else 'N/A'],
        ]
    else:
        kpis = [
            ['Revenue',      fd['rev_fmt'] or 'N/A'],
            ['Net Income',   fd['ni_fmt']  or 'N/A'],
            ['Gross Margin', f"{fd['gm_ttm']}%" if fd['gm_ttm'] else 'N/A'],
            ['Free CF',      f"${fd['fcf']}B" if fd['fcf'] else 'N/A'],
            ['EPS',          f"${fd['eps']}" if fd['eps'] else 'N/A'],
            ['ROE',          f"{fd['roe']}%" if fd['roe'] else 'N/A'],
        ]

    # ── Scores ──────────────────────────────────────────────────────
    score = ai.get('score', [18, 20, 16])
    if not isinstance(score, list) or len(score) != 3:
        score = [18, 20, 16]

    # ── Valuation & Health cards ─────────────────────────────────
    def default_vc():
        return [
            ['P/E Ratio',    str(fd['pe']) if fd['pe'] else 'N/A', 'Trailing twelve months', 'a'],
            ['Fwd P/E',      str(fd['fwd_pe']) if fd['fwd_pe'] else 'N/A', 'Forward estimate', 'a'],
            ['52W High',     f"${fd['w52_hi']}" if fd['w52_hi'] else 'N/A', '52-week high', 'g'],
            ['52W Low',      f"${fd['w52_lo']}" if fd['w52_lo'] else 'N/A', '52-week low', 'r'],
            ['Market Cap',   fd['mkt_cap'] or 'N/A', '', 'a'],
            ['Div. Yield',   f"{fd['div_yield']}%" if fd['div_yield'] else '0%', 'Annual yield', 'a'],
        ]
    def default_hc():
        return [
            ['Gross Margin', f"{fd['gm_ttm']}%" if fd['gm_ttm'] else 'N/A', 'TTM', 'g'],
            ['Beta',         str(fd['beta']) if fd['beta'] else 'N/A', 'Market correlation', 'a'],
            ['EPS',          f"${fd['eps']}" if fd['eps'] else 'N/A', 'Trailing', 'g'],
            ['ROE',          f"{fd['roe']}%" if fd['roe'] else 'N/A', 'Return on equity', 'g'],
            ['FCF',          f"${fd['fcf']}B" if fd['fcf'] else 'N/A', 'Free cash flow', 'g'],
            ['Revenue',      fd['rev_fmt'] or 'N/A', 'TTM', 'g'],
        ]

    vc = ai.get('vc') or default_vc()
    hc = ai.get('hc') or default_hc()

    # ── Quarterly table ──────────────────────────────────────────
    qt_ai = ai.get('qt', [])
    if qt_ai:
        qt = qt_ai
    else:
        qt = [[r['q'], r['rev'], '—', '—', '—', '—', ''] for r in fd['qt_rows']] or \
             [['Q1 2026', '—', '—', '—', '—', '—', 'Data pending']]

    # ── Segments ────────────────────────────────────────────────
    segs = ai.get('segs', [{'l': fd['sector'] or 'Core Business', 'v': 1}])

    return {
        'ticker':  ticker,
        'name':    fd['name'],
        'type':    type_label,
        'is_etf':  fd['is_etf'],
        'price':   fd['price'],
        'chg':     fd['chg'],
        'chg_pct': fd['chg_pct'],
        'score':   score,
        'kpis':    kpis,
        'prices':  fd['prices_12'],
        'events':  ai.get('events', []),
        'vc':      vc,
        'hc':      hc,
        'qt':      qt,
        'update':  ai.get('update', f'{ticker} — análisis generado automáticamente.'),
        'cats':    ai.get('cats', ['Crecimiento del sector', 'Expansión de márgenes', 'Catalizador estratégico']),
        'risks':   ai.get('risks', ['Presión competitiva', 'Riesgo macroeconómico', 'Valuación elevada']),
        'verdict': ai.get('verdict', 'HOLD'),
        'vtext':   ai.get('vtext', 'Análisis en proceso. Consultá los fundamentos antes de operar.'),
        # Financial tab data
        'rev':     fd['rev'],
        'gm':      fd['gm'],
        'om':      fd['om'],
        'nm':      fd['nm'],
        'fcf_arr': fd['fcf_arr'],
        'segs':    segs,
    }


def _lang_instruction(lang):
    """Return a language instruction string to append to AI prompts."""
    if lang and lang.lower().startswith('es'):
        return '\n\nIMPORTANT: Write ALL text values in Spanish (castellano). Do not translate ticker symbols, financial metrics labels, or numeric values.'
    return '\n\nIMPORTANT: Write ALL text values in English.'


def _portfolio_context_rows(positions):
    """Build a list of portfolio position rows merged with CSV data for use in prompts."""
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    crypto_prices = fetch_coingecko_prices()
    rows = []
    total_value = 0.0
    total_cost  = 0.0
    total_pnl   = 0.0
    day_change  = 0.0

    for ticker in PORTFOLIO_TICKERS:
        pos    = positions.get(ticker, {})
        shares = pos.get('shares')
        try:
            if ticker in COINGECKO_IDS:
                cg      = crypto_prices.get(ticker, {})
                price   = cg.get('price') or 0.0
                chg_pct = cg.get('change_pct') or 0.0
            else:
                info    = yf.Ticker(ticker).info
                price   = float(info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice') or 0)
                chg_pct = float(info.get('regularMarketChangePercent') or 0)
        except Exception:
            price, chg_pct = 0.0, 0.0

        cost    = pos.get('costBasis') or 0.0
        cur_val = round(price * float(shares or 0), 2)
        pnl     = round(cur_val - cost, 2)
        pnl_pct = round((pnl / cost) * 100, 2) if cost else 0.0
        meta    = TICKER_META.get(ticker, {})

        total_value += cur_val
        total_cost  += cost
        total_pnl   += pnl
        day_change  += cur_val * chg_pct / 100

        rows.append({
            'ticker':  ticker,
            'name':    meta.get('name', ticker),
            'type':    meta.get('type', ''),
            'sector':  meta.get('sector', ''),
            'country': meta.get('country', ''),
            'shares':  shares,
            'cost':    round(cost, 2),
            'value':   cur_val,
            'pnl':     pnl,
            'pnl_pct': pnl_pct,
            'chg_pct': round(chg_pct, 2),
        })

    for r in rows:
        r['weight'] = round(r['value'] / total_value * 100, 2) if total_value else 0

    total_pnl_pct = round((total_pnl / total_cost) * 100, 2) if total_cost else 0.0
    return rows, {
        'value':    round(total_value, 2),
        'cost':     round(total_cost, 2),
        'pnl':      round(total_pnl, 2),
        'pnlPct':   total_pnl_pct,
        'dayChange': round(day_change, 2),
    }


@app.route('/api/portfolio/analysis', methods=['POST'])
def portfolio_analysis():
    """Call Gemma to analyze the full portfolio and return narrative + recommendations."""
    try:
        positions = load_portfolio_from_sheet()
        lang = request.json.get('lang', 'en') if request.is_json else 'en'
        rows, totals = _portfolio_context_rows(positions)

        lang_instruction = _lang_instruction(lang)
        prompt = f"""You are an expert portfolio manager and financial analyst. Analyze this investment portfolio and provide a concise strategic assessment.
{lang_instruction}

Portfolio summary:
- Total value: ${totals['value']:,.2f}
- Total cost basis: ${totals['cost']:,.2f}
- Unrealized P&L: ${totals['pnl']:+,.2f} ({totals['pnlPct']:+.2f}%)
- Today's change: ${totals['dayChange']:+,.2f}
- Number of positions: {len(rows)}

Positions (ticker, type, sector, country, weight%, P&L%):
{chr(10).join(f"  {r['ticker']} | {r['type']} | {r['sector']} | {r['country']} | {r['weight']:.1f}% | {r['pnl_pct']:+.1f}%" for r in sorted(rows, key=lambda x: -x['weight']))}

Return ONLY a valid JSON object (no markdown, no explanation). All string values must follow the language instruction above:
{{
  "summary": "2-3 sentence executive summary of the portfolio's current state.",
  "diversification": "Assessment of diversification across types, sectors and geographies.",
  "strengths": ["Strength 1", "Strength 2", "Strength 3"],
  "concerns": ["Concern 1", "Concern 2", "Concern 3"],
  "recommendations": ["Action 1", "Action 2", "Action 3"],
  "outlook": "Short-term (1-3 month) outlook given current market conditions.",
  "risk_level": "LOW|MODERATE|HIGH|VERY HIGH"
}}"""

        raw = _call_gemma(prompt)
        ai  = _extract_json(raw) or {}

        return jsonify({
            'ok': True,
            'positions': rows,
            'totals': totals,
            'analysis': ai,
            'timestamp': datetime.now().strftime('%b %d %Y, %I:%M %p'),
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/generate-report/<ticker>', methods=['POST'])
def generate_report(ticker):
    ticker = ticker.upper().strip()
    report_path = os.path.join(REPORTS_DIR, f'{ticker}.json')

    try:
        lang = request.json.get('lang', 'en') if request.is_json else 'en'

        # 1. Pull yfinance data
        fd = _yf_fundamentals(ticker)

        # 2. Build Gemma prompt
        lang_instruction = _lang_instruction(lang)
        prompt = f"""You are a financial analyst. Generate a risk and financial analysis for {ticker} ({fd['name']}).
{lang_instruction}

Current data:
- Price: ${fd['price']}, change: {fd['chg']:+.2f} ({fd['chg_pct']:+.2f}%)
- P/E: {fd['pe']}, Fwd P/E: {fd['fwd_pe']}, Beta: {fd['beta']}
- Market Cap: {fd['mkt_cap']}, 52W: ${fd['w52_lo']} – ${fd['w52_hi']}
- Gross Margin: {fd['gm_ttm']}%, ROE: {fd['roe']}%, EPS: ${fd['eps']}
- Revenue trend (oldest→newest, $B): {fd['rev']}
- Net margin trend (%): {fd['nm']}
- Sector: {fd['sector']}, Industry: {fd['industry']}
- Recent quarters: {[r['q']+' '+r['rev'] for r in fd['qt_rows']]}

Return ONLY a valid JSON object (no markdown, no explanation). All string values must follow the language instruction above:
{{
  "type": "Sector · Sub-sector one-liner",
  "score": [valuation_0_to_35, health_0_to_35, technical_0_to_30],
  "segs": [{{"l": "Segment Name", "v": revenue_in_billions}}, ...],
  "vc": [["Label","Value","Context/Reference","g|r|a"], ... 6 items],
  "hc": [["Label","Value","Context/Reference","g|r|a"], ... 6 items],
  "qt": [["Q1 2026","$Xb","+Y%","$Z","+W%","BEAT|MISS|HOLD","Short note"], ... 4 rows],
  "events": [{{"i": 0_to_11, "t": "Short label"}}],
  "update": "2-3 sentence earnings/delivery update narrative.",
  "cats": ["Catalyst 1", "Catalyst 2", "Catalyst 3"],
  "risks": ["Risk 1", "Risk 2", "Risk 3"],
  "verdict": "STRONG BUY|BUY|HOLD|CAUTION|SELL|HIGH RISK",
  "vtext": "2-3 sentence bottom-line explanation with price target or action."
}}"""

        # 3. Call Gemma
        raw = _call_gemma(prompt)
        ai  = _extract_json(raw)

        # 4. Build final report
        report = _build_report(fd, ai)

        # 5. Save to disk
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 6. Update TICKER_META if new ticker
        if ticker not in TICKER_META:
            TICKER_META[ticker] = {
                'name':     fd['name'],
                'type':     'ETF' if fd['is_etf'] else 'Stock',
                'country':  'USA',
                'sector':   fd['sector'] or '',
                'category': request.json.get('category', 'watching') if request.is_json else 'watching',
            }

        return jsonify({'ok': True, 'report': report})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/financials/<ticker>')
def get_financials(ticker):
    """Return structured financial data for any ticker via yfinance."""
    ticker = ticker.upper().strip()
    try:
        t = yf.Ticker(ticker)
        info = t.info
        quote_type = info.get('quoteType', '')
        is_etf = quote_type in ('ETF', 'MUTUALFUND') or bool(info.get('totalAssets') and not info.get('marketCap'))
        name = info.get('longName') or info.get('shortName') or ticker
        sector = info.get('sector') or info.get('category') or ''
        industry = info.get('industry') or ''
        price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice') or 0
        pe = _safe(info.get('trailingPE'))
        fwd_pe = _safe(info.get('forwardPE'))
        mkt_cap = info.get('marketCap') or info.get('totalAssets')
        div_yield = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
        eps = _safe(info.get('trailingEps'))
        beta = _safe(info.get('beta'))

        kpis = [
            ['Price', f'${float(price):.2f}'],
            ['P/E', f'{pe}x' if pe else '—'],
            ['Fwd P/E', f'{fwd_pe}x' if fwd_pe else '—'],
            ['Mkt Cap', _fmt_large(mkt_cap) if mkt_cap else '—'],
            ['Div Yield', f'{div_yield*100:.1f}%' if div_yield else '—'],
            ['EPS', f'${eps}' if eps else '—'],
            ['Beta', str(beta) if beta else '—'],
        ]

        rev_arr, gm_arr, om_arr, nm_arr, fcf_arr, year_labels, annual_returns = [], [], [], [], [], [], []
        segs = []

        if is_etf:
            try:
                hist = t.history(period='5y', interval='1mo')
                if not hist.empty:
                    hist.index = pd.to_datetime(hist.index)
                    hist['year'] = hist.index.year
                    yearly = hist.groupby('year')['Close'].agg(['first', 'last'])
                    for yr, row in yearly.tail(5).iterrows():
                        annual_returns.append(round((row['last'] / row['first'] - 1) * 100, 1))
                        year_labels.append(f'FY{str(yr)[2:]}')
            except Exception:
                pass
            holdings = info.get('holdings', [])
            for h in holdings[:8]:
                segs.append({'l': h.get('holdingName', '?'), 'v': round(float(h.get('holdingPercent', 0)) * 100, 1)})
            if not segs:
                segs = [{'l': sector or 'Diversified', 'v': 100}]
        else:
            try:
                inc = t.income_stmt
                cf = t.cashflow
                cols = list(inc.columns[:5])[::-1]
                for col in cols:
                    yr_str = f'FY{str(col.year)[2:]}' if hasattr(col, 'year') else str(col)[:4]
                    year_labels.append(yr_str)
                    rev  = float(inc.loc['Total Revenue', col])    if 'Total Revenue'    in inc.index else None
                    gp   = float(inc.loc['Gross Profit', col])     if 'Gross Profit'     in inc.index else None
                    oi   = float(inc.loc['Operating Income', col]) if 'Operating Income' in inc.index else None
                    ni   = float(inc.loc['Net Income', col])       if 'Net Income'       in inc.index else None
                    op_cf = float(cf.loc['Operating Cash Flow', col]) if cf is not None and 'Operating Cash Flow' in cf.index else None
                    capex = float(cf.loc['Capital Expenditure', col]) if cf is not None and 'Capital Expenditure' in cf.index else None
                    fcf = round(op_cf + capex, 2) if op_cf is not None and capex is not None else None
                    rev_b = round(rev / 1e9, 2) if rev else None
                    fcf_b = round(fcf / 1e9, 2) if fcf else None
                    gm_p  = round(gp  / rev * 100, 1) if gp  and rev else None
                    om_p  = round(oi  / rev * 100, 1) if oi  and rev else None
                    nm_p  = round(ni  / rev * 100, 1) if ni  and rev else None
                    rev_arr.append(rev_b); gm_arr.append(gm_p)
                    om_arr.append(om_p);   nm_arr.append(nm_p); fcf_arr.append(fcf_b)
            except Exception:
                pass
            segs = [{'l': sector or 'Total Revenue', 'v': 100}]

        max_rev = max((v for v in rev_arr if v is not None), default=0)
        unit = '$M' if max_rev < 2 else '$B'
        type_str = f'{sector} · {industry}' if sector and industry else (sector or industry or ('ETF' if is_etf else 'Stock'))

        return jsonify({
            'name': name, 'type': type_str, 'etf': is_etf,
            'kpis': kpis, 'segs': segs,
            'rev': rev_arr, 'fcf': fcf_arr,
            'gm': gm_arr, 'om': om_arr, 'nm': nm_arr,
            'unit': unit, 'revUnit': unit,
            'years': year_labels,
            'annualReturns': annual_returns,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/report/<ticker>')
def get_report(ticker):
    """Return a previously generated report from disk."""
    ticker = ticker.upper().strip()
    report_path = os.path.join(REPORTS_DIR, f'{ticker}.json')
    if not os.path.exists(report_path):
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    with open(report_path, encoding='utf-8') as f:
        return jsonify({'ok': True, 'report': json.load(f)})


@app.route('/api/reports')
def list_reports():
    """List all tickers that have generated reports saved."""
    files = [f.replace('.json', '') for f in os.listdir(REPORTS_DIR) if f.endswith('.json')]
    return jsonify(files)


@app.route('/api/chat', methods=['POST'])
def chat():
    """
    Portfolio-aware conversational chat endpoint.
    Body: { message, history: [{role, content}], lang }
    """
    try:
        body    = request.get_json(force=True)
        message = (body.get('message') or '').strip()
        history = body.get('history') or []
        lang    = body.get('lang', 'en')

        if not message:
            return jsonify({'ok': False, 'error': 'Empty message'}), 400

        # Build portfolio context snapshot (non-blocking, best-effort)
        positions = load_portfolio_from_sheet()
        pf_lines  = []
        for ticker in PORTFOLIO_TICKERS:
            pos  = positions.get(ticker, {})
            meta = TICKER_META.get(ticker, {})
            cost = pos.get('costBasis')
            pf_lines.append(
                f"  {ticker} ({meta.get('name','')}) | {meta.get('type','')} | "
                f"{meta.get('sector','')} | {meta.get('country','')} | "
                f"shares: {pos.get('shares','?')} | cost basis: ${cost or '?'}"
            )

        lang_note = (
            'Always respond in Spanish (castellano).'
            if lang.lower().startswith('es')
            else 'Always respond in English.'
        )

        system_prompt = f"""You are an expert financial advisor and investment analyst with deep knowledge of stocks, ETFs, crypto, and portfolio management. You have full visibility into the user's investment portfolio.

{lang_note}

PORTFOLIO COMPOSITION (as of latest snapshot):
{chr(10).join(pf_lines) if pf_lines else '  No portfolio data available.'}

Key information about the user:
- Investor level: intermediate (not expert)
- Goals: track and analyze existing positions, evaluate potential acquisitions/sales, keep portfolio balanced and optimized
- You should help them learn and operate better

Answer their question concisely and accurately. When relevant, reference their specific holdings. Be educational but practical."""

        # Build messages list: system + history + new user message
        messages = [{'role': 'user', 'content': system_prompt + '\n\n[System loaded. Ready to assist.]'}]
        messages.append({'role': 'assistant', 'content': 'Understood. I have your portfolio context loaded. How can I help you?'})

        for h in history[-10:]:  # keep last 10 turns to stay within token limits
            role    = h.get('role', 'user')
            content = h.get('content', '')
            if role in ('user', 'assistant') and content:
                messages.append({'role': role, 'content': content})

        messages.append({'role': 'user', 'content': message})

        if not NVIDIA_API_KEY:
            return jsonify({'ok': False, 'error': 'NVIDIA_API_KEY not configured'}), 503

        resp = http_requests.post(
            NVIDIA_API_URL,
            headers={'Authorization': f'Bearer {NVIDIA_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model':       NVIDIA_MODEL,
                'messages':    messages,
                'max_tokens':  800,
                'temperature': 0.4,
            },
            timeout=60,
        )
        resp.raise_for_status()
        reply = resp.json()['choices'][0]['message']['content']

        return jsonify({'ok': True, 'reply': reply})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ─── MAIN ────────────────────────────────────────────────────────────────────

_init_ticker_meta()   # load tickers from Google Sheet before serving

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5050)
    args = parser.parse_args()

    print()
    print('  ┌─────────────────────────────────────────────┐')
    print('  │  Financial Dashboard · Local Server         │')
    print(f'  │  http://localhost:{args.port}                      │')
    print('  └─────────────────────────────────────────────┘')
    print()
    app.run(debug=False, port=args.port)
