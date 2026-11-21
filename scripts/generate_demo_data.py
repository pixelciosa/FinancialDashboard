"""Regenerate the FICTIONAL demo data and the frozen price fixtures used by the Lab tests.

Everything this script writes is synthetic:
- Demo tickers get seeded random-walk prices (geometric, correlated with SPY or BTC-USD through a
  fixed beta). They are NOT real market prices. Benchmark/index columns already present in the
  fixtures (SPY, QQQ, sector ETFs, GLD, BTC-USD...) are kept as they are.
- sample_data/ledger.csv is a fictional list of operations priced at those synthetic closes, and
  sample_data/portfolio.csv holds the resulting quantities with an average-cost basis.
- config/lab/blocks.json gets reference weights equal to the block weights on the last fixture day.

Usage:  python scripts/generate_demo_data.py      (deterministic: fixed seed)
After running it, re-derive the golden numbers in tests/lab/test_engine_golden.py and friends.
"""
import csv
import json
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, 'tests', 'lab', 'fixtures')
MAIN = os.path.join(FIX, 'prices_2026-11-13.csv')
STRESS = os.path.join(FIX, 'stress_prices.csv')
SAMPLE = os.path.join(ROOT, 'sample_data')
SEED = 2026

# ticker: (yfinance symbol, benchmark, beta, idiosyncratic annual vol, annual drift, first close, listed since)
UNIVERSE = {
    'JPM':  ('JPM', 'SPY', 1.10, 0.18, 0.10, 240.0, None),
    'COST': ('COST', 'SPY', 0.80, 0.18, 0.08, 920.0, None),
    'JNJ':  ('JNJ', 'SPY', 0.40, 0.15, 0.04, 145.0, None),
    'HD':   ('HD', 'SPY', 1.00, 0.20, 0.03, 410.0, None),
    'NFLX': ('NFLX', 'SPY', 1.20, 0.30, 0.15, 90.0, None),
    'ORCL': ('ORCL', 'SPY', 1.40, 0.35, 0.12, 175.0, None),
    'CRM':  ('CRM', 'SPY', 1.20, 0.30, -0.05, 335.0, None),
    'NU':   ('NU', 'SPY', 1.30, 0.40, 0.15, 11.0, '2021-12-09'),
    'VTI':  ('VTI', 'SPY', 1.00, 0.02, 0.00, 295.0, None),
    'BND':  ('BND', 'SPY', 0.05, 0.05, 0.00, 72.0, None),
    'VNQ':  ('VNQ', 'SPY', 0.80, 0.12, 0.00, 90.0, None),
    'SOL':  ('SOL-USD', 'BTC-USD', 1.30, 0.60, -0.10, 220.0, '2020-04-10'),
    'ADA':  ('ADA-USD', 'BTC-USD', 1.20, 0.60, -0.20, 1.0, None),
}
INDEX_COLUMNS = ['SPY', 'QQQ', 'XLK', 'SMH', 'XLC', 'XLY', 'XLF', 'XLV', 'XLI', 'XLP', 'XLE', 'XLB',
                 'XLU', 'XLRE', 'GLD', 'SPYD', 'EWZ', 'ARGT', 'BTC-USD']
STRESS_PROXIES = ['SPY', 'QQQ', 'XLP', 'SMH', 'GLD', 'BTC-USD']

