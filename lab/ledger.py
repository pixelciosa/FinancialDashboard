"""Operations ledger of the Lab: parser of the investments ledger (spreadsheet export), reader
of the opening/history file, loader and reconciliation against the current holdings. Pure functions
(they take text/paths and return data). Does not import server.py. Missing data is None, never 0
(except an explicit zero cost).

The Lab reads two kinds of input (same split as the private edition: a history file + the
investments ledger exported from a spreadsheet):

1. History / opening positions (`LEDGER_CSV`, default sample_data/ledger.csv), clean CSV:

       fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen[;plataforma]

   - fecha YYYY-MM-DD; cantidad negative for sales; monto_usd is the absolute cash flow.
   - origen: apertura | compra | venta | dividendo (units at zero cost) |
             dividendo_efectivo (cash income, no units).

2. Investments ledger files (spreadsheet export, e.g. Numbers/Excel "CSV with ;"), listed in
   config/lab/config.json -> `ledger_files` (paths relative to the project or to `ledger_dir`), or
   in the env var INVESTMENTS_LEDGER_CSV (several paths separated by the OS path separator).
   Default demo file: sample_data/investments_ledger.csv (fictional). One header row, then:

       Fecha;Activo;Símbolo;Tipo de Operación;Tipo de Activo;Cantidad;Plataforma;
       Precio en USD (MEP);Precio en moneda de cambio;Moneda de cambio;Inversión en dólares[;...]

   - Only the first 11 columns are read (market value / ratio columns after them are ignored).
   - Fecha d/m/yy or d/m/yyyy. Numbers in Spanish locale: '1.234,56', 'US$\\t(150,00)' (parentheses
     or '-' = negative); the amount is always taken as absolute.
   - Tipo de Operación: Compra | Venta | Dividendos (with Cantidad: units at zero cost; without it:
     cash income from 'Inversión en dólares').
   - Tipo de Activo 'Stable' (stablecoins) is cash, not an investment: skipped and counted.
   - Moneda de cambio: a cash currency (USD, USDT, USDC, ARS...) or another asset. Paying with
     another asset is a swap ('canje'): a sale leg of that asset is generated with
     quantity = Cantidad x 'Precio en moneda de cambio'.
   - Plataforma: rows from `excluded_platforms` are out of the Lab's scope but remembered, so the
     reconciliation can explain a gap as 'esperada_excluida'. When `included_platforms` is not
     empty, any other platform is a row error (catches typos).
   - Holder ('titular'): `titular` of the ledger_files entry, else the part of the file name after
     the last '-' ('investments 26-HolderA.csv' -> 'HolderA'), else None.
   Symbols are normalized (spaces removed, upper case, '-USD' suffix dropped). A bad row never
   aborts the rest: it is reported in `errors` with its line number.
"""
import csv
import io
import json
import math
import os
import re
from datetime import datetime

from . import paths

ROOT = paths.ROOT
DATA_DIR = paths.DATA_DIR
CONFIG_PATH = paths.config_file('config.json')
LEDGER_PATH = paths.LEDGER_CSV
PORTFOLIO_CSV = paths.PORTFOLIO_CSV
INVESTMENTS_ENV = 'INVESTMENTS_LEDGER_CSV'

TIPOS = ('apertura', 'compra', 'venta', 'canje', 'dividendo_unidades', 'dividendo_efectivo')
ORIGENES = {'apertura': 'apertura', 'compra': 'compra', 'venta': 'venta',
            'dividendo': 'dividendo_unidades', 'dividendo_efectivo': 'dividendo_efectivo'}
REQUIRED_COLUMNS = ('fecha', 'ticker', 'cantidad', 'precio_unitario_usd', 'monto_usd', 'origen')

# Investments ledger (spreadsheet export): column positions, only up to "Inversion en dolares".
(COL_FECHA, COL_ACTIVO, COL_SIMBOLO, COL_OP, COL_TIPO_ACTIVO, COL_CANT, COL_PLAT,
 COL_PRECIO, COL_PRECIO_CAMBIO, COL_MONEDA, COL_INV) = range(11)
