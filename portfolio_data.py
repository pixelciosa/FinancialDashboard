"""
Portfolio input files for the dashboard (no network, no AI).

The dashboard reads three small CSV files. Their paths come from environment variables and
default to the fictional demo files in `sample_data/`:

    PORTFOLIO_CSV  (default sample_data/portfolio.csv)
        ticker,name,type,quantity,cost_basis_usd[,sector,country,coingecko_id]
        - type is one of Stock, ETF, Crypto.
        - cost_basis_usd is the TOTAL cost of the position in USD (not the per-unit price).
        - sector, country and coingecko_id are optional; known tickers get them from PRESETS.

    CASH_CSV       (default sample_data/cash.csv)
        currency,amount,usd_rate
        - usd_rate = how many USD one unit of the currency is worth (USD itself is 1).

    WATCHLIST_CSV  (default sample_data/watchlist.csv, optional)
        ticker,name,type,category[,sector,country,coingecko_id]
        - category is 'watching' or 'archived'.

Rules (same as the rest of the app): a missing value is None, never 0; a malformed row is
skipped and reported in `errors`, it never stops the dashboard.
"""
import csv
import math
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, 'sample_data')

TYPES = ('Stock', 'ETF', 'Crypto')
CATEGORIES = ('portfolio', 'watching', 'archived')
TICKER_RE = re.compile(r'^[A-Z0-9.\-^]{1,15}$')

# Metadata presets for well-known tickers. A ticker that is not here still works: it takes the
# name/type from the CSV and 'Other' / 'USA' as sector and country.
PRESETS = {
    # US large caps
    'JPM':  {'name': 'JPMorgan Chase & Co.',        'type': 'Stock', 'country': 'USA',    'sector': 'Financials'},
    'COST': {'name': 'Costco Wholesale',            'type': 'Stock', 'country': 'USA',    'sector': 'Consumer Staples'},
    'JNJ':  {'name': 'Johnson & Johnson',           'type': 'Stock', 'country': 'USA',    'sector': 'Healthcare'},
    'HD':   {'name': 'The Home Depot',              'type': 'Stock', 'country': 'USA',    'sector': 'Consumer Discretionary'},
    'NFLX': {'name': 'Netflix Inc.',                'type': 'Stock', 'country': 'USA',    'sector': 'Communication Services'},
    'ORCL': {'name': 'Oracle Corporation',          'type': 'Stock', 'country': 'USA',    'sector': 'Technology'},
    'CRM':  {'name': 'Salesforce Inc.',             'type': 'Stock', 'country': 'USA',    'sector': 'Technology'},
    'PG':   {'name': 'Procter & Gamble',            'type': 'Stock', 'country': 'USA',    'sector': 'Consumer Staples'},
    'DIS':  {'name': 'The Walt Disney Company',     'type': 'Stock', 'country': 'USA',    'sector': 'Communication Services'},
    'SBUX': {'name': 'Starbucks Corporation',       'type': 'Stock', 'country': 'USA',    'sector': 'Consumer Discretionary'},
    'INTC': {'name': 'Intel Corporation',           'type': 'Stock', 'country': 'USA',    'sector': 'Semiconductors'},
    # Latin America ADRs
    'NU':   {'name': 'Nu Holdings Ltd.',            'type': 'Stock', 'country': 'Brazil', 'sector': 'Financials'},
    # ETFs
    'VTI':  {'name': 'Vanguard Total Stock Market ETF', 'type': 'ETF', 'country': 'USA',  'sector': 'Broad Market'},
    'BND':  {'name': 'Vanguard Total Bond Market ETF',  'type': 'ETF', 'country': 'USA',  'sector': 'Bonds'},
    'VNQ':  {'name': 'Vanguard Real Estate ETF',        'type': 'ETF', 'country': 'USA',  'sector': 'Real Estate'},
    # Crypto
    'SOL':  {'name': 'Solana',                      'type': 'Crypto', 'country': 'Global', 'sector': 'Crypto'},
    'ADA':  {'name': 'Cardano',                     'type': 'Crypto', 'country': 'Global', 'sector': 'Crypto'},
}

