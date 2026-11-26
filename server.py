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
import math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import csv
import re
import time
import urllib.request
import json as _json
import json
import requests as http_requests
from dotenv import load_dotenv

load_dotenv()   # before the local imports: they read PORTFOLIO_CSV & co. at import time

import financials
import portfolio_data
import risk
import narrative
import valuations
import agent
import explore

# ─── IN-MEMORY TTL QUOTE CACHE ────────────────────────────────────────────────
QUOTE_CACHE = {}  # ticker -> {'data': dict, 'timestamp': float}

def get_cached_quote(ticker, max_age_seconds=45):
    """Retrieve quote from in-memory cache if not older than max_age_seconds."""
    ticker = ticker.upper().strip()
    entry = QUOTE_CACHE.get(ticker)
    if not entry:
        return None
    if time.time() - entry['timestamp'] > max_age_seconds:
        return None
    return entry['data']

def put_cached_quote(ticker, data):
    """Store quote dictionary in memory cache tagged with current epoch timestamp."""
    ticker = ticker.upper().strip()
    QUOTE_CACHE[ticker] = {
        'data': data,
        'timestamp': time.time()
    }

# ─── IN-MEMORY TTL OHLCV CACHE ───────────────────────────────────────────────
OHLCV_CACHE = {}  # (ticker, interval) -> {'data': dict, 'timestamp': float}
TICKER_VALIDATION_REGEX = re.compile(r'^[A-Z0-9.\-^]{1,15}$')

OHLCV_INTERVAL_MAP = {
    '4h': ('6mo', '1h',  900),     # 6 months (resampled 1h -> 4h), TTL 15 min
    '1d': ('2y',  '1d',  3600),    # 2 years daily, TTL 1 hour
    '1w': ('10y', '1wk', 86400),   # 10 years weekly, TTL 24 hours
}

def get_cached_ohlcv(ticker, interval, max_age_seconds=None):
    """Retrieve OHLCV data from in-memory cache if not expired."""
    key = (ticker.upper().strip(), interval)
    entry = OHLCV_CACHE.get(key)
    if not entry:
        return None
    ttl = max_age_seconds if max_age_seconds is not None else OHLCV_INTERVAL_MAP.get(interval, (None, None, 300))[2]
    if time.time() - entry['timestamp'] > ttl:
        return None
    return entry['data']

def put_cached_ohlcv(ticker, interval, data):
    """Store OHLCV data in in-memory cache."""
    key = (ticker.upper().strip(), interval)
    OHLCV_CACHE[key] = {
        'data': data,
        'timestamp': time.time()
    }


app = Flask(__name__)

if os.environ.get('PORTFOLIO_LAB', '1').strip().lower() not in ('0', 'false', 'off', 'no'):
    try:
        from lab.api import register as _register_lab
        _register_lab(app)
    except Exception as _lab_err:  # el Lab nunca debe impedir que el dashboard arranque
        print(f'[lab] no se pudo cargar Portfolio Lab: {_lab_err}')

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

NVIDIA_API_URL  = 'https://integrate.api.nvidia.com/v1/chat/completions'
NVIDIA_MODEL    = os.environ.get('NVIDIA_MODEL') or 'nvidia/nemotron-3.5-lightning-30b-a3b'  # NIM retira modelos: se cambia desde .env
NVIDIA_API_KEY  = os.environ.get('NVIDIA_API_KEY', '')

# ─── TICKER METADATA ────────────────────────────────────────────────────────
# Data-driven: built from PORTFOLIO_CSV (category 'portfolio') plus the optional WATCHLIST_CSV
# (categories 'watching' / 'archived'). Defaults point to the fictional files in sample_data/.
# See portfolio_data.py for the file formats.

TICKER_META = {}
PORTFOLIO_TICKERS = []
WATCHING_TICKERS = []
ACTIVE_TICKERS = []
ALL_TICKERS = []
# yfinance requires the -USD suffix for crypto (e.g. 'SOL-USD')
CRYPTO_YF_SUFFIX = set()
# CoinGecko ids for crypto tickers (single-request batch fetch)
COINGECKO_IDS = {}
PORTFOLIO_ERRORS = []


def _refresh_ticker_lists():
    """Rebuild TICKER_META and the derived lists from the CSV files (cheap: a few small files)."""
    global TICKER_META, PORTFOLIO_TICKERS, WATCHING_TICKERS, ACTIVE_TICKERS, ALL_TICKERS
    global CRYPTO_YF_SUFFIX, COINGECKO_IDS, PORTFOLIO_ERRORS
    pf = portfolio_data.load_portfolio()
    wl = portfolio_data.load_watchlist()
    TICKER_META = portfolio_data.build_ticker_meta(pf, wl)
    PORTFOLIO_TICKERS = [t for t, m in TICKER_META.items() if m['category'] == 'portfolio']
    WATCHING_TICKERS = [t for t, m in TICKER_META.items() if m['category'] == 'watching']
    ACTIVE_TICKERS = [t for t, m in TICKER_META.items() if m.get('category') != 'archived']
    ALL_TICKERS = list(TICKER_META.keys())
    CRYPTO_YF_SUFFIX = portfolio_data.crypto_tickers(pf, wl)
    COINGECKO_IDS = portfolio_data.coingecko_ids(pf, wl)
    PORTFOLIO_ERRORS = pf['errors'] + wl['errors']
    for e in PORTFOLIO_ERRORS:
        print(f"[portfolio CSV] {e.get('file')} row {e.get('row')}: {e.get('error')}")
    return pf


