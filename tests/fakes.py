"""
Dobles de prueba offline para yfinance.

FakeTicker imita la superficie de yf.Ticker que usa server.py: info, income_stmt,
cashflow, balance_sheet, quarterly_* y history(). Los DataFrames siguen el formato
real de yfinance 1.3: filas = conceptos contables, columnas = fechas de cierre,
de la más nueva a la más vieja.
"""
import math
from datetime import date, timedelta

import pandas as pd


def statement(rows, dates):
    """rows: {'Total Revenue': [newest, ..., oldest]}, dates: ['2025-09-30', ...] newest first."""
    cols = [pd.Timestamp(d) for d in dates]
    return pd.DataFrame({c: {k: v[i] for k, v in rows.items()} for i, c in enumerate(cols)})


# Empresa ficticia con números redondos para que las cuentas se verifiquen a mano.
ANNUAL_DATES = ['2025-12-31', '2024-12-31', '2023-12-31', '2022-12-31']
ANNUAL_INCOME = {
    'Total Revenue':    [400e9, 360e9, 320e9, 300e9],
    'Gross Profit':     [181.3e9, 158.7e9, 134.9e9, 120.4e9],
    'Operating Income': [120e9, 100.8e9, 86.4e9, 75e9],
    'Net Income':       [100e9, 86.4e9, 70.4e9, 60e9],
}
ANNUAL_CASHFLOW = {
    'Operating Cash Flow': [130e9, 110e9, 100e9, 90e9],
    'Capital Expenditure': [-30e9, -25e9, -20e9, -18e9],
}
QUARTER_DATES = ['2026-09-30', '2026-06-30', '2026-03-31', '2025-12-31',
                 '2025-09-30', '2025-06-30', '2025-03-31', '2024-12-31']
QUARTER_INCOME = {
    'Total Revenue':    [110e9, 105e9, 100e9, 104e9, 102e9, 98e9, 96e9, 95e9],
    'Gross Profit':     [50e9, 47e9, 45e9, 47e9, 46e9, 44e9, 43e9, 42e9],
    'Operating Income': [33e9, 31e9, 30e9, 31e9, 30e9, 29e9, 28e9, 27e9],
    'Net Income':       [28e9, 26e9, 25e9, 26e9, 25e9, 24e9, 23e9, 22e9],
    'Diluted EPS':      [1.9, 1.75, 1.7, 1.76, 1.7, 1.62, 1.55, 1.5],
}
QUARTER_CASHFLOW = {
    'Operating Cash Flow': [36e9, 34e9, 32e9, 33e9, 31e9, 30e9, 29e9, 28e9],
    'Capital Expenditure': [-8e9, -8.5e9, -7e9, -7.5e9, -7e9, -6.5e9, -6e9, -6e9],
}
# EPS ajustado vs estimado (Yahoo usa el último día del trimestre calendario como índice)
EARNINGS_HISTORY = pd.DataFrame(
    {'epsEstimate': [1.70, 1.72, 1.80, 1.85], 'epsActual': [1.80, 1.70, 1.80, 1.95]},
    index=pd.to_datetime(['2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30']))
# Relativa a hoy para que los tests no venzan solos cuando pase la fecha.
NEXT_EARNINGS = date.today() + timedelta(days=80)

STOCK_INFO = {
    'quoteType': 'EQUITY', 'longName': 'Acme Corp', 'sector': 'Technology',
    'industry': 'Software', 'currentPrice': 200.0, 'regularMarketChange': 2.0,
    'regularMarketChangePercent': 1.0, 'trailingPE': 30.0, 'forwardPE': 25.0,
    'marketCap': 3.0e12, 'dividendYield': 0.5, 'dividendRate': 1.0, 'trailingEps': 6.5, 'beta': 1.1,
    'fiftyTwoWeekHigh': 220.0, 'fiftyTwoWeekLow': 150.0,
    'priceToSalesTrailing12Months': 7.2, 'debtToEquity': 150.0, 'currentRatio': 1.4,
    'returnOnEquity': 0.21, 'currency': 'USD', 'financialCurrency': 'USD',
}

ETF_INFO = {
    'quoteType': 'ETF', 'longName': 'Fake Index ETF', 'category': 'Large Blend',
    'navPrice': 500.0, 'totalAssets': 5.0e11, 'yield': 0.013, 'beta': 1.0,
}


def monthly_history(start='2021-01-01', periods=60, start_price=100.0, monthly_growth=0.01):
    idx = pd.date_range(start, periods=periods, freq='MS', tz='America/New_York')
    closes = [round(start_price * (1 + monthly_growth) ** i, 4) for i in range(periods)]
    return pd.DataFrame({'Open': closes, 'High': closes, 'Low': closes,
                         'Close': closes, 'Volume': [1000] * periods}, index=idx)