# Fictional operations: (date, ticker, quantity, origen, platform). Prices come from the synthetic
# closes. 'BankC' is listed in config/lab/config.json -> excluded_platforms (out of the Lab's scope).
OPS = [
    ('2025-01-02', 'VTI', 10, 'apertura', ''), ('2025-01-02', 'JPM', 8, 'apertura', ''),
    ('2025-01-02', 'JNJ', 10, 'apertura', ''), ('2025-01-02', 'BND', 20, 'apertura', ''),
    ('2025-01-02', 'SOL', 6, 'apertura', ''), ('2025-01-02', 'ADA', 2000, 'apertura', ''),
    ('2025-02-03', 'ORCL', 5, 'compra', 'BrokerA'), ('2025-02-18', 'COST', 2, 'compra', 'BrokerA'),
    ('2025-03-03', 'VNQ', 12, 'compra', 'BrokerA'), ('2025-03-17', 'CRM', 4, 'compra', 'BrokerA'),
    ('2025-04-07', 'VTI', 3, 'compra', 'BrokerA'), ('2025-04-08', 'SOL', 4, 'compra', 'ExchangeB'),
    ('2025-04-21', 'JPM', 6, 'compra', 'BrokerA'), ('2025-05-06', 'HD', 3, 'compra', 'BrokerA'),
    ('2025-05-20', 'NU', 60, 'compra', 'BrokerA'), ('2025-06-10', 'ADA', 1500, 'compra', 'ExchangeB'),
    ('2025-06-10', 'JNJ', None, 'dividendo_efectivo', 'BrokerA'),
    ('2025-07-01', 'NFLX', 15, 'compra', 'BrokerA'), ('2025-08-05', 'BND', 10, 'compra', 'BrokerA'),
    ('2025-09-15', 'SOL', -0.5, 'venta', 'ExchangeB'), ('2025-09-22', 'ORCL', 3, 'compra', 'BrokerA'),
    ('2025-10-07', 'NU', 50, 'compra', 'BankC'), ('2025-10-14', 'VTI', 2, 'compra', 'BrokerA'),
    ('2025-11-03', 'VNQ', 8, 'compra', 'BrokerA'), ('2025-12-29', 'BND', None, 'dividendo_efectivo', 'BrokerA'),
    ('2026-01-20', 'JPM', -2, 'venta', 'BrokerA'), ('2026-02-10', 'ADA', -500, 'venta', 'ExchangeB'),
    ('2026-03-02', 'SOL', 0.5, 'dividendo', 'ExchangeB'), ('2026-03-16', 'NFLX', 8, 'compra', 'BrokerA'),
    ('2026-04-14', 'NU', 40, 'compra', 'BrokerA'), ('2026-04-30', 'JPM', None, 'dividendo_efectivo', 'BrokerA'),
    ('2026-05-12', 'COST', 1, 'compra', 'BrokerA'), ('2026-06-15', 'CRM', 3, 'compra', 'BrokerA'),
    ('2026-07-07', 'HD', 2, 'compra', 'BrokerA'), ('2026-08-18', 'NFLX', -3, 'venta', 'BrokerA'),
]
CASH_DIVIDENDS = {('2025-06-10', 'JNJ'): 12.40, ('2025-12-29', 'BND'): 52.10, ('2026-04-30', 'JPM'): 18.00}


def _bench_returns(series):
    s = series.ffill()
    return np.log(s / s.shift(1)).fillna(0.0).to_numpy()


def synth_main(df, rng):
    out = df[[c for c in INDEX_COLUMNS if c in df.columns]].copy()
    for t, (sym, bench, beta, vol, drift, p0, listed) in UNIVERSE.items():
        br = _bench_returns(out[bench])
        crypto = sym.endswith('-USD')
        days = np.r_[0, np.diff(out.index.values).astype('timedelta64[D]').astype(int)]
        noise = rng.standard_normal(len(out)) * vol * np.sqrt(np.maximum(days, 1) / 365.0)
        lr = beta * br + noise + drift * days / 365.0
        lr[0] = 0.0
        path = p0 * np.exp(np.cumsum(lr))
        col = pd.Series(path, index=out.index)
        if not crypto:
            col[out[bench].isna()] = np.nan
        out[sym] = col.round(4 if p0 < 5 else 2)
    return out


def synth_stress(sdf, main, rng):
    out = sdf[[c for c in STRESS_PROXIES if c in sdf.columns]].copy()
    for t, (sym, bench, beta, vol, drift, p0, listed) in UNIVERSE.items():
        br = _bench_returns(out[bench])
        days = np.r_[0, np.diff(out.index.values).astype('timedelta64[D]').astype(int)]
        noise = rng.standard_normal(len(out)) * vol * np.sqrt(np.maximum(days, 1) / 365.0)
        lr = beta * br + noise
        lr[0] = 0.0
        col = pd.Series(p0 * 0.6 * np.exp(np.cumsum(lr)), index=out.index)
        recent = out.index >= main.index[0]
        col[recent] = main[sym].reindex(out.index[recent]).to_numpy()   # same closes as the main fixture
        if not sym.endswith('-USD'):
            col[out[bench].isna()] = np.nan
        if listed:
            col[out.index < pd.Timestamp(listed)] = np.nan
        out[sym] = col.round(4 if p0 < 5 else 2)
    return out