_refresh_ticker_lists()

_CRYPTO_CACHE = {'data': {}, 'ts': 0}

def fetch_coingecko_prices():
    """Fetch all crypto prices in one CoinGecko request with cache & User-Agent fallback. Returns {ticker: {price, change_pct, change}}."""
    now = time.time()
    if _CRYPTO_CACHE['data'] and (now - _CRYPTO_CACHE['ts']) < 60:
        return _CRYPTO_CACHE['data']

    ids = ','.join(COINGECKO_IDS.values())
    url = f'https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
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
        if result:
            _CRYPTO_CACHE['data'] = result
            _CRYPTO_CACHE['ts']   = now
        return result or _CRYPTO_CACHE.get('data', {})
    except Exception as exc:
        print(f"[CoinGecko Error] {exc}")
        return _CRYPTO_CACHE.get('data', {})

def _num_or_none(v, decimals=None):
    """Número finito o None (regla 4: un dato faltante es null, nunca 0). Un 0 real se conserva."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, decimals) if decimals is not None else f


def _parse_num(s):
    """Parse Spanish-locale numbers: '1.479' → 1479, '20,08 %' → 20.08"""
    if not s:
        return None
    s = s.strip().rstrip('%').strip()
    s = s.replace('US$', '').replace('$', '').replace('€', '').replace('R$', '').strip()
    # Remove thousands dots, swap decimal comma
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None

def _is_valid_ticker_symbol(sym):
    """Check if string is a valid financial ticker symbol (e.g. JPM, VTI, SOL)."""
    if not sym or not isinstance(sym, str):
        return False
    sym = sym.strip().upper()
    if ' ' in sym or len(sym) < 1 or len(sym) > 6 or not sym.isalnum():
        return False
    INVALID = {'PESOS', 'DOLARES', 'DOLAR', 'EUROS', 'TOTAL', 'CASH'}
    return sym not in INVALID


def load_portfolio_from_csv():
    """
    Read PORTFOLIO_CSV and CASH_CSV (defaults: sample_data/) and return ticker -> position data:
      - holdings: {'shares', 'costBasis', 'recovered'}
      - cash:     'USD_CASH' for US dollars, the ISO code for other currencies (ARS, EUR...),
                  each {'currentValue' (USD), 'currency', 'amount', 'usdRate', 'name', 'country', 'cash': True}
    The files are re-read on every call, so edits show up without restarting the server.
    """
    pf = _refresh_ticker_lists()
    return portfolio_data.positions(pf)



# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
@app.route('/apple-touch-icon')
def apple_touch_icon():
    return send_from_directory(BASE_DIR, 'apple-touch-icon.png', mimetype='image/png')


@app.route('/favicon-32x32.png')
@app.route('/favicon.png')
def favicon_32():
    return send_from_directory(BASE_DIR, 'favicon-32x32.png', mimetype='image/png')


@app.route('/favicon-16x16.png')
def favicon_16():
    return send_from_directory(BASE_DIR, 'favicon-16x16.png', mimetype='image/png')


@app.route('/favicon.jpeg')
@app.route('/favicon.jpg')
def favicon_jpeg():
    return send_from_directory(BASE_DIR, 'favicon.jpeg', mimetype='image/jpeg')


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(BASE_DIR, 'favicon-32x32.png', mimetype='image/png')


@app.route('/api/ticker-metadata')
@app.route('/api/metadata')
@app.route('/api/meta')
def meta():
    """Return all ticker metadata + categories."""
    return jsonify(TICKER_META)


def fetch_quote_data(ticker):
    ticker_clean = ticker.upper().strip()
    cached = get_cached_quote(ticker_clean, max_age_seconds=45)
    if cached:
        return cached

    try:
        if ticker_clean in COINGECKO_IDS:
            crypto_prices = fetch_coingecko_prices()
            cg = crypto_prices.get(ticker_clean, {})
            price = cg.get('price')
            change_pct = cg.get('change_pct')
            change = cg.get('change')
            if price is not None:
                q = {'price': float(price), 'chg': _num_or_none(change), 'chg_pct': _num_or_none(change_pct)}
                put_cached_quote(ticker_clean, q)
                return q
        else:
            yf_ticker = f"{ticker_clean}-USD" if ticker_clean in CRYPTO_YF_SUFFIX else ticker_clean
            t_obj = yf.Ticker(yf_ticker)
            info = t_obj.info or {}
            price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice')
            change_pct = info.get('regularMarketChangePercent')
            change = info.get('regularMarketChange')
            if price is not None:
                q = {'price': float(price), 'chg': _num_or_none(change), 'chg_pct': _num_or_none(change_pct)}
                put_cached_quote(ticker_clean, q)
                return q
    except Exception as e:
        print(f"Error fetching quote for {ticker_clean}: {e}")

    return None


def _validate_ticker(ticker):
    """
    Validate that ticker symbol is a real, fetchable financial asset.
    Returns (is_valid: bool, error_message: str|None).
    """
    ticker = ticker.upper().strip()
    if not ticker or len(ticker) > 12 or not ticker.isalnum():
        return False, f"Formato de símbolo inválido '{ticker}'"

    # Built-in known tickers are valid
    if ticker in TICKER_META or ticker in COINGECKO_IDS:
        return True, None

    # Check via Yahoo Finance / CoinGecko
    try:
        yf_sym = f'{ticker}-USD' if ticker in CRYPTO_YF_SUFFIX else ticker
        t_info = yf.Ticker(yf_sym).info
        price = t_info.get('regularMarketPrice') or t_info.get('currentPrice') or t_info.get('previousClose') or t_info.get('navPrice')
        sym_name = t_info.get('symbol') or t_info.get('shortName') or t_info.get('longName')
        if price is not None or sym_name is not None:
            return True, None
    except Exception:
        pass

    # Check CoinGecko
    try:
        cg_prices = fetch_coingecko_prices()
        if ticker in cg_prices and cg_prices[ticker].get('price') is not None:
            return True, None
    except Exception:
        pass

    return False, f"El símbolo '{ticker}' no pudo ser verificado en mercados financieros."


@app.route('/api/quotes')
def batch_quotes():
    """
    Return live price, change, and change_pct for requested or active tickers.
    Uses ThreadPoolExecutor to fetch quotes concurrently with 45s TTL cache.
    """
    tickers_param = request.args.get('tickers', '')
    if tickers_param:
        requested = [t.strip().upper() for t in tickers_param.split(',') if t.strip()]
    else:
        requested = list(ACTIVE_TICKERS)

    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(fetch_quote_data, t): t for t in requested}
        for future in as_completed(future_to_ticker):
            t = future_to_ticker[future]
            try:
                data = future.result()
                if data:
                    results[t] = data
            except Exception as err:
                pass

    return jsonify({'quotes': results, 'timestamp': datetime.now(timezone.utc).isoformat()})


@app.route('/api/portfolio')
def portfolio():
    """
    Return portfolio positions from CSV merged with live price data.
    Only portfolio-category tickers are included.
    """
    positions    = load_portfolio_from_csv()
    crypto_prices = fetch_coingecko_prices()

    def fetch_ticker(ticker):
        pos    = positions.get(ticker, {})
        shares = pos.get('shares')
        meta   = TICKER_META.get(ticker, {})
        is_crypto = ticker in COINGECKO_IDS or ticker in CRYPTO_YF_SUFFIX
        try:
            if ticker in COINGECKO_IDS:
                cg = crypto_prices.get(ticker, {})
                price      = cg.get('price')
                change_pct = cg.get('change_pct')
                change     = cg.get('change')
                if price is None and ticker in CRYPTO_YF_SUFFIX:
                    try:
                        info = yf.Ticker(f"{ticker}-USD").info
                        price = _num_or_none(info.get('currentPrice') or info.get('regularMarketPrice'))
                        change_pct = _num_or_none(info.get('regularMarketChangePercent'))
                        change = _num_or_none(info.get('regularMarketChange'))
                    except Exception:
                        pass
            else:
                info       = yf.Ticker(f"{ticker}-USD" if ticker in CRYPTO_YF_SUFFIX else ticker).info
                price      = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice')
                change_pct = info.get('regularMarketChangePercent')
                change     = info.get('regularMarketChange')

            # Without a live price the position is shown at cost (flagged with priceSource).
            current_value = round(float(price) * float(shares), 2) if (price is not None and shares is not None) else pos.get('costBasis')

            cost_basis    = pos.get('costBasis')
            unrealized_pnl = round(current_value - cost_basis, 2) if (current_value is not None and cost_basis is not None) else None

            # Cost basis < 4 (or <= 0) for active holding means investment was fully recovered
            is_active = bool(shares and shares > 0.001 and current_value and current_value > 0)
            recovered = bool(is_active and cost_basis is not None and cost_basis < 4)
            if recovered or cost_basis is None or cost_basis <= 0:
                unrealized_pnl_pct = None
            else:
                unrealized_pnl_pct = round((unrealized_pnl / cost_basis) * 100, 2) if unrealized_pnl is not None else None

            ticker_type = 'Crypto' if is_crypto else meta.get('type', 'Stock')
            ticker_sector = 'Crypto' if is_crypto else meta.get('sector', 'Other')
            ticker_country = 'Global' if is_crypto else meta.get('country', 'USA')

            return ticker, {
                **meta,
                'type':             ticker_type,
                'sector':           ticker_sector,
                'country':          ticker_country,
                'shares':           shares,
                'costBasis':        cost_basis,
                'currentPrice':     round(float(price), 2) if price else None,
                'currentValue':     current_value,
                'unrealizedPnl':    unrealized_pnl,
                'unrealizedPnlPct': unrealized_pnl_pct,
                'recovered':        recovered,
                'changePct':        _num_or_none(change_pct, 2),
                'change':           _num_or_none(change, 2),
                'priceSource':      'live' if price is not None else 'cost_basis',
            }
        except Exception as exc:
            cur_val = pos.get('costBasis')
            return ticker, {
                **meta,
                'type':         'Crypto' if is_crypto else meta.get('type', 'Stock'),
                'error':        str(exc),
                'shares':       shares,
                'costBasis':    pos.get('costBasis'),
                'currentValue': cur_val,
                # sin precio en vivo no se calcula nada: null explícito, nunca 0
                'currentPrice':     None,
                'unrealizedPnl':    None,
                'unrealizedPnlPct': None,
                'changePct':        None,
                'change':           None,
                'recovered':    bool(pos.get('costBasis') is not None and pos.get('costBasis') < 4 and shares and shares > 0.001),
            }

    result = {}
    tickers_to_query = [
        t for t in dict.fromkeys(PORTFOLIO_TICKERS + list(positions.keys()))
        if not positions.get(t, {}).get('cash')
        and TICKER_META.get(t, {}).get('category') != 'archived'
        and (positions.get(t, {}).get('shares') or 0) > 0.001
    ]
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_ticker, t): t for t in tickers_to_query}
        for future in as_completed(futures):
            ticker, data = future.result()
            result[ticker] = data

    # Cash positions (no market data needed)
    for sym, pos in positions.items():
        if not pos.get('cash'):
            continue
        val = pos.get('currentValue')
        if val:
            result[sym] = {
                'name':             pos.get('name') or sym,
                'type':             'Cash',
                'currency':         pos.get('currency'),
                'country':          pos.get('country') or 'Global',
                'sector':           'Liquidez',
                'shares':           None,
                'currentPrice':     None,
                'currentValue':     round(val, 2),
                'costBasis':        round(val, 2),
                'unrealizedPnl':    None,
                'unrealizedPnlPct': None,
                'recovered':        False,
                'changePct':        None,
                'change':           None,
            }

    return jsonify({
        'ok':        True,
        'data':      result,
        'metadata':  TICKER_META,
        'cryptoSymbols': sorted(set(COINGECKO_IDS) | set(CRYPTO_YF_SUFFIX)),
        'timestamp': datetime.now().strftime('%b %d %Y, %I:%M %p'),
    })


def _portfolio_prompt_lines(positions):
    """Líneas de texto para el prompt y total. Valor faltante = 'n/a' y no suma (nunca 0)."""
    lines, total = [], 0
    for ticker, pos in positions.items():
        meta = TICKER_META.get(ticker, {})
        val = pos.get('currentValue')
        if val is None:
            val = pos.get('costBasis')
        if val is not None:
            total += val
        lines.append(
            f"- {ticker} ({meta.get('name', ticker)}): shares {pos.get('shares', '?')}, "
            f"type: {meta.get('type','Stock')}, sector: {meta.get('sector','Other')}, "
            f"country: {meta.get('country','USA')}, value: {'n/a' if val is None else f'${val}'}"
        )
    return lines, total


@app.route('/api/portfolio/analysis', methods=['POST', 'GET'])
def portfolio_analysis():
    """
    Generate structured AI portfolio analysis using Gemma AI.
    Body: { lang: 'en' | 'es' }
    """
    try:
        data = request.get_json(force=True, silent=True) if request.data else {}
        lang = data.get('lang', 'es') if isinstance(data, dict) else 'es'

        positions = load_portfolio_from_csv()
        pf_lines, total_val = _portfolio_prompt_lines(positions)

        lang_instr = (
            "Write all text fields in Spanish (castellano)."
            if str(lang).lower().startswith('es')
            else "Write all text fields in English."
        )

        prompt = f"""You are a senior portfolio manager and risk analyst. Analyze the following investment portfolio snapshot and output ONLY a raw JSON object with NO markdown formatting, NO backticks, and NO extra text.

