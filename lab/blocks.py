"""Bloques de riesgo del Lab: lector y validador de config/lab/blocks.json. Funciones puras salvo
`load_blocks` (lee JSON). Sin red.

Esquema de blocks.json: `regla_banda` {nombre, absoluta_pp, relativa, fecha_referencia},
`tope_posicion` {aviso_pct, alerta_pct, excepciones [tickers], nota} y `bloques[]` {id, nombre
(texto), tickers, indice_proxy, bandas {referencia_pct}}. La referencia es el peso de una fecha
(modo descriptivo), no un objetivo; la banda la calcula el motor desde la referencia y la regla.
`indice_proxy` es el indice del bloque para las pruebas de estres.

Reglas:
- Las claves cripto valen con o sin sufijo -USD ('SOL' == 'SOL-USD'): se normalizan con
  prices.price_symbol.
- Un ticker en varios bloques vale para el primero (y se informa una advertencia).
- Un ticker sin bloque cae en 'resto' (el bloque comodin, con tickers [], es obligatorio)."""
import json
import math
import os

from . import prices

from .paths import config_file

BLOCKS_PATH = config_file('blocks.json')
REST_ID = 'resto'
MODE = 'descriptivo'
DEFAULT_RULE = {'nombre': '5/25', 'absoluta_pp': 5.0, 'relativa': 0.25, 'fecha_referencia': None}


class BlocksError(ValueError):
    """blocks.json mal formado."""


def _num(v, where, lo=0.0):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < lo:
        raise BlocksError(f'{where}: debe ser un numero >= {lo}')
    return float(v)


def validate_rule(rule):
    """regla_banda -> {'nombre', 'absoluta_pp', 'relativa', 'fecha_referencia'} (defecto 5/25)."""
    if rule is None:
        return dict(DEFAULT_RULE)
    if not isinstance(rule, dict):
        raise BlocksError("'regla_banda' debe ser un objeto")
    return {'nombre': str(rule.get('nombre') or DEFAULT_RULE['nombre']),
            'absoluta_pp': _num(rule.get('absoluta_pp'), 'regla_banda.absoluta_pp'),
            'relativa': _num(rule.get('relativa'), 'regla_banda.relativa'),
            'fecha_referencia': rule.get('fecha_referencia')}


def validate_cap(cap):
    """tope_posicion -> {'aviso_pct', 'alerta_pct', 'excepciones': {simbolo de precio: nota}, 'nota'}."""
    if cap is None:
        return {'aviso_pct': 10.0, 'alerta_pct': 20.0, 'excepciones': {}, 'nota': None}
    if not isinstance(cap, dict):
        raise BlocksError("'tope_posicion' debe ser un objeto")
    aviso = _num(cap.get('aviso_pct'), 'tope_posicion.aviso_pct')
    alerta = _num(cap.get('alerta_pct'), 'tope_posicion.alerta_pct')
    if alerta < aviso:
        raise BlocksError('tope_posicion: alerta_pct debe ser >= aviso_pct')
    exc_raw = cap.get('excepciones') or []
    if not isinstance(exc_raw, list) or not all(isinstance(t, str) and t.strip() for t in exc_raw):
        raise BlocksError('tope_posicion.excepciones debe ser una lista de tickers (texto)')
    nota = cap.get('nota')
    return {'aviso_pct': aviso, 'alerta_pct': alerta, 'nota': nota,
            'excepciones': {prices.price_symbol(t): nota for t in exc_raw}}


def validate_blocks(data):
    """Valida la estructura y devuelve la lista de bloques normalizada. Lanza BlocksError.
    Cada bloque: id, nombre, tickers, indice_proxy (simbolo de precio), referencia_pct (None si no
    la trae), comodin (el bloque 'resto')."""
    if not isinstance(data, dict) or not isinstance(data.get('bloques'), list) or not data['bloques']:
        raise BlocksError("blocks.json debe ser un objeto con una lista 'bloques' no vacia")
    out, seen = [], set()
    for i, b in enumerate(data['bloques']):
        where = f'bloque #{i + 1}'
        if not isinstance(b, dict):
            raise BlocksError(f'{where}: debe ser un objeto')
        bid = b.get('id')
        if not isinstance(bid, str) or not bid.strip():
            raise BlocksError(f"{where}: falta 'id' (texto no vacio)")
        if bid in seen:
            raise BlocksError(f"id de bloque repetido: '{bid}'")
        seen.add(bid)
        where = f"bloque '{bid}'"
        nombre = b.get('nombre')
        if not isinstance(nombre, str) or not nombre.strip():
            raise BlocksError(f"{where}: falta 'nombre'")
        tickers = b.get('tickers')
        if not isinstance(tickers, list) or not all(isinstance(t, str) and t.strip() for t in tickers):
            raise BlocksError(f"{where}: 'tickers' debe ser una lista de textos")
        proxy = b.get('indice_proxy')
        if not isinstance(proxy, str) or not proxy.strip():
            raise BlocksError(f"{where}: falta 'indice_proxy'")
        bandas = b.get('bandas')
        if bandas is not None and not isinstance(bandas, dict):
            raise BlocksError(f"{where}: 'bandas' debe ser null o un objeto")
        ref = (bandas or {}).get('referencia_pct')
        ref = None if ref is None else _num(ref, f"{where}: bandas.referencia_pct")
        out.append({'id': bid, 'nombre': nombre.strip(), 'tickers': [t.strip() for t in tickers],
                    'indice_proxy': prices.price_symbol(proxy), 'referencia_pct': ref,
                    'comodin': bid == REST_ID})
    if REST_ID not in seen:
        raise BlocksError(f"falta el bloque comodin '{REST_ID}'")
    return out


def build_map(blocks):
    """{simbolo de precio: id de bloque} y advertencias por tickers repetidos. Gana el primero."""
    mapping, warnings = {}, []
    for b in blocks:
        for t in b['tickers']:
            sym = prices.price_symbol(t)
            if sym in mapping:
                warnings.append({'tipo': 'ticker_repetido', 'ticker': sym,
                                 'bloques': [mapping[sym], b['id']], 'usado': mapping[sym]})
                continue
            mapping[sym] = b['id']
    return mapping, warnings


def block_of(ticker, mapping):
    """Id del bloque de un ticker (en simbolo de precio o del libro). Sin bloque -> 'resto'."""
    return mapping.get(prices.price_symbol(ticker), REST_ID)


def load_blocks(path=None):
    """Lee y valida el JSON. Devuelve {'blocks', 'map', 'proxies', 'names', 'rule', 'cap', 'meta',
    'warnings'}. Lanza BlocksError si el archivo falta o esta mal formado."""
    path = path or BLOCKS_PATH
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except OSError as e:
        raise BlocksError(f'no se pudo leer blocks.json: {e}')
    except ValueError as e:
        raise BlocksError(f'blocks.json no es JSON valido: {e}')
    blocks = validate_blocks(data)
    mapping, warnings = build_map(blocks)
    rule = validate_rule(data.get('regla_banda'))
    return {'blocks': blocks, 'map': mapping,
            'proxies': {b['id']: b['indice_proxy'] for b in blocks},
            'names': {b['id']: b['nombre'] for b in blocks},
            'rule': rule, 'cap': validate_cap(data.get('tope_posicion')),
            'meta': {'modo': MODE, 'fecha_referencia': rule['fecha_referencia'], 'nota': data.get('nota')},
            'warnings': warnings}