def daily_history(days=260, start_price=100.0, drift=0.0008, wiggle=0.01, dividends=(), end=None):
    """Cierres diarios hábiles hasta hoy: tendencia suave con oscilación (determinista).
    dividends: índices de días con dividendo de 0.25."""
    end = end or pd.Timestamp(date.today())
    idx = pd.bdate_range(end=end, periods=days, tz='America/New_York')
    closes = [round(start_price * (1 + drift) ** i * (1 + wiggle * math.sin(i)), 4) for i in range(days)]
    divs = [0.25 if i in dividends else 0.0 for i in range(days)]
    return pd.DataFrame({'Open': closes, 'High': closes, 'Low': closes, 'Close': closes,
                         'Volume': [1000] * days, 'Dividends': divs, 'Stock Splits': [0.0] * days}, index=idx)


def earnings_dates_df(offsets_days=(-20, -110, -200, 60)):
    idx = pd.DatetimeIndex([pd.Timestamp(date.today() + timedelta(days=o)) for o in offsets_days]).tz_localize('America/New_York')
    return pd.DataFrame({'EPS Estimate': [1.8] * len(idx), 'Reported EPS': [1.9] * len(idx),
                         'Surprise(%)': [5.5, -1.2, 3.0, float('nan')][:len(idx)]}, index=idx)


def hourly_history(hours=48, start_price=100.0):
    idx = pd.date_range('2026-11-02 08:00', periods=hours, freq='h', tz='UTC')
    rows = []
    for i in range(hours):
        p = start_price + i
        rows.append({'Open': p, 'High': p + 0.5, 'Low': p - 0.5, 'Close': p + 0.25, 'Volume': 10})
    return pd.DataFrame(rows, index=idx)


class FakeFundsData:
    def __init__(self, holdings=None, sectors=None, expense_ratio=0.0009):
        rows = holdings if holdings is not None else [
            ('AAA', 'Alpha Inc', 0.071), ('BBB', 'Beta Corp', 0.065), ('CCC', 'Gamma Ltd', 0.052)]
        self.top_holdings = pd.DataFrame(
            {'Symbol': [r[0] for r in rows], 'Name': [r[1] for r in rows],
             'Holding Percent': [r[2] for r in rows]}).set_index('Symbol')
        self.sector_weightings = sectors if sectors is not None else {
            'technology': 0.315, 'healthcare': 0.12, 'financial_services': 0.13}
        self.fund_operations = pd.DataFrame(
            {'Attributes': ['Annual Report Expense Ratio', 'Annual Holdings Turnover', 'Total Net Assets'],
             'FETF': [expense_ratio, 0.02, 5e11], 'Category Average': [0.005, 0.3, None]}).set_index('Attributes')


class FakeTicker:
    def __init__(self, symbol, info=None, income=None, cashflow=None, balance=None,
                 q_income=None, history=None, q_cashflow=None, earnings_history=None,
                 calendar=None, funds_data=None, daily=None, earnings_dates=None):
        self.ticker = symbol
        self.info = info if info is not None else dict(STOCK_INFO)
        self.income_stmt = income if income is not None else statement(ANNUAL_INCOME, ANNUAL_DATES)
        self.financials = self.income_stmt
        self.cashflow = cashflow if cashflow is not None else statement(ANNUAL_CASHFLOW, ANNUAL_DATES)
        self.balance_sheet = balance if balance is not None else statement(
            {'Stockholders Equity': [500e9, 480e9, 460e9, 440e9]}, ANNUAL_DATES)
        self.quarterly_income_stmt = q_income if q_income is not None else statement(QUARTER_INCOME, QUARTER_DATES)
        self.quarterly_cashflow = q_cashflow if q_cashflow is not None else statement(QUARTER_CASHFLOW, QUARTER_DATES)
        self.earnings_history = earnings_history if earnings_history is not None else EARNINGS_HISTORY.copy()
        self.calendar = calendar if calendar is not None else {'Earnings Date': [NEXT_EARNINGS]}
        self.funds_data = funds_data if funds_data is not None else FakeFundsData()
        self._history = history
        self._daily = daily
        self._earnings_dates = earnings_dates

    def history(self, period='1y', interval='1d', **kw):
        if self._history is not None:
            return self._history.copy()
        if interval in ('1h',):
            return hourly_history()
        if interval == '1d':
            return (self._daily if self._daily is not None else daily_history(dividends=(40, 103, 166, 229))).copy()
        return monthly_history()

    def get_earnings_dates(self, limit=12, offset=0):
        return self._earnings_dates if self._earnings_dates is not None else earnings_dates_df()


def is_strict_json_number(v):
    return v is None or (isinstance(v, (int, float)) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))))


class FakeDownload:
    """Doble de yf.download(group_by='ticker'): arma columnas (símbolo, campo) con los precios
    diarios de cada FakeTicker del registro. `calls` guarda la lista de símbolos de cada llamada
    (para verificar que la actualización en lote hace UNA llamada y no una por ticker)."""

    def __init__(self, registry, missing=()):
        self.registry = registry
        self.missing = set(missing)      # símbolos que el lote "no trae" (fuerza el reintento individual)
        self.calls = []

    def __call__(self, symbols, period='1y', interval='1d', group_by='ticker', **kw):
        symbols = [symbols] if isinstance(symbols, str) else list(symbols)
        self.calls.append(symbols)
        frames = {}
        for s in symbols:
            if s in self.missing:
                continue
            t = self.registry.get(s) or FakeTicker(s)
            frames[s] = t.history(period=period, interval=interval)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, axis=1)