{lang_instr}

PORTFOLIO COMPOSITION:
{chr(10).join(pf_lines) if pf_lines else 'No positions found.'}
Total Portfolio Value (approx): ${total_val:,.2f}

Output JSON schema must match EXACTLY:
{{
  "risk_level": "MODERATE",
  "summary": "2-3 sentence executive summary of overall portfolio health and balance.",
  "diversification": "Analysis of asset allocation, sector concentration, and geographical coverage.",
  "strengths": [
    "Key strength point 1",
    "Key strength point 2"
  ],
  "concerns": [
    "Key risk/concern point 1",
    "Key risk/concern point 2"
  ],
  "recommendations": [
    "Actionable recommendation point 1",
    "Actionable recommendation point 2"
  ],
  "outlook": "Short forward-looking market outlook for this asset mix."
}}
"""

        raw_response = _call_gemma(prompt, timeout=60, enable_thinking=False)
        analysis_json = _extract_json(raw_response)

        if not analysis_json or not isinstance(analysis_json, dict):
            # No canned text: without a valid model answer the UI shows an error instead.
            return jsonify({'ok': False,
                            'error': 'AI analysis unavailable (NVIDIA_API_KEY missing or model error)'}), 503

        return jsonify({
            'ok': True,
            'analysis': analysis_json,
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        })

    except Exception as e:
        print(f"Error in portfolio_analysis: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/refresh')
def refresh():
    """Full market data refresh for all tickers (risk report + watching)."""
    results = {}
    errors  = []

    for ticker in ALL_TICKERS:
        try:
            yf_sym = f'{ticker}-USD' if ticker in CRYPTO_YF_SUFFIX else ticker
            t    = yf.Ticker(yf_sym)
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
    interval: '4h' (6mo) | '1d' (2y) | '1w' (10y)
    Returns list of {time, open, high, low, close, volume, ema20}
    Supports query param ?force=true to bypass cache.
    """
    ticker = ticker.upper().strip()
    if not TICKER_VALIDATION_REGEX.match(ticker):
        return jsonify({'error': 'Invalid ticker format'}), 400

    if interval not in OHLCV_INTERVAL_MAP:
        return jsonify({'error': 'interval must be 4h, 1d, or 1w'}), 400

    force = request.args.get('force', 'false').lower() in ('true', '1')
    if not force:
        cached = get_cached_ohlcv(ticker, interval)
        if cached:
            return jsonify(cached)

    period, yf_interval, _ttl = OHLCV_INTERVAL_MAP[interval]

    try:
        yf_ticker = f'{ticker}-USD' if ticker in CRYPTO_YF_SUFFIX else ticker
        t    = yf.Ticker(yf_ticker)
        hist = t.history(period=period, interval=yf_interval)

        if hist.empty:
            result = {'candles': [], 'ticker': ticker, 'interval': interval}
            put_cached_ohlcv(ticker, interval, result)
            return jsonify(result)

        hist = hist.dropna(subset=['Open', 'High', 'Low', 'Close'])

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
        response_data = {
            'ticker':   ticker,
            'name':     meta.get('name', ticker),
            'interval': interval,
            'period':   period,
            'candles':  candles,
        }
        put_cached_ohlcv(ticker, interval, response_data)
        return jsonify(response_data)

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


