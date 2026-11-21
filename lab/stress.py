"""Pruebas de estres del Lab: escenarios historicos aplicados a los pesos ACTUALES.
Funciones puras (DataFrames/listas/dicts in, datos out), sin red ni IA ni recomendaciones.
Solo `load_scenarios` lee disco.

Convenciones:
- Pesos actuales = valor de cada posicion al ultimo cierre / valor total (solo activos
  invertidos; tenencias del motor: engine.portfolio_series). Los activos sin precio no pesan
  (el motor ya los informa como 'sin_precio').
- Retorno del activo en la ventana = cierre(hasta) / cierre(desde) - 1, con el ultimo cierre
  en o antes de cada borde (ffill) y a lo sumo MAX_STALE_DAYS dias de antiguedad.
- Si el activo no tiene dato en alguno de los bordes (no existia), se usa el retorno del
  `indice_proxy` de su bloque (fuente_retorno 'proxy'). Sin dato ni de proxy: retorno None,
  la posicion va a `sin_dato` y el impacto se calcula solo sobre lo valuable (`cobertura_pct`).
- Faltantes son None, nunca 0. Nada de NaN/Infinity (`_clean`).
- impacto_pct = suma(peso x retorno) sobre lo valuado, con pesos sobre la cartera completa;
  `impacto_pct_sobre_valuado` lo reescala por la cobertura (coinciden con cobertura 100%).
- Peor/mejor posicion: por aporte (peso x retorno) a la cartera."""
import json
import math
import os
from datetime import datetime

import pandas as pd

from . import blocks as blocks_mod
from . import prices

from .paths import config_file

SCENARIOS_PATH = config_file('scenarios.json')
LAST = 'ultimo'
MAX_STALE_DAYS = 7   # un cierre mas viejo que esto no cuenta como dato del borde


class ScenariosError(ValueError):
    """scenarios.json mal formado."""


def _clean(x):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pct(x, nd=4):
    x = _clean(x)
    return None if x is None else round(x * 100, nd)


def _usd(x):
    x = _clean(x)
    return None if x is None else round(x, 2)


# --- Escenarios -------------------------------------------------------------------
def _parse_date(s, where):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise ScenariosError(f"{where}: fecha invalida {s!r} (formato AAAA-MM-DD)")


def validate_scenarios(data):
    """Valida y devuelve la lista de escenarios. Lanza ScenariosError."""
    if not isinstance(data, dict) or not isinstance(data.get('escenarios'), list) or not data['escenarios']:
        raise ScenariosError("scenarios.json debe ser un objeto con una lista 'escenarios' no vacia")
    out, seen = [], set()
    for i, s in enumerate(data['escenarios']):
        if not isinstance(s, dict):
            raise ScenariosError(f'escenario #{i + 1}: debe ser un objeto')
        sid = s.get('id')
        if not isinstance(sid, str) or not sid.strip():
            raise ScenariosError(f"escenario #{i + 1}: falta 'id'")
        if sid in seen:
            raise ScenariosError(f"id de escenario repetido: '{sid}'")
        seen.add(sid)
        where = f"escenario '{sid}'"
        for k in ('nombre', 'descripcion', 'nota'):
            if not isinstance(s.get(k), str) or not s[k].strip():
                raise ScenariosError(f"{where}: falta '{k}'")
        d0 = _parse_date(s.get('desde'), where)
        if s.get('hasta') != LAST:
            d1 = _parse_date(s.get('hasta'), where)
            if d1 <= d0:
                raise ScenariosError(f"{where}: 'hasta' debe ser posterior a 'desde'")
        out.append({k: s[k] for k in ('id', 'nombre', 'descripcion', 'nota', 'desde', 'hasta')})
    return out


def load_scenarios(path=None):
    path = path or SCENARIOS_PATH
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except OSError as e:
        raise ScenariosError(f'no se pudo leer scenarios.json: {e}')
    except ValueError as e:
        raise ScenariosError(f'scenarios.json no es JSON valido: {e}')
    return validate_scenarios(data)


def resolve_hasta(scn, datos_al):
    """'ultimo' -> ultima fecha con datos (datos_al); si no, la fecha del escenario."""
    return datos_al if scn['hasta'] == LAST else scn['hasta']


# --- Retornos ---------------------------------------------------------------------
def close_on_or_before(col, fecha):
    """(cierre, fecha del cierre) del ultimo dato en o antes de `fecha` ('AAAA-MM-DD'), o
    (None, None) si no hay o es mas viejo que MAX_STALE_DAYS."""
    if col is None:
        return None, None
    ts = pd.Timestamp(fecha)
    s = col.dropna()
    s = s[s.index <= ts]
    if s.empty:
        return None, None
    d, v = s.index[-1], _clean(s.iloc[-1])
    if v is None or v <= 0 or (ts - d).days > MAX_STALE_DAYS:
        return None, None
    return v, d.strftime('%Y-%m-%d')