LEDGER_MIN_COLS = 11
# Exchange currencies that are NOT invested assets (anything else, if not empty => swap)
NON_INVESTED_CURRENCIES = {'USD', 'USDC', 'USDT', 'USDX', 'DAI', 'PESOS', 'ARS', 'EUR', 'BRL', ''}


def _clean_num(x):
    """Finite float or None (never NaN/inf)."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_signed(t):
    neg = False
    if t.startswith('(') and t.endswith(')'):
        neg, t = True, t[1:-1]
    if t.startswith('-'):
        neg, t = True, t[1:]
    return neg, t


def parse_number(s):
    """History file numbers: '1234.5', '(1,234.50)', 'US$ 12' -> float. Empty -> None.
    Garbage -> ValueError. Dot is the decimal separator; commas are thousands separators."""
    if s is None:
        return None
    t = re.sub(r'US\$|\$|\s|\xa0', '', str(s))
    if t == '':
        return None
    neg, t = _parse_signed(t)
    v = float(t.replace(',', ''))  # ValueError on garbage
    if not math.isfinite(v):
        raise ValueError('non-finite number')
    return -v if neg else v


def parse_number_es(s):
    """Investments ledger numbers (Spanish locale): 'US$\\t(1.234,50)' -> -1234.5,
    '0,25' -> 0.25. Empty -> None. Garbage -> ValueError."""
    if s is None:
        return None
    t = re.sub(r'US\$|\$|\s|\xa0', '', str(s))
    if t == '':
        return None
    neg, t = _parse_signed(t)
    v = float(t.replace('.', '').replace(',', '.'))  # ValueError on garbage
    if not math.isfinite(v):
        raise ValueError('non-finite number')
    return -v if neg else v


def parse_date_dmy(s):
    """'13/1/26' -> '2026-01-13'. Two- or four-digit year. Invalid date -> ValueError."""
    d, m, y = [p.strip() for p in str(s).split('/')]
    y = int(y)
    if y < 100:
        y += 2000
    return datetime(y, int(m), int(d)).strftime('%Y-%m-%d')


def normalize_symbol(s):
    t = re.sub(r'\s+', '', str(s or '')).upper()
    if t.endswith('-USD'):
        t = t[:-4]
    return t


def _op(fecha, ticker, cantidad, precio, monto, tipo, plataforma, origen, nota=None, titular=None):
    return {'fecha': fecha, 'ticker': ticker, 'cantidad': _clean_num(cantidad),
            'precio_usd': _clean_num(precio), 'monto_usd': _clean_num(monto), 'tipo': tipo,
            'plataforma': plataforma, 'titular': titular, 'origen': origen, 'nota': nota}


def _norm_platforms(platforms):
    return {str(p).strip().lower(): str(p).strip() for p in (platforms or []) if str(p).strip()}


def parse_history_text(text, excluded_platforms=()):
    """Parse the history / opening-positions CSV (clean format, see the module docstring).

    Returns {'ops', 'errors': [{'fila', 'error'}], 'excluded': {'plataforma_excluida', 'vacias'},
    'excluded_ops': [{'ticker', 'cantidad', 'plataforma'}]}. A bad row never aborts the rest."""
    if text.startswith('\ufeff'):
        text = text[1:]
    excluded_set = set(_norm_platforms(excluded_platforms))
    ops, errors, excluded_ops = [], [], []
    excl = {'plataforma_excluida': 0, 'vacias': 0}
    reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=';')
    header = [str(h or '').strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        return {'ops': [], 'errors': [{'fila': 1, 'error': f'missing columns: {", ".join(missing)}'}],
                'excluded': excl, 'excluded_ops': []}
    for raw in reader:
        lineno = reader.line_num
        r = {str(k or '').strip().lower(): (v or '').strip() for k, v in raw.items() if k is not None}
        if not any(r.values()):
            excl['vacias'] += 1
            continue
        try:
            fecha = datetime.strptime(r['fecha'], '%Y-%m-%d').strftime('%Y-%m-%d')
            ticker = normalize_symbol(r['ticker'])
            if not ticker:
                raise ValueError('empty ticker')
            origen = r['origen'].lower()
            tipo = ORIGENES.get(origen)
            if tipo is None:
                raise ValueError(f'unknown origen: {r["origen"]!r}')
            qty = parse_number(r['cantidad'])
            precio = parse_number(r['precio_unitario_usd'])
            monto = parse_number(r['monto_usd'])
            monto = abs(monto) if monto is not None else None
            plat = r.get('plataforma') or None
            if tipo in ('compra', 'venta'):
                if qty is None or qty == 0:
                    raise ValueError('quantity missing')
                qty = -abs(qty) if tipo == 'venta' else abs(qty)
                if monto is None and precio is not None:
                    monto = abs(qty) * precio
                if monto is None:
                    raise ValueError('price or amount missing')
            elif tipo == 'apertura':
                if qty is None:
                    raise ValueError('quantity missing')
            elif tipo == 'dividendo_unidades':
                if qty is None or qty == 0:
                    raise ValueError('dividend without units (use dividendo_efectivo for cash)')
                qty, nota_div = abs(qty), 'units at zero cost'
            else:  # dividendo_efectivo
                if monto is None:
                    raise ValueError('cash dividend without amount')
                qty = None
            if plat and plat.lower() in excluded_set:
                excl['plataforma_excluida'] += 1
                if qty is not None and tipo != 'dividendo_efectivo':
                    excluded_ops.append({'ticker': ticker, 'cantidad': qty, 'plataforma': plat})
                continue
            if tipo == 'dividendo_unidades':
                ops.append(_op(fecha, ticker, qty, 0.0, 0.0, tipo, plat, origen, nota_div))
            elif tipo == 'dividendo_efectivo':
                ops.append(_op(fecha, ticker, None, None, monto, tipo, plat, origen, 'income, not a flow'))
            else:
                ops.append(_op(fecha, ticker, qty, precio, monto, tipo, plat, origen))
        except (ValueError, KeyError, TypeError) as e:
            errors.append({'fila': lineno, 'error': str(e)})
    return {'ops': ops, 'errors': errors, 'excluded': excl, 'excluded_ops': excluded_ops}


def _record_excluded(acc, row, plat):
    """Ticker and signed quantity (buy +, sell -) of an excluded-platform row; unreadable rows are skipped."""
    try:
        tipo_op = row[COL_OP].strip().lower()
        if tipo_op not in ('compra', 'venta'):
            return
        ticker = normalize_symbol(row[COL_SIMBOLO])
        qty = parse_number_es(row[COL_CANT])
        if not ticker or qty is None or qty == 0:
            return
        acc.append({'ticker': ticker, 'cantidad': -abs(qty) if tipo_op == 'venta' else abs(qty),
                    'plataforma': plat})
    except (ValueError, IndexError):
        return


def parse_ledger_text(text, titular=None, excluded_platforms=(), included_platforms=(),
                      origen='libro'):
    """Parse one investments ledger export (spreadsheet format, see the module docstring).

    Returns {'ops', 'errors': [{'fila', 'error'}],
             'excluded': {'plataforma_excluida', 'stable', 'vacias'},
             'excluded_ops': [{'ticker', 'cantidad', 'plataforma'}]}. A bad row never aborts the rest."""
    if text.startswith('\ufeff'):
        text = text[1:]
    excluded_map = _norm_platforms(excluded_platforms)
    included_map = _norm_platforms(included_platforms)
    ops, errors, excluded_ops = [], [], []
    excl = {'plataforma_excluida': 0, 'stable': 0, 'vacias': 0}
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=';')
    first = True
    for row in reader:
        lineno = reader.line_num
        if first:
            first = False
            continue  # header
        row = row[:LEDGER_MIN_COLS]  # market value / ratio columns are ignored
        if not any(c.strip() for c in row):
            excl['vacias'] += 1
            continue
        if len(row) < LEDGER_MIN_COLS:
            errors.append({'fila': lineno, 'error': 'row has fewer columns than expected'})
            continue
        try:
            plat_raw = row[COL_PLAT].strip()
            key = plat_raw.lower()
            if key in excluded_map:
                excl['plataforma_excluida'] += 1
                _record_excluded(excluded_ops, row, plat_raw)
                continue
            if included_map and key not in included_map:
                raise ValueError(f'unknown platform: {plat_raw!r}')
            if row[COL_TIPO_ACTIVO].strip().lower() == 'stable':
                excl['stable'] += 1
                continue
            plat = included_map.get(key) or plat_raw or None
            fecha = parse_date_dmy(row[COL_FECHA])
            ticker = normalize_symbol(row[COL_SIMBOLO])
            if not ticker:
                raise ValueError('empty symbol')
            tipo_op = row[COL_OP].strip().lower()
            qty = parse_number_es(row[COL_CANT])
            precio = parse_number_es(row[COL_PRECIO])
            inv = parse_number_es(row[COL_INV])
            monto = abs(inv) if inv is not None else None
            moneda = row[COL_MONEDA].strip()
            if tipo_op in ('compra', 'venta'):
                if qty is None or qty == 0:
                    raise ValueError('quantity missing')
                qty = -abs(qty) if tipo_op == 'venta' else abs(qty)
                if monto is None:
                    raise ValueError('investment in dollars missing')
                if moneda.upper() not in NON_INVESTED_CURRENCIES:
                    # Swap: paid with another asset -> sale leg of the asset handed over.
                    cambio = normalize_symbol(moneda)
                    pc = parse_number_es(row[COL_PRECIO_CAMBIO])
                    if pc is None:
                        raise ValueError('swap without price in the exchange currency')
                    leg_qty = abs(qty) * pc
                    ops.append(_op(fecha, ticker, qty, precio, monto, tipo_op, plat, origen,
                                   f'swap against {cambio}', titular))
                    ops.append(_op(fecha, cambio, -leg_qty, monto / leg_qty if leg_qty else None,
                                   monto, 'canje', plat, origen,
                                   f'sale leg of the swap for {ticker}', titular))
                    continue
                ops.append(_op(fecha, ticker, qty, precio, monto, tipo_op, plat, origen, None, titular))
            elif tipo_op in ('dividendos', 'dividendo'):
                if qty is not None and qty != 0:
                    ops.append(_op(fecha, ticker, abs(qty), 0.0, 0.0, 'dividendo_unidades',
                                   plat, origen, 'units at zero cost', titular))
                else:
                    if monto is None:
                        raise ValueError('dividend without quantity or amount')
                    ops.append(_op(fecha, ticker, None, None, monto, 'dividendo_efectivo',
                                   plat, origen, 'income, not a flow', titular))
            else:
                raise ValueError(f'unknown operation type: {row[COL_OP]!r}')
        except (ValueError, IndexError) as e:
            errors.append({'fila': lineno, 'error': str(e)})
    return {'ops': ops, 'errors': errors, 'excluded': excl, 'excluded_ops': excluded_ops}


def load_config(path=None):
    with open(path or CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)


DEFAULT_TRIM_THRESHOLD_PCT = 0.5


def trim_threshold_pct(config=None):
    """Threshold (in %) of the excess of a trimmed sale; `umbral_recorte_pct` in config.json."""
    try:
        cfg = config if config is not None else load_config()
        v = float(cfg.get('umbral_recorte_pct', DEFAULT_TRIM_THRESHOLD_PCT))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_TRIM_THRESHOLD_PCT
    return v if math.isfinite(v) and v >= 0 else DEFAULT_TRIM_THRESHOLD_PCT


def trimmed_sales(clipped, threshold_pct):
    """Sales the engine trimmed to the holding (engine.portfolio_series -> warnings
    'ventas_recortadas'.detalle: fecha, ticker, pedido, tenencia). Pure function.

    exceso = pedido - tenencia (units); exceso_pct = exceso / tenencia x 100 (None if the holding
    is 0: the excess is not measurable and the sale counts as above the threshold).
    Returns {'umbral_pct', 'lista' (above the threshold, by date), 'bajo_umbral' (count)}."""
    lista, bajo = [], 0
    for c in clipped or []:
        pedido, tenencia = c.get('pedido'), c.get('tenencia')
        if pedido is None or tenencia is None:
            continue
        exceso = pedido - tenencia
        pct = exceso / tenencia * 100 if tenencia > 0 else None
        if pct is not None and pct <= threshold_pct:
            bajo += 1
            continue
        lista.append({'ticker': c['ticker'], 'fecha': c['fecha'], 'exceso_unidades': None if _clean_num(exceso) is None else round(exceso, 8),
                      'exceso_pct': None if pct is None else round(pct, 4),
                      'pedido': _clean_num(pedido), 'tenencia': _clean_num(tenencia)})
    lista.sort(key=lambda r: (r['fecha'], r['ticker']))
    return {'umbral_pct': threshold_pct, 'lista': lista, 'bajo_umbral': bajo}


def _cfg_list(cfg, key):
    v = (cfg or {}).get(key) or []
    return [str(p) for p in v] if isinstance(v, list) else []


def excluded_platforms(config=None):
    try:
        cfg = config if config is not None else load_config()
    except (OSError, ValueError):
        return []
    return _cfg_list(cfg, 'excluded_platforms')


def titular_from_filename(name):
    """Holder label from the file name: the part after the last '-' ('inv 26-HolderA.csv' -> 'HolderA')."""
    stem = os.path.splitext(os.path.basename(str(name or '')))[0]
    if '-' not in stem:
        return None
    t = stem.rsplit('-', 1)[1].strip()
    return t or None


def ledger_files(config=None):
    """Investments ledger files as [{'path' (absolute), 'name', 'titular'}].

    INVESTMENTS_LEDGER_CSV (paths separated by os.pathsep) wins over config.json -> `ledger_files`.
    Relative paths resolve against `ledger_dir` (if set) or the project root."""
    cfg = config or {}
    env = os.environ.get(INVESTMENTS_ENV)
    if env:
        entries = [p for p in env.split(os.pathsep) if p.strip()]
    else:
        entries = cfg.get('ledger_files') or []
    base = cfg.get('ledger_dir') or ''
    base = base if os.path.isabs(base) else os.path.join(ROOT, base)
    out = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict):
            rel, titular = str(e.get('path') or ''), e.get('titular')
        else:
            rel, titular = str(e), None
        if not rel.strip():
            continue
        path = rel if os.path.isabs(rel) else os.path.join(base, rel)
        out.append({'path': path, 'name': os.path.basename(path),
                    'titular': titular if titular else titular_from_filename(path)})
    return out


def load_ledger(config=None, history_path=None):
    """Load the history file (LEDGER_CSV) and every investments ledger file.
    Missing files are reported in `errors`, never raised."""
    try:
        cfg = config if config is not None else load_config()
    except (OSError, ValueError):
        cfg = {}
    excluded = _cfg_list(cfg, 'excluded_platforms')
    included = _cfg_list(cfg, 'included_platforms')
    ops, errors, excluded_ops = [], [], []
    counts = {'historial': 0, 'excluidas': {'plataforma_excluida': 0, 'stable': 0, 'vacias': 0},
              'por_archivo': {}}

    def _add_excluded(res):
        for k, v in res['excluded'].items():
            counts['excluidas'][k] = counts['excluidas'].get(k, 0) + v

    hpath = history_path or LEDGER_PATH
    hname = os.path.basename(hpath)
    if os.path.exists(hpath):
        with open(hpath, encoding='utf-8-sig', newline='') as f:
            h = parse_history_text(f.read(), excluded)
        ops += h['ops']
        excluded_ops += h['excluded_ops']
        counts['historial'] = len(h['ops'])
        _add_excluded(h)
        errors += [dict(e, archivo=hname) for e in h['errors']]
    else:
        errors.append({'archivo': hname, 'fila': None, 'error': 'file not found'})
    for lf in ledger_files(cfg):
        name = lf['name']
        if not os.path.exists(lf['path']):
            errors.append({'archivo': name, 'fila': None, 'error': 'file not found'})
            counts['por_archivo'][name] = None
            continue
        with open(lf['path'], encoding='utf-8-sig', newline='') as f:
            res = parse_ledger_text(f.read(), lf['titular'], excluded, included)
        ops += res['ops']
        excluded_ops += res['excluded_ops']
        counts['por_archivo'][name] = len(res['ops'])
        _add_excluded(res)
        errors += [dict(e, archivo=name) for e in res['errors']]
    ops.sort(key=lambda o: o['fecha'])
    counts['total'] = len(ops)
    # Cash dividends are income, not operations/flows: they do not count as an operation.
    counts['ingresos_efectivo'] = sum(1 for o in ops if o['tipo'] == 'dividendo_efectivo')
    counts['operaciones'] = counts['total'] - counts['ingresos_efectivo']
    return {'ops': ops, 'errors': errors, 'counts': counts, 'excluded_ops': excluded_ops}


# --- Reconciliation ----------------------------------------------------------------
def final_quantities(ops):
    q = {}
    for o in ops:
        if o['cantidad'] is not None:
            q[o['ticker']] = q.get(o['ticker'], 0.0) + o['cantidad']
    return q


def parse_portfolio_holdings(text):
    """Read the portfolio CSV (columns ticker, quantity; comma-separated). Returns {ticker: quantity}."""
    if text.startswith('﻿'):
        text = text[1:]
    out = {}
    reader = csv.DictReader(io.StringIO(text, newline=''))
    for raw in reader:
        r = {str(k or '').strip().lower(): (v or '').strip() for k, v in raw.items() if k is not None}
        ticker = normalize_symbol(r.get('ticker'))
        if not ticker:
            continue
        try:
            v = parse_number(r.get('quantity'))
        except ValueError:
            continue
        if v is not None:
            out[ticker] = v
    return out


EXCLUIDA_MOTIVO = 'held on a platform excluded from the Lab (excluded_platforms in config.json)'
RESIDUO = 0.01


def excluded_quantities(excluded_ops):
    q = {}
    for o in excluded_ops or []:
        q[o['ticker']] = q.get(o['ticker'], 0.0) + o['cantidad']
    return q


def reconcile(ledger_qty, portfolio_qty, tol=0.02, tiny=0.001, excluded_qty=None):
    """Compare quantities per ticker.
    estado: ok / esperada_excluida / sin_historial / diff / sin_portfolio.
    - Residue: ledger with |quantity| < 0.01 and portfolio 0 or absent counts as ok.
    - esperada_excluida: ledger + excluded-platform quantity matches the portfolio within `tol`.
    - sin_historial: in the portfolio but without any operation in the ledger (informative)."""
    excluded_qty = excluded_qty or {}
    rows = []
    for t in sorted(set(ledger_qty) | set(portfolio_qty)):
        lq, pq = ledger_qty.get(t), portfolio_qty.get(t)
        diff = pct = None
        motivo = None
        if lq is None:
            estado = 'ok' if abs(pq) < tiny else 'sin_historial'
        elif pq is None:
            estado = 'ok' if abs(lq) < RESIDUO else 'sin_portfolio'
        else:
            diff = lq - pq
            base = max(abs(lq), abs(pq))
            pct = abs(diff) / base if base else 0.0
            if pct <= tol or (abs(lq) < tiny and abs(pq) < tiny) or (pq == 0 and abs(lq) < RESIDUO):
                estado = 'ok'
            else:
                estado = 'diff'
        if estado in ('diff', 'sin_historial', 'sin_portfolio') and t in excluded_qty:
            total = (lq or 0.0) + excluded_qty[t]
            target = pq or 0.0
            base = max(abs(total), abs(target))
            if base and abs(total - target) / base <= tol:
                estado, motivo = 'esperada_excluida', EXCLUIDA_MOTIVO
        rows.append({'ticker': t, 'libro': _clean_num(lq), 'portfolio': _clean_num(pq),
                     'diferencia': _clean_num(diff), 'diferencia_pct': _clean_num(pct),
                     'estado': estado, 'motivo': motivo})
    return rows


def summarize_reconcile(rows):
    """Group by state, with counts. diferencias_reales = diff + sin_portfolio."""
    grupos = {'ok': [], 'esperada_excluida': [], 'sin_historial': [], 'diferencias_reales': []}
    for r in rows:
        key = r['estado'] if r['estado'] in grupos else 'diferencias_reales'
        grupos[key].append(r['ticker'])
    return {k: {'conteo': len(v), 'tickers': v} for k, v in grupos.items()}