def close_on(main, sym, date):
    s = main[sym].dropna()
    s = s[s.index >= pd.Timestamp(date)]
    return s.index[0].strftime('%Y-%m-%d'), float(s.iloc[0])


def build_ledger(main):
    rows, qty, cost = [], {}, {}
    for date, t, q, origen, plat in OPS:
        sym = UNIVERSE[t][0]
        d, px = close_on(main, sym, date)
        if origen == 'dividendo_efectivo':
            rows.append([d, t, '', '', f'{CASH_DIVIDENDS[(date, t)]:.2f}', origen, plat])
            continue
        if origen == 'dividendo':
            rows.append([d, t, f'{q:g}', '', '', origen, plat])
            qty[t] = qty.get(t, 0) + q
            continue
        if origen == 'apertura':
            rows.append([d, t, f'{q:g}', '', '', origen, plat])
            qty[t] = qty.get(t, 0) + q
            cost[t] = cost.get(t, 0) + q * px          # opening position valued at that day's close
            continue
        monto = abs(q) * px
        rows.append([d, t, f'{q:g}', f'{px:.4f}' if px < 5 else f'{px:.2f}', f'{monto:.2f}', origen, plat])
        if q > 0:
            cost[t] = cost.get(t, 0) + monto
        else:
            cost[t] = cost.get(t, 0) * (1 + q / qty[t])   # average cost: a sale removes its share of cost
        qty[t] = qty.get(t, 0) + q
    return rows, qty, cost


def main():
    rng = np.random.default_rng(SEED)
    df = pd.read_csv(MAIN, index_col=0, parse_dates=True)
    sdf = pd.read_csv(STRESS, index_col=0, parse_dates=True)
    m = synth_main(df, rng)
    s = synth_stress(sdf, m, rng)
    m.to_csv(MAIN, index_label='date')
    s.to_csv(STRESS, index_label='date')

    rows, qty, cost = build_ledger(m)
    with open(os.path.join(SAMPLE, 'ledger.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, delimiter=';', lineterminator='\n')
        w.writerow(['fecha', 'ticker', 'cantidad', 'precio_unitario_usd', 'monto_usd', 'origen', 'plataforma'])
        w.writerows(rows)

    from portfolio_data import PRESETS
    with open(os.path.join(SAMPLE, 'portfolio.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['ticker', 'name', 'type', 'quantity', 'cost_basis_usd'])
        for t in UNIVERSE:
            p = PRESETS[t]
            w.writerow([t, p['name'], p['type'], f'{qty[t]:g}', f'{cost[t]:.2f}'])

    # Reference weights of the risk blocks = weights on the last fixture day (descriptive mode).
    from lab import blocks, engine, ledger, prices, stress
    ops = prices.to_price_ops(ledger.load_ledger()['ops'])
    dates, px = prices.align(pd.read_csv(MAIN, index_col=0, parse_dates=True))
    port = engine.portfolio_series(dates, px, ops)
    pos, total, _ = stress.current_weights(port['holdings_end'], stress.last_prices_from(px))
    path = blocks.BLOCKS_PATH
    with open(path, encoding='utf-8') as f:
        cfg_raw = json.load(f)
    cfg = blocks.load_blocks()
    by_block = {}
    for p in pos:
        b = blocks.block_of(p['ticker'], cfg['map'])
        by_block[b] = by_block.get(b, 0) + p['peso'] * 100
    for b in cfg_raw['bloques']:
        b['bandas']['referencia_pct'] = round(by_block.get(b['id'], 0.0), 1)
    cfg_raw['regla_banda']['fecha_referencia'] = dates[-1]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg_raw, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('total value', round(total, 2), {k: round(v, 1) for k, v in by_block.items()})
    print('positions', {p['ticker']: round(p['peso'] * 100, 1) for p in pos})


if __name__ == '__main__':
    main()