def window_return(closes, symbol, desde, hasta):
    """Retorno simple de `symbol` entre dos fechas, o None si falta dato en algun borde."""
    if closes is None or symbol not in closes.columns:
        return None
    a, _ = close_on_or_before(closes[symbol], desde)
    b, _ = close_on_or_before(closes[symbol], hasta)
    if a is None or b is None:
        return None
    return _clean(b / a - 1)


# --- Pesos actuales ---------------------------------------------------------------
def current_weights(holdings, last_prices):
    """Posiciones con valor al ultimo cierre y peso sobre el total.

    holdings: {ticker: cantidad} (engine.portfolio_series()['holdings_end']);
    last_prices: {ticker: ultimo cierre | None}. Devuelve (posiciones, valor_total,
    sin_precio); posiciones ordenadas por valor desc con {ticker, cantidad, valor, peso}."""
    rows, sin_precio = [], []
    for t, q in holdings.items():
        p = _clean(last_prices.get(t))
        if p is None or p <= 0 or q is None or q <= 0:
            if p is None:
                sin_precio.append(t)
            continue
        rows.append({'ticker': t, 'cantidad': q, 'valor': q * p})
    total = sum(r['valor'] for r in rows)
    for r in rows:
        r['peso'] = r['valor'] / total if total > 0 else None
    rows.sort(key=lambda r: (-r['valor'], r['ticker']))
    return rows, (total if total > 0 else None), sorted(sin_precio)


def last_prices_from(prices_aligned):
    """{ticker: ultimo valor} de prices.align()/load_aligned() (listas con ffill)."""
    return {t: _clean(col[-1]) if col else None for t, col in prices_aligned.items()}


def weights_payload(positions, mapping, names, block_order):
    """Pesos actuales para la respuesta: por posicion y por bloque (pesos en %)."""
    pos = [{'ticker': p['ticker'], 'bloque': blocks_mod.block_of(p['ticker'], mapping),
            'cantidad': _clean(p['cantidad']), 'valor_usd': _usd(p['valor']), 'peso_pct': _pct(p['peso'])}
           for p in positions]
    agg = {}
    for p in positions:
        bid = blocks_mod.block_of(p['ticker'], mapping)
        a = agg.setdefault(bid, {'peso': 0.0, 'valor': 0.0})
        a['peso'] += p['peso'] or 0.0
        a['valor'] += p['valor']
    blk = [{'id': b, 'nombre': names.get(b, b), 'valor_usd': _usd(agg[b]['valor']),
            'peso_pct': _pct(agg[b]['peso'])}
           for b in list(block_order) + sorted(set(agg) - set(block_order)) if b in agg]
    return pos, blk


# --- Un escenario -----------------------------------------------------------------
def position_returns(positions, closes, desde, hasta, mapping, proxies):
    """Retorno de cada posicion en la ventana, con proxy cuando el activo no tiene dato."""
    out = []
    for p in positions:
        t = p['ticker']
        bid = blocks_mod.block_of(t, mapping)
        r = window_return(closes, t, desde, hasta)
        fuente, proxy = ('activo' if r is not None else None), None
        if r is None:
            cand = proxies.get(bid)
            r = window_return(closes, cand, desde, hasta) if cand else None
            if r is not None:
                fuente, proxy = 'proxy', cand
        peso = p['peso']
        out.append({'ticker': t, 'bloque': bid, 'valor': p['valor'], 'peso': peso, 'retorno': r,
                    'fuente_retorno': fuente, 'indice_proxy': proxy,
                    'aporte': peso * r if (r is not None and peso is not None) else None,
                    'impacto_usd': p['valor'] * r if r is not None else None})
    return out


def block_breakdown(rows, names, block_order):
    """Desglose por bloque (solo bloques con peso). aporte = suma peso x retorno valuado."""
    agg = {}
    for r in rows:
        a = agg.setdefault(r['bloque'], {'peso': 0.0, 'peso_valuado': 0.0, 'aporte': 0.0,
                                         'impacto_usd': 0.0, 'n': 0, 'sin_dato': [], 'n_valuadas': 0})
        a['peso'] += r['peso'] or 0.0
        a['n'] += 1
        if r['retorno'] is None:
            a['sin_dato'].append(r['ticker'])
        else:
            a['peso_valuado'] += r['peso']
            a['aporte'] += r['aporte']
            a['impacto_usd'] += r['impacto_usd']
            a['n_valuadas'] += 1
    out = []
    for bid in list(block_order) + sorted(set(agg) - set(block_order)):
        a = agg.get(bid)
        if not a:
            continue
        out.append({'id': bid, 'nombre': names.get(bid, bid), 'posiciones': a['n'],
                    'peso_pct': _pct(a['peso']),
                    'cobertura_pct': _pct(a['peso_valuado'] / a['peso']) if a['peso'] > 0 else None,
                    'aporte_pct': _pct(a['aporte']) if a['n_valuadas'] else None,
                    'retorno_pct': _pct(a['aporte'] / a['peso_valuado'])
                    if a['n_valuadas'] and a['peso_valuado'] > 0 else None,
                    'impacto_usd': _usd(a['impacto_usd']) if a['n_valuadas'] else None,
                    'sin_dato': sorted(a['sin_dato'])})
    return out


