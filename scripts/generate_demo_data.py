"""Regenerate the FICTIONAL demo data and the frozen price fixtures used by the Lab tests.

Everything this script writes is synthetic:
- Demo tickers get seeded random-walk prices (geometric, correlated with SPY or BTC-USD through a
  fixed beta). They are NOT real market prices. Benchmark/index columns already present in the
  fixtures (SPY, QQQ, sector ETFs, GLD, BTC-USD...) are kept as they are.
- sample_data/ledger.csv (opening positions, clean format) and sample_data/investments_ledger.csv
  (2025-2026 operations in the spreadsheet-export format: Spanish-locale numbers, d/m/yy dates,
  stablecoin rows, a swap, dividends, an excluded platform) are fictional, priced at those synthetic
  closes; sample_data/portfolio.csv holds the resulting quantities with an average-cost basis.
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

# Fictional operations. Opening positions go to the history file (sample_data/ledger.csv, clean
# format); everything else goes to the investments ledger (sample_data/investments_ledger.csv, the
# spreadsheet-export format read by lab/ledger.py). Prices come from the synthetic closes.
# kind: apertura | compra | venta | div_unidades | div_efectivo | stable | canje (buy `t` paying with
# `paid` units of another asset) | blank. 'BankC' is listed in config/lab/config.json ->
# excluded_platforms (out of the Lab's scope): its NU rows explain the reconciliation gap.
OPENING = [('VTI', 10), ('JPM', 8), ('JNJ', 10), ('BND', 20), ('SOL', 6), ('ADA', 2000)]
OPENING_DATE = '2025-01-02'
OPS = [
    # (date, kind, ticker, quantity, platform, extra)
    ('2025-02-03', 'compra', 'ORCL', 5, 'BrokerA', None),
    ('2025-02-18', 'compra', 'COST', 2, 'BrokerA', None),
    ('2025-03-03', 'compra', 'VNQ', 12, 'BrokerA', None),
    ('2025-03-17', 'compra', 'CRM', 4, 'BrokerA', None),
    ('2025-04-01', 'stable', 'USDT', 500, 'ExchangeB', None),
    ('2025-04-07', 'compra', 'VTI', 3, 'BrokerA', 'multiline'),
    ('2025-04-08', 'compra', 'SOL', 4, 'ExchangeB', None),
    ('2025-04-21', 'compra', 'JPM', 6, 'BrokerA', None),
    ('2025-05-06', 'compra', 'HD', 3, 'BrokerA', None),
    ('2025-05-20', 'compra', 'NU', 60, 'BrokerA', None),
    ('2025-06-10', 'compra', 'ADA', 1500, 'ExchangeB', None),
    ('2025-06-10', 'div_efectivo', 'JNJ', 12.40, 'BrokerA', None),
    ('2025-07-01', 'compra', 'NFLX', 15, 'BrokerA', None),
    ('2025-08-05', 'compra', 'BND', 10, 'BrokerA', None),
    ('2025-09-15', 'venta', 'SOL', 0.5, 'ExchangeB', None),
    ('2025-09-22', 'compra', 'ORCL', 3, 'BrokerA', None),
    ('2025-09-30', 'div_efectivo', 'VTI', 9.85, 'BrokerA', None),
    ('2025-10-07', 'compra', 'NU', 50, 'BankC', None),
    ('2025-10-14', 'compra', 'VTI', 2, 'BrokerA', None),
    ('2025-11-03', 'compra', 'VNQ', 8, 'BrokerA', None),
    ('2025-12-01', 'div_unidades', 'ADA', 25, 'ExchangeB', None),
    ('2025-12-15', 'compra', 'JNJ', 2, 'BrokerA', None),
    ('2025-12-29', 'div_efectivo', 'BND', 52.10, 'BrokerA', None),
    (None, 'blank', None, None, None, None),
    ('2026-01-05', 'stable', 'USDT', 300, 'ExchangeB', None),
    ('2026-01-20', 'venta', 'JPM', 2, 'BrokerA', None),
    ('2026-02-10', 'venta', 'ADA', 500, 'ExchangeB', None),
    ('2026-03-02', 'div_unidades', 'SOL', 0.5, 'ExchangeB', None),
    ('2026-03-16', 'compra', 'NFLX', 8, 'BrokerA', None),
    ('2026-04-14', 'compra', 'NU', 40, 'BrokerA', None),
    ('2026-04-30', 'div_efectivo', 'JPM', 18.00, 'BrokerA', None),
    ('2026-05-12', 'compra', 'COST', 1, 'BrokerA', None),
    ('2026-06-15', 'compra', 'CRM', 3, 'BrokerA', None),
    ('2026-06-30', 'div_efectivo', 'VNQ', 21.30, 'BrokerA', None),
    ('2026-07-07', 'compra', 'HD', 2, 'BrokerA', None),
    ('2026-07-20', 'venta', 'NU', 5, 'BankC', None),
    ('2026-08-18', 'venta', 'NFLX', 3, 'BrokerA', None),
    ('2026-09-08', 'canje', 'SOL', None, 'ExchangeB', ('ADA', 300)),
    ('2026-09-15', 'div_efectivo', 'COST', 4.65, 'BrokerA', None),
    ('2026-10-06', 'compra', 'BND', 5, 'BrokerA', None),
]
ASSET_TYPE = {'Stock': 'Acción', 'ETF': 'ETF', 'Crypto': 'Criptomoneda'}
LEDGER_HEADER = ['Fecha', 'Activo', 'Símbolo', 'Tipo de Operación', 'Tipo de Activo', 'Cantidad',
                 'Plataforma', 'Precio en USD (MEP)', 'Precio en moneda de cambio', 'Moneda de cambio',
                 'Inversión en dólares', 'Ultimo valor de mercado reportado', 'Variación', 'Valor',
                 'Var2', 'Ratio CEDEARS']


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


def es_num(v, decimals=2):
    """Spanish-locale number as the spreadsheet exports it: 1234.5 -> '1.234,50'."""
    txt = f'{abs(v):,.{decimals}f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    if decimals > 2:
        txt = txt.rstrip('0').rstrip(',') if ',' in txt else txt
    return ('-' if v < 0 else '') + txt


def dmy(date):
    y, m, d = date.split('-')
    return f'{int(d)}/{int(m)}/{y[2:]}'


def build_ledger(main):
    """Rows of the history file (openings) and of the investments ledger (spreadsheet format)."""
    from portfolio_data import PRESETS
    hist = [[OPENING_DATE, t, f'{q:g}', '', '', 'apertura', ''] for t, q in OPENING]
    rows = []
    for date, kind, t, q, plat, extra in OPS:
        if kind == 'blank':
            rows.append([''] * len(LEDGER_HEADER))
            continue
        if kind == 'stable':
            rows.append([dmy(date), 'Tether', t, 'Compra', 'Stable', es_num(q, 2), plat, 'US$\xa01,00',
                         '1', 'USD', f'US$\t{es_num(q)}', '', '', '', '', ''])
            continue
        sym = UNIVERSE[t][0]
        d, px = close_on(main, sym, date)
        name = PRESETS[t]['name']
        if extra == 'multiline':
            name = name.replace(' Stock ', '\nStock ')
        atype = ASSET_TYPE[PRESETS[t]['type']]
        cur = 'USDT' if plat == 'ExchangeB' else 'USD'
        pxs = es_num(px, 4 if px < 5 else 2)
        ratio = '1' if atype != 'Criptomoneda' else ''
        if kind == 'div_efectivo':
            rows.append([dmy(d), name, t, 'Dividendos', atype, '', plat, '', '', cur,
                         f'US$\t({es_num(q)})', '', '', '', '', ratio])
        elif kind == 'div_unidades':
            rows.append([dmy(d), name, t, 'Dividendos', atype, es_num(q, 6), plat, '', '', '', '',
                         '', '', '', '', ratio])
        elif kind == 'canje':
            paid_t, paid_q = extra
            _, ppx = close_on(main, UNIVERSE[paid_t][0], date)
            monto = paid_q * ppx
            qty = round(monto / px, 4)
            pc = paid_q / qty                        # units of the paid asset per unit bought
            rows.append([dmy(d), name, t, 'Compra', atype, es_num(qty, 6), plat, f'US$\xa0{pxs}',
                         es_num(pc, 6), paid_t, f'US$\t{es_num(monto)}', '', '', '', '', ratio])
        else:
            monto = q * px
            qtxt = ('-' if kind == 'venta' else '') + es_num(q, 6)
            inv = f'US$\t({es_num(monto)})' if kind == 'venta' else f'US$\t{es_num(monto)}'
            rows.append([dmy(d), name, t, kind.capitalize(), atype, qtxt, plat, f'US$\xa0{pxs}', pxs, cur,
                         inv, f'US$\t{es_num(q * px * 1.05)}', '5,00%', '', '', ratio])
    return hist, rows


def holdings_and_cost(main):
    """Final quantity and average-cost basis per ticker, from the files as the Lab parses them
    (excluded-platform rows included: they are real holdings outside the Lab's scope)."""
    from lab import ledger
    res = ledger.load_ledger()
    assert not res['errors'], res['errors']
    flows = [dict(o) for o in res['ops'] if o['cantidad'] is not None]
    for x in res['excluded_ops']:
        d = next(op[0] for op in OPS if op[2] == x['ticker'] and op[4] == x['plataforma'])
        _, px = close_on(main, UNIVERSE[x['ticker']][0], d)
        flows.append({'fecha': d, 'ticker': x['ticker'], 'cantidad': x['cantidad'], 'tipo': 'compra',
                      'monto_usd': abs(x['cantidad']) * px})
    flows.sort(key=lambda o: o['fecha'])
    qty, cost = {}, {}
    for o in flows:
        t, q = o['ticker'], o['cantidad']
        if o['tipo'] == 'apertura':
            _, px = close_on(main, UNIVERSE[t][0], o['fecha'])
            cost[t] = cost.get(t, 0) + q * px        # opening position valued at that day's close
        elif o['tipo'] == 'dividendo_unidades':
            pass                                     # units at zero cost
        elif q > 0:
            cost[t] = cost.get(t, 0) + (o['monto_usd'] or 0)
        else:
            cost[t] = cost.get(t, 0) * (1 + q / qty[t])   # a sale removes its share of cost
        qty[t] = qty.get(t, 0) + q
    return qty, cost


def main():
    rng = np.random.default_rng(SEED)
    df = pd.read_csv(MAIN, index_col=0, parse_dates=True)
    sdf = pd.read_csv(STRESS, index_col=0, parse_dates=True)
    m = synth_main(df, rng)
    s = synth_stress(sdf, m, rng)
    m.to_csv(MAIN, index_label='date')
    s.to_csv(STRESS, index_label='date')

    hist, rows = build_ledger(m)
    with open(os.path.join(SAMPLE, 'ledger.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, delimiter=';', lineterminator='\n')
        w.writerow(['fecha', 'ticker', 'cantidad', 'precio_unitario_usd', 'monto_usd', 'origen', 'plataforma'])
        w.writerows(hist)
    with open(os.path.join(SAMPLE, 'investments_ledger.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';', lineterminator='\n')
        w.writerow(LEDGER_HEADER)
        w.writerows(rows)

    qty, cost = holdings_and_cost(m)
    from portfolio_data import PRESETS
    with open(os.path.join(SAMPLE, 'portfolio.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['ticker', 'name', 'type', 'quantity', 'cost_basis_usd'])
        for t in UNIVERSE:
            p = PRESETS[t]
            w.writerow([t, p['name'], p['type'], f'{round(qty[t], 6):g}', f'{cost[t]:.2f}'])

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