# ─── IA (Gemma): análisis del portfolio y narrativa ──────────────────────────

def _call_gemma(prompt, timeout=120, enable_thinking=True):
    """Call Nvidia Gemma API and return the text response."""
    if not NVIDIA_API_KEY:
        return None
    url = NVIDIA_API_URL
    stream = False
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "text/event-stream" if stream else "application/json",
    }
    payload = {
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "model": NVIDIA_MODEL,
        "chat_template_kwargs": {
            "enable_thinking": enable_thinking
        },
        "max_tokens": 16384 if enable_thinking else 2048,
        "stream": stream,
        "temperature": 1.0 if enable_thinking else 0.3,
        "top_p": 0.95
    }
    try:
        resp = http_requests.post(url, headers=headers, json=payload, stream=stream, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            return content
        else:
            print(f'Gemma API error {resp.status_code}: {resp.text[:300]}')
            return None
    except Exception as e:
        print(f'Gemma error: {e}')
        return None


def _extract_json(text):
    """Extract first JSON object from a text string, stripping thinking tags and codeblocks."""
    if not text:
        return None
    # 1. Remove thinking traces if any
    clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # 2. Extract content inside ```json ... ``` or ``` ... ``` if present
    codeblock_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', clean_text)
    if codeblock_match:
        try:
            return json.loads(codeblock_match.group(1))
        except Exception:
            pass
    # 3. Search for outer JSON object
    match = re.search(r'\{[\s\S]*\}', clean_text)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except Exception:
        try:
            match_ng = re.search(r'\{[\s\S]*?\}', clean_text)
            if match_ng:
                return json.loads(match_ng.group())
        except Exception:
            pass
        return None


def _lang_instruction(lang):
    """Return a language instruction string to append to AI prompts."""
    if lang and lang.lower().startswith('es'):
        return '\n\nIMPORTANT: Write ALL text values in Spanish (castellano). Do not translate ticker symbols, financial metrics labels, or numeric values.'
    return '\n\nIMPORTANT: Write ALL text values in English.'


FIN_CRYPTO = CRYPTO_YF_SUFFIX | set(COINGECKO_IDS)


@app.route('/api/financials/status')
def financials_status():
    """Frescura de varios tickers a la vez. Solo lee la caché: no consulta la red."""
    param = request.args.get('tickers', '')
    tickers = [t.strip().upper() for t in param.split(',') if t.strip()] or list(ACTIVE_TICKERS)
    tickers = [t for t in tickers if TICKER_VALIDATION_REGEX.match(t)]
    out = {}
    for t in tickers:
        out[t] = {'state': 'unsupported'} if t in FIN_CRYPTO else financials.get_freshness(t)
    return jsonify(out)


@app.route('/api/financials/<ticker>')
def get_financials(ticker):
    """Datos de la tab Análisis Financiero (ver financials.py).

    Siempre responde desde la caché en disco; solo descarga la primera vez.
    ?force=true pasa por la misma validación que el refresh: sin datos nuevos, no descarga.
    """
    ticker = ticker.upper().strip()
    if not TICKER_VALIDATION_REGEX.match(ticker):
        return jsonify({'error': 'Invalid ticker format'}), 400
    try:
        if request.args.get('force', 'false').lower() in ('true', '1'):
            updated, body = financials.refresh_ticker(ticker, crypto_symbols=FIN_CRYPTO)
            if updated:
                return jsonify(body['data'])
        return jsonify(financials.get_financials(ticker, crypto_symbols=FIN_CRYPTO))
    except Exception as e:
        print(f'[financials] {ticker}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/financials/<ticker>/refresh', methods=['POST'])
def refresh_financials_ticker(ticker):
    """Actualiza un ticker solo si hay datos nuevos. Sin datos nuevos responde 409."""
    ticker = ticker.upper().strip()
    if not TICKER_VALIDATION_REGEX.match(ticker):
        return jsonify({'error': 'Invalid ticker format'}), 400
    try:
        updated, body = financials.refresh_ticker(ticker, crypto_symbols=FIN_CRYPTO)
    except Exception as e:
        return jsonify({'updated': False, 'error': str(e)}), 500
    return jsonify(body), (200 if updated else 409)


@app.route('/api/refresh/financials', methods=['POST'])
def refresh_financials_all():
    """Actualiza solo los tickers con datos nuevos disponibles."""
    candidates = [t for t in ACTIVE_TICKERS if t not in FIN_CRYPTO
                  and financials.get_freshness(t)['state'] == 'available']
    updated, failed = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for t, res in zip(candidates, pool.map(
                lambda s: financials.refresh_ticker(s, crypto_symbols=FIN_CRYPTO), candidates)):
            (updated if res[0] and res[1].get('updated') else failed).append(t)
    return jsonify({'ok': True, 'updated': updated, 'failed': failed, 'checked': len(candidates)})


@app.route('/api/validate/<ticker>')
def validate_ticker_route(ticker):
    """Valida que el símbolo exista (sin generar reportes ni llamar a la IA)."""
    ok, err = _validate_ticker(ticker)
    return jsonify({'ok': ok, 'error': err}), (200 if ok else 400)


def _requested_tickers(param=None, body_list=None):
    raw = body_list if body_list is not None else [t for t in (param or '').split(',')]
    tickers = [str(t).strip().upper() for t in raw if str(t).strip()] or list(ACTIVE_TICKERS)
    return [t for t in dict.fromkeys(tickers) if TICKER_VALIDATION_REGEX.match(t)]


@app.route('/api/valuations')
def get_valuations():
    """Registro de valuaciones: Barata / Justa / Cara / Info no disponible, con la misma etiqueta
    que el Reporte de Riesgo. Solo lee la caché de riesgo: no consulta la red."""
    tickers = _requested_tickers(request.args.get('tickers'))
    return jsonify(valuations.table(tickers, TICKER_META))


@app.route('/api/valuations/refresh', methods=['POST'])
def refresh_valuations():
    """Actualiza las valuaciones en segundo plano (una descarga de precios para todos).
    202 si arrancó; 409 si ya hay una en curso. Progreso: GET /api/valuations/refresh/status."""
    body = request.get_json(silent=True) or {}
    tickers = _requested_tickers(body_list=body.get('tickers'))
    if not valuations.start_refresh(tickers, crypto_symbols=FIN_CRYPTO):
        return jsonify({'started': False, 'status': valuations.status()}), 409
    return jsonify({'started': True, 'status': valuations.status()}), 202


@app.route('/api/valuations/refresh/status')
def refresh_valuations_status():
    return jsonify(valuations.status())


@app.route('/api/risk/summary')
def risk_summary():
    """Puntaje de riesgo de varios tickers desde la caché (sin red). Más alto = más riesgo."""
    param = request.args.get('tickers', '')
    tickers = [t.strip().upper() for t in param.split(',') if t.strip()] or list(ACTIVE_TICKERS)
    return jsonify(risk.summary([t for t in tickers if TICKER_VALIDATION_REGEX.match(t)]))


@app.route('/api/risk/<ticker>/narrative')
def get_risk_narrative(ticker):
    """Narrativa IA (Gemma) validada contra los datos calculados. status: 'ready' | 'generating'.
    Campos que el modelo no pudo dar de forma válida vienen en None con su motivo en 'unavailable'."""
    ticker = ticker.upper().strip()
    if not TICKER_VALIDATION_REGEX.match(ticker):
        return jsonify({'error': 'Invalid ticker format'}), 400
    lang = request.args.get('lang', 'es')
    call = (lambda prompt: _call_gemma(prompt, timeout=narrative.MODEL_TIMEOUT_S, enable_thinking=False)) \
        if NVIDIA_API_KEY else None
    try:
        return jsonify(narrative.get_narrative(
            ticker, lang,
            report_fn=lambda t: risk.get_risk(t, crypto_symbols=FIN_CRYPTO),
            fin_fn=lambda t: financials.get_financials(t, crypto_symbols=FIN_CRYPTO),
            call_model=call, model_name=NVIDIA_MODEL))
    except Exception as e:
        print(f'[narrative] {ticker}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/risk/<ticker>')
def get_risk(ticker):
    """Reporte de riesgo determinista (risk.py): 0-100, más alto = más riesgo. Sin IA."""
    ticker = ticker.upper().strip()
    if not TICKER_VALIDATION_REGEX.match(ticker):
        return jsonify({'error': 'Invalid ticker format'}), 400
    force = request.args.get('force', 'false').lower() in ('true', '1')
    try:
        return jsonify(risk.get_risk(ticker, crypto_symbols=FIN_CRYPTO, force=force))
    except Exception as e:
        print(f'[risk] {ticker}: {e}')
        return jsonify({'error': str(e)}), 500


CHAT_LAST_ERROR = {'detail': None}
CHAT_CONNECT_TIMEOUT = 10     # s para conectar con NVIDIA
CHAT_IDLE_TIMEOUT = 150       # s máximos sin recibir nada (incluye la espera en la cola de NVIDIA)
CHAT_TOTAL_TIMEOUT = 300      # s máximos para la respuesta completa
CHAT_ATTEMPTS = 2             # un reintento si NVIDIA no mandó nada (cola saturada, 429/5xx)
CHAT_RETRY_WAIT = 3
CHAT_MAX_TOKENS = 1200
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _chat_attempt(payload, headers):
    """Un intento en streaming. Devuelve (texto, error, reintentable)."""
    started = time.time()
    first = None
    parts = []
    try:
        resp = http_requests.post(NVIDIA_API_URL, headers=headers, json=payload, stream=True,
                                  timeout=(CHAT_CONNECT_TIMEOUT, CHAT_IDLE_TIMEOUT))
        if resp.status_code != 200:
            body = resp.text[:300]
            print(f'[chat] Gemma API error {resp.status_code}: {body}')
            return None, f'NVIDIA {resp.status_code}: {body[:200]}', resp.status_code in _RETRYABLE_STATUS
        # NVIDIA manda text/event-stream sin charset: requests asume ISO-8859-1 y los acentos salen
        # como "Ã³". Se leen bytes y se decodifican como UTF-8 (lo que exige el estándar SSE).
        for raw in resp.iter_lines():
            line = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
            if time.time() - started > CHAT_TOTAL_TIMEOUT:
                resp.close()
                raise TimeoutError(f'la respuesta superó {CHAT_TOTAL_TIMEOUT} s')
            if not line or not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            for ch in chunk.get('choices') or []:
                piece = (ch.get('delta') or {}).get('content') or (ch.get('message') or {}).get('content')
                if piece:
                    if first is None:
                        first = time.time() - started
                    parts.append(piece)
    except Exception as e:
        print(f'[chat] Gemma error: {e}')
        # Si ya llegó texto no se reintenta (se repetiría la generación); si no llegó nada, sí.
        return None, f'{type(e).__name__}: {str(e)[:200]}', not parts
    content = ''.join(parts).strip()
    print(f'[chat] Gemma respondió en {time.time() - started:.1f} s (primer texto a los '
          f'{first if first is None else round(first, 1)} s, {len(content)} caracteres)')
    if not content:
        return None, 'NVIDIA 200 sin texto', True
    return content, None, False


def _chat_call(messages):
    """Llama a Gemma en streaming y junta el texto. Devuelve el texto o None si falla
    (el motivo queda en CHAT_LAST_ERROR para mostrarlo en el chat).

    El plan gratuito de NVIDIA a veces encola el pedido y tarda en mandar el primer texto:
    el límite de espera es amplio y, si no llegó nada, se reintenta una vez."""
    CHAT_LAST_ERROR['detail'] = None
    payload = {
        "messages": messages, "model": NVIDIA_MODEL,
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": CHAT_MAX_TOKENS, "stream": True,
        "temperature": 0.3,          # baja: el agente cita datos, no improvisa
        "top_p": 0.95,
    }
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Accept": "text/event-stream"}
    err = None
    for attempt in range(CHAT_ATTEMPTS):
        content, err, retry = _chat_attempt(payload, headers)
        if content:
            return content
        if not retry or attempt == CHAT_ATTEMPTS - 1:
            break
        print(f'[chat] reintentando ({err})')
        time.sleep(CHAT_RETRY_WAIT)
    if err and CHAT_ATTEMPTS > 1 and retry:
        err = f'{err} · {CHAT_ATTEMPTS} intentos'
    CHAT_LAST_ERROR['detail'] = err
    return None


def _quote_snapshot():
    """Cotizaciones ya en memoria (sin red), con su hora."""
    return {t: {**(e.get('data') or {}), 'ts': e.get('timestamp')} for t, e in QUOTE_CACHE.items()}


def _run_explore(spec, prof, prefs, owned):
    """Ejecuta un pedido de exploración validado. owned: tickers que ya están en el dashboard."""
    params, rejected = explore.parse_spec(spec, prefs)
    exclude = {str(t).upper() for t in owned} | set(TICKER_META) | set(PORTFOLIO_TICKERS)
    res = explore.run(params, prof, prefs, exclude, crypto_symbols=FIN_CRYPTO)
    res['rejected'] = rejected
    return res


@app.route('/api/explore/preferences', methods=['GET', 'PUT'])
def explore_preferences():
    """Preferencias para el modo Explorar (data/explore_profile.json). PUT valida y normaliza."""
    if request.method == 'PUT':
        return jsonify(explore.save_prefs(request.get_json(silent=True) or {}))
    return jsonify({**explore.load_prefs(), 'sectors': list(explore.SECTORS), 'levels': list(explore.LEVELS),
                    'priorities': [p for p in explore.PRIORITIES if p]})


@app.route('/api/explore', methods=['POST'])
def explore_direct():
    """Exploración sin pasar por Gemma. Body: {intent, sectors: [...], max_beta, min_div, pe_max, min_mcap_b, owned}."""
    body = request.get_json(silent=True) or {}
    spec = ' '.join(f'{k}={",".join(v) if isinstance(v, list) else v}' for k, v in body.items()
                    if k in ('intent', 'sectors', *explore.BOUNDS) and v not in (None, '', []))
    positions = load_portfolio_from_csv()
    owned = [str(t).upper() for t in body.get('owned') or []] or list(ACTIVE_TICKERS)
    tickers = list(dict.fromkeys(owned + [t for t in PORTFOLIO_TICKERS if t in positions]))
    rows = agent.build_rows(tickers, TICKER_META, positions, _quote_snapshot())
    prefs = explore.load_prefs()
    return jsonify(_run_explore(spec, explore.profile(rows), prefs, tickers))


@app.route('/api/chat', methods=['POST'])
def chat():
    """Portfolio Agent. Body: {message, history: [{role, content}], lang, tickers?}.

    Contexto completo (posiciones, técnico, finanzas, riesgo y valuación) desde las cachés, sin red.
    Respuesta: {ok, reply, tables: [{title, columns: [{key, label, unit}], rows}], omitted, focus, as_of}.
    Las tablas las arma el servidor con datos reales; las cifras del texto se verifican (agent.py)."""
    try:
        body = request.get_json(force=True) or {}
        message = (body.get('message') or '').strip()[:4000]
        history = body.get('history') or []
        lang = body.get('lang', 'es')
        if not message:
            return jsonify({'ok': False, 'error': 'Empty message'}), 400
        if not NVIDIA_API_KEY:
            return jsonify({'ok': False, 'error': 'NVIDIA_API_KEY not configured'}), 503
        positions = load_portfolio_from_csv()
        tickers = _requested_tickers(body_list=body.get('tickers'))
        tickers = list(dict.fromkeys(tickers + [t for t in PORTFOLIO_TICKERS if t in positions]))
        rows = agent.build_rows(tickers, TICKER_META, positions, _quote_snapshot())
        prefs = explore.load_prefs()
        out = agent.answer(message, history, lang, rows, agent.totals(rows, positions), _chat_call, prefs=prefs)
        if out is None:
            return jsonify({'ok': False, 'error': 'model_error', 'detail': CHAT_LAST_ERROR.get('detail')}), 502
        spec = out.pop('explore_spec', None)
        if spec is not None:
            out['explore'] = _run_explore(spec, out['profile'], prefs, body.get('owned') or tickers)
        return jsonify(out)
    except Exception as e:
        print(f'[chat] {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


# ─── BATCH REFRESH ENDPOINTS ──────────────────────────────────────────────────

@app.route('/api/refresh/market')
def refresh_market():
    """Batch refresh live market quotes for all active non-archived tickers."""
    def _fetch(ticker):
        try:
            if ticker in COINGECKO_IDS:
                cg = fetch_coingecko_prices().get(ticker, {})
                p = cg.get('price')
                cp = cg.get('change_pct')
                c = cg.get('change')
                if p is None and ticker in CRYPTO_YF_SUFFIX:
                    info = yf.Ticker(f"{ticker}-USD").info
                    p = _num_or_none(info.get('currentPrice') or info.get('regularMarketPrice'))
                    cp = _num_or_none(info.get('regularMarketChangePercent'))
                    c = _num_or_none(info.get('regularMarketChange'))
                q = {'price': p, 'chg': c, 'chg_pct': cp}
            else:
                info = yf.Ticker(ticker).info
                p = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('navPrice')
                cp = info.get('regularMarketChangePercent')
                c = info.get('regularMarketChange')
                q = {
                    'price': _num_or_none(p, 2),
                    'chg': _num_or_none(c, 2),            # un 0 real se conserva; un faltante es null
                    'chg_pct': _num_or_none(cp, 2)
                }
            if q.get('price') is not None:
                put_cached_quote(ticker, q)
            return ticker, q
        except Exception as e:
            return ticker, {'error': str(e)}

    quotes = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch, t): t for t in ACTIVE_TICKERS}
        for future in as_completed(futures):
            ticker, q = future.result()
            quotes[ticker] = q

    return jsonify({'ok': True, 'updated': len(quotes), 'quotes': quotes})