def _row_out(r):
    return {'ticker': r['ticker'], 'bloque': r['bloque'], 'peso_pct': _pct(r['peso']),
            'valor_usd': _usd(r['valor']), 'retorno_pct': _pct(r['retorno']),
            'fuente_retorno': r['fuente_retorno'], 'indice_proxy': r['indice_proxy'],
            'aporte_pct': _pct(r['aporte']), 'impacto_usd': _usd(r['impacto_usd'])}


def run_scenario(scn, positions, closes, mapping, proxies, names, block_order, datos_al,
                 calendar=prices.CALENDAR_TICKER):
    """Aplica un escenario a las posiciones. Devuelve un dict serializable (sin NaN)."""
    desde, hasta = scn['desde'], resolve_hasta(scn, datos_al)
    base = {'id': scn['id'], 'nombre': scn['nombre'], 'descripcion': scn['descripcion'],
            'nota': scn['nota'],
            'ventana': {'desde': desde, 'hasta': hasta, 'cierre_desde': None, 'cierre_hasta': None}}
    spy = closes[calendar] if closes is not None and calendar in closes.columns else None
    _, d_a = close_on_or_before(spy, desde)
    _, d_b = close_on_or_before(spy, hasta)
    base['ventana'].update(cierre_desde=d_a, cierre_hasta=d_b)
    if d_a is None or d_b is None:
        return dict(base, status='sin_precios_estres',
                    mensaje=f'No hay precios de {calendar} en la cache de estres para la ventana '
                            f'{desde} a {hasta}. Actualiza la cache con `python -m lab.stress_prices`.',
                    impacto_pct=None, impacto_usd=None, cobertura_pct=None, posiciones=[],
                    sin_dato=[], peor=None, mejor=None, bloques=[], advertencias=[])
    rows = position_returns(positions, closes, desde, hasta, mapping, proxies)
    valued = [r for r in rows if r['retorno'] is not None]
    cobertura = sum(r['peso'] for r in valued)
    aporte = sum(r['aporte'] for r in valued)
    impacto_usd = sum(r['impacto_usd'] for r in valued)
    sin_dato = sorted(r['ticker'] for r in rows if r['retorno'] is None)
    by_aporte = sorted(valued, key=lambda r: (r['aporte'], r['ticker']))
    adv = []
    proxied = sorted((r['ticker'], r['indice_proxy']) for r in rows if r['fuente_retorno'] == 'proxy')
    if proxied:
        adv.append({'tipo': 'proxy_usado', 'detalle': [{'ticker': t, 'indice_proxy': p} for t, p in proxied],
                    'mensaje': 'Activos sin cierre en el borde de la ventana: se uso el indice de su bloque.'})
    if sin_dato:
        adv.append({'tipo': 'sin_dato', 'tickers': sin_dato,
                    'mensaje': 'Sin dato ni del activo ni de su indice proxy: no entran en el impacto.'})
    if d_a != desde or d_b != hasta:
        adv.append({'tipo': 'cierre_distinto_de_borde', 'cierre_desde': d_a, 'cierre_hasta': d_b,
                    'mensaje': 'Un borde cae en dia sin mercado: se uso el ultimo cierre anterior (SPY).'})
    return dict(base, status='ok', mensaje=None,
                impacto_pct=_pct(aporte) if valued else None,
                impacto_usd=_usd(impacto_usd) if valued else None,
                impacto_pct_sobre_valuado=_pct(aporte / cobertura) if valued and cobertura > 0 else None,
                cobertura_pct=_pct(cobertura),
                posiciones=[_row_out(r) for r in sorted(rows, key=lambda r: (-r['valor'], r['ticker']))],
                sin_dato=sin_dato,
                peor=_row_out(by_aporte[0]) if valued else None,
                mejor=_row_out(by_aporte[-1]) if valued else None,
                bloques=block_breakdown(rows, names, block_order), advertencias=adv)


def run_all(scenarios, positions, closes, block_cfg, datos_al, only=None):
    """Corre los escenarios (o solo `only`, una lista de ids). `block_cfg` = blocks.load_blocks()."""
    order = [b['id'] for b in block_cfg['blocks']]
    return [run_scenario(s, positions, closes, block_cfg['map'], block_cfg['proxies'],
                         block_cfg['names'], order, datos_al)
            for s in scenarios if only is None or s['id'] in only]