# CoinGecko ids for common cryptocurrencies (one batched request, no API key). A crypto row in the
# CSV can also bring its own id in the optional `coingecko_id` column.
COINGECKO_PRESETS = {
    'SOL': 'solana', 'ADA': 'cardano', 'DOGE': 'dogecoin', 'DOT': 'polkadot', 'LINK': 'chainlink',
    'AVAX': 'avalanche-2', 'LTC': 'litecoin',
}

# Cash keys: USD is USD_CASH; any other currency uses its ISO code (ARS, EUR, BRL...).
CASH_NAMES = {
    'USD': ('US dollars — Cash', 'Global'),
    'ARS': ('Argentine pesos — Cash', 'Argentina'),
    'EUR': ('Euros — Cash', 'Global'),
    'BRL': ('Brazilian reais — Cash', 'Brazil'),
}


def portfolio_csv_path():
    return os.environ.get('PORTFOLIO_CSV') or os.path.join(SAMPLE_DIR, 'portfolio.csv')


def cash_csv_path():
    return os.environ.get('CASH_CSV') or os.path.join(SAMPLE_DIR, 'cash.csv')


def watchlist_csv_path():
    return os.environ.get('WATCHLIST_CSV') or os.path.join(SAMPLE_DIR, 'watchlist.csv')


def cash_key(currency):
    c = str(currency or '').strip().upper()
    return 'USD_CASH' if c == 'USD' else c


def _num(v):
    """Finite float or None. Accepts '1234.5' and '1,234.5' (thousands comma)."""
    if v is None:
        return None
    s = str(v).strip().replace(',', '')
    if s == '':
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _rows(path):
    """DictReader rows with lower-case, stripped keys. Missing file -> ([], [error])."""
    if not path or not os.path.exists(path):
        return [], [{'file': os.path.basename(path or ''), 'row': None, 'error': 'file not found'}]
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        out = []
        for r in reader:
            out.append((reader.line_num, {str(k or '').strip().lower(): (v or '').strip() for k, v in r.items()}))
    return out, []


def _meta_row(r, category):
    ticker = r.get('ticker', '').upper()
    preset = PRESETS.get(ticker, {})
    typ = r.get('type') or preset.get('type') or 'Stock'
    typ = {'stock': 'Stock', 'etf': 'ETF', 'crypto': 'Crypto'}.get(typ.lower(), typ)
    meta = {
        'name': r.get('name') or preset.get('name') or ticker,
        'type': typ,
        'country': r.get('country') or preset.get('country') or ('Global' if typ == 'Crypto' else 'USA'),
        'sector': r.get('sector') or preset.get('sector') or ('Crypto' if typ == 'Crypto' else 'Other'),
        'category': category,
    }
    cg = r.get('coingecko_id') or (COINGECKO_PRESETS.get(ticker) if typ == 'Crypto' else None)
    return ticker, meta, cg


def load_portfolio(path=None):
    """Positions from PORTFOLIO_CSV.

    Returns {'rows': [{ticker, name, type, quantity, cost_basis_usd, meta, coingecko_id}], 'errors': [...]}"""
    path = path or portfolio_csv_path()
    raw, errors = _rows(path)
    rows, seen = [], set()
    for line, r in raw:
        ticker = r.get('ticker', '').upper()
        if not ticker and not any(r.values()):
            continue
        if not TICKER_RE.match(ticker):
            errors.append({'file': os.path.basename(path), 'row': line, 'error': f'invalid ticker {ticker!r}'})
            continue
        if ticker in seen:
            errors.append({'file': os.path.basename(path), 'row': line, 'error': f'duplicate ticker {ticker}'})
            continue
        qty = _num(r.get('quantity'))
        if qty is None:
            errors.append({'file': os.path.basename(path), 'row': line, 'error': 'quantity missing'})
            continue
        t, meta, cg = _meta_row(r, 'portfolio')
        if meta['type'] not in TYPES:
            errors.append({'file': os.path.basename(path), 'row': line, 'error': f"unknown type {meta['type']!r}"})
            continue
        seen.add(ticker)
        rows.append({'ticker': t, 'quantity': qty, 'cost_basis_usd': _num(r.get('cost_basis_usd')),
                     'meta': meta, 'coingecko_id': cg})
    return {'rows': rows, 'errors': errors}