@app.route('/api/refresh/technical')
def refresh_technical():
    """Batch download 2y daily OHLCV historical data for active tickers and reset cache."""
    global OHLCV_CACHE
    OHLCV_CACHE.clear()
    stock_tickers = [t for t in ACTIVE_TICKERS if t not in COINGECKO_IDS]
    crypto_tickers = [f"{t}-USD" for t in ACTIVE_TICKERS if t in CRYPTO_YF_SUFFIX]
    all_download_tickers = stock_tickers + crypto_tickers
    
    updated_count = 0
    try:
        if all_download_tickers:
            df = yf.download(all_download_tickers, period='2y', interval='1d', group_by='ticker', progress=False)
            updated_count = len(all_download_tickers)
        return jsonify({'ok': True, 'updated': len(ACTIVE_TICKERS), 'downloaded': updated_count})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})



# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5050)
    args = parser.parse_args()

    financials.start_background_checker(lambda: [t for t in ALL_TICKERS if t not in FIN_CRYPTO])

    print()
    print('  ┌─────────────────────────────────────────────┐')
    print('  │  Financial Dashboard · Local Server         │')
    print(f'  │  http://localhost:{args.port}                      │')
    print('  └─────────────────────────────────────────────┘')
    print()
    app.run(debug=False, port=args.port)
