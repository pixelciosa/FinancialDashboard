"""Operations ledger of the Lab: parser of the clean CSV format, loader and reconciliation against
the current holdings. Pure functions (they take text/paths and return data). Does not import
server.py. Missing data is None, never 0 (except an explicit zero cost).

Ledger format (`LEDGER_CSV`, default sample_data/ledger.csv), semicolon-separated, UTF-8:

    fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen[;plataforma]

- fecha: YYYY-MM-DD.
- ticker: as in the portfolio CSV; crypto with or without the -USD suffix.
- cantidad: units; negative for sales (a 'venta' with a positive quantity is negated).
- precio_unitario_usd / monto_usd: optional; monto_usd is the absolute cash flow of the operation.
- origen: apertura | compra | venta | dividendo (units received at zero cost) |
          dividendo_efectivo (cash income, no units: cantidad may be empty).
- plataforma (optional): broker or exchange. Rows from a platform listed in
  config.json -> `excluded_platforms` are left out of the Lab (out of scope) but remembered, so the
  reconciliation can explain the gap with the portfolio as 'esperada_excluida'.
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

TIPOS = ('apertura', 'compra', 'venta', 'canje', 'dividendo_unidades', 'dividendo_efectivo')
ORIGENES = {'apertura': 'apertura', 'compra': 'compra', 'venta': 'venta',
            'dividendo': 'dividendo_unidades', 'dividendo_efectivo': 'dividendo_efectivo'}
REQUIRED_COLUMNS = ('fecha', 'ticker', 'cantidad', 'precio_unitario_usd', 'monto_usd', 'origen')


def _clean_num(x):
    """Finite float or None (never NaN/inf)."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_number(s):
    """'1234.5', '(1,234.50)', 'US$ 12' -> float. Empty -> None. Garbage -> ValueError.
    Dot is the decimal separator; commas are thousands separators."""
    if s is None:
        return None
    t = re.sub(r'US\$|\$|\s|\xa0', '', str(s))
    if t == '':
        return None
    neg = False
    if t.startswith('(') and t.endswith(')'):
        neg, t = True, t[1:-1]
    if t.startswith('-'):
        neg, t = True, t[1:]
    t = t.replace(',', '')
    v = float(t)  # ValueError on garbage
    if not math.isfinite(v):
        raise ValueError('non-finite number')
    return -v if neg else v


def normalize_symbol(s):
    t = re.sub(r'\s+', '', str(s or '')).upper()
    if t.endswith('-USD'):
        t = t[:-4]
    return t


def _op(fecha, ticker, cantidad, precio, monto, tipo, plataforma, origen, nota=None):
    return {'fecha': fecha, 'ticker': ticker, 'cantidad': _clean_num(cantidad),
            'precio_usd': _clean_num(precio), 'monto_usd': _clean_num(monto), 'tipo': tipo,
            'plataforma': plataforma, 'origen': origen, 'nota': nota}


def _norm_platforms(excluded):
    return {str(p).strip().lower() for p in (excluded or []) if str(p).strip()}


def parse_ledger_text(text, excluded_platforms=()):
    """Parse a ledger CSV.

    Returns {'ops', 'errors': [{'fila', 'error'}], 'excluded': {'plataforma_excluida', 'vacias'},
    'excluded_ops': [{'ticker', 'cantidad', 'plataforma'}]}. A bad row never aborts the rest."""
    if text.startswith('﻿'):
        text = text[1:]
    excluded_set = _norm_platforms(excluded_platforms)
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


def excluded_platforms(config=None):
    try:
        cfg = config if config is not None else load_config()
    except (OSError, ValueError):
        return []
    v = cfg.get('excluded_platforms') or []
    return [str(p) for p in v] if isinstance(v, list) else []


def load_ledger(config=None, ledger_path=None):
    """Load the ledger file. A missing file is reported in `errors`, never raised."""
    try:
        cfg = config if config is not None else load_config()
    except (OSError, ValueError):
        cfg = {}
    path = ledger_path or LEDGER_PATH
    name = os.path.basename(path)
    ops, errors, excluded_ops = [], [], []
    counts = {'excluidas': {'plataforma_excluida': 0, 'vacias': 0}, 'por_archivo': {}}
    if os.path.exists(path):
        with open(path, encoding='utf-8-sig', newline='') as f:
            res = parse_ledger_text(f.read(), excluded_platforms(cfg))
        ops = res['ops']
        excluded_ops = res['excluded_ops']
        counts['por_archivo'][name] = len(ops)
        counts['excluidas'] = res['excluded']
        errors += [dict(e, archivo=name) for e in res['errors']]
    else:
        errors.append({'archivo': name, 'fila': None, 'error': 'file not found'})
        counts['por_archivo'][name] = None
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