def load_watchlist(path=None):
    """Watchlist rows (category watching/archived). The file is optional."""
    path = path or watchlist_csv_path()
    if not os.path.exists(path):
        return {'rows': [], 'errors': []}
    raw, errors = _rows(path)
    rows = []
    for line, r in raw:
        ticker = r.get('ticker', '').upper()
        if not TICKER_RE.match(ticker):
            if any(r.values()):
                errors.append({'file': os.path.basename(path), 'row': line, 'error': f'invalid ticker {ticker!r}'})
            continue
        category = (r.get('category') or 'watching').lower()
        if category not in ('watching', 'archived'):
            errors.append({'file': os.path.basename(path), 'row': line, 'error': f'invalid category {category!r}'})
            continue
        t, meta, cg = _meta_row(r, category)
        rows.append({'ticker': t, 'meta': meta, 'coingecko_id': cg})
    return {'rows': rows, 'errors': errors}


def load_cash(path=None):
    """Cash balances from CASH_CSV, converted to USD with the row's usd_rate.

    Returns {key: {'currentValue', 'currency', 'amount', 'usdRate', 'name', 'country', 'cash': True}}."""
    path = path or cash_csv_path()
    if not os.path.exists(path):
        return {}
    raw, _ = _rows(path)
    out = {}
    for _line, r in raw:
        cur = (r.get('currency') or '').upper()
        amount = _num(r.get('amount'))
        rate = 1.0 if cur == 'USD' else _num(r.get('usd_rate'))
        if not cur or amount is None or rate is None:
            continue
        name, country = CASH_NAMES.get(cur, (f'{cur} — Cash', 'Global'))
        key = cash_key(cur)
        prev = out.get(key, {}).get('currentValue') or 0.0
        out[key] = {'currentValue': round(prev + amount * rate, 2), 'currency': cur,
                    'amount': amount, 'usdRate': rate, 'name': name, 'country': country, 'cash': True}
    return out


def build_ticker_meta(portfolio=None, watchlist=None):
    """TICKER_META = portfolio rows (category 'portfolio') + watchlist rows. Portfolio wins on duplicates."""
    portfolio = portfolio if portfolio is not None else load_portfolio()
    watchlist = watchlist if watchlist is not None else load_watchlist()
    meta = {}
    for r in watchlist['rows']:
        meta[r['ticker']] = dict(r['meta'])
    for r in portfolio['rows']:
        meta[r['ticker']] = dict(r['meta'])
    return meta


def coingecko_ids(portfolio=None, watchlist=None):
    portfolio = portfolio if portfolio is not None else load_portfolio()
    watchlist = watchlist if watchlist is not None else load_watchlist()
    out = {}
    for r in watchlist['rows'] + portfolio['rows']:
        if r['meta']['type'] == 'Crypto' and r.get('coingecko_id'):
            out[r['ticker']] = r['coingecko_id']
    return out


def crypto_tickers(portfolio=None, watchlist=None):
    """Crypto symbols that need the -USD suffix in yfinance: the CSV rows typed Crypto plus the
    common cryptos in COINGECKO_PRESETS."""
    portfolio = portfolio if portfolio is not None else load_portfolio()
    watchlist = watchlist if watchlist is not None else load_watchlist()
    rows = {r['ticker'] for r in watchlist['rows'] + portfolio['rows'] if r['meta']['type'] == 'Crypto'}
    return rows | set(COINGECKO_PRESETS)


def positions(portfolio=None, cash=None):
    """{ticker: {'shares', 'costBasis', 'recovered'}} plus cash entries keyed by USD_CASH / ISO code."""
    portfolio = portfolio if portfolio is not None else load_portfolio()
    out = {}
    for r in portfolio['rows']:
        qty, cost = r['quantity'], r['cost_basis_usd']
        out[r['ticker']] = {
            'shares': qty,
            'costBasis': None if cost is None else round(cost, 2),
            # A position whose remaining cost is (almost) zero already paid back its investment.
            'recovered': bool(qty and qty > 0.001 and cost is not None and cost < 4),
        }
    out.update(cash if cash is not None else load_cash())
    return out
