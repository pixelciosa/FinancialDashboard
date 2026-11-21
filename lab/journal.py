"""Diario de decisiones del Lab: tesis + condicion de salida, evaluadas con reglas (sin IA).

- Persistencia en data/lab/journal.json con escritura atomica (tmp + os.replace) y lock en proceso.
- La ruta es inyectable: los tests parchean `_journal_path()`; nunca deben escribir en el data/ real.
- Evaluacion pura (`evaluate`): recibe precios, pesos y `today`; faltantes son None, nunca 0
  (salvo tenencia 0 explicita en 'peso'). Sin NaN/Infinity.
- La tesis se guarda tal cual; no se interpreta ni ejecuta."""
import json
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import date, datetime

from . import prices

VERSION = 1
MAX_ENTRIES = 500
MAX_TESIS = 2000
MAX_NOTA = 500
MAX_TICKER = 15
TIPOS = ('precio', 'peso', 'fecha')
OPERADORES = ('>=', '<=')
ESTADOS = ('abierta', 'cerrada')
_TICKER_RE = re.compile(r'^[A-Z0-9][A-Z0-9.\-]{0,14}$')
_LOCK = threading.Lock()


class JournalError(Exception):
    """Archivo corrupto o ilegible: no se sobrescribe."""


class ValidationError(ValueError):
    def __init__(self, msg, campo=None):
        super().__init__(msg)
        self.campo = campo


class LimitError(ValidationError):
    pass


def _journal_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'data', 'lab', 'journal.json')


def _now():
    return datetime.now().replace(microsecond=0).isoformat()


# --- Persistencia -------------------------------------------------------------------
def load_entries():
    """Lista de entradas. Archivo ausente -> []. Corrupto -> JournalError (sin tocarlo)."""
    path = _journal_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise JournalError(f'journal.json ilegible o corrupto ({e.__class__.__name__}). '
                           'El archivo se conserva sin cambios; reparalo o movelo a mano.')
    if not isinstance(data, dict) or not isinstance(data.get('entries'), list) \
            or not all(isinstance(e, dict) and isinstance(e.get('id'), str) for e in data['entries']):
        raise JournalError('journal.json tiene una estructura invalida. El archivo se conserva sin cambios.')
    return data['entries']


def _write_entries(entries):
    path = _journal_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.journal.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'version': VERSION, 'entries': entries}, f, ensure_ascii=False, indent=2,
                      allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- Validacion ---------------------------------------------------------------------
def _num(x, campo):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValidationError('valor debe ser un numero finito', campo)
    return float(x)


def _iso_date(s, campo):
    if not isinstance(s, str) or len(s) != 10:
        raise ValidationError('fecha invalida, usa AAAA-MM-DD', campo)
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValidationError('fecha invalida, usa AAAA-MM-DD', campo)


def normalize_ticker(raw):
    if not isinstance(raw, str):
        raise ValidationError('ticker debe ser texto', 'ticker')
    t = raw.strip().upper()
    if not t or len(t) > MAX_TICKER or not _TICKER_RE.match(t):
        raise ValidationError(f'ticker invalido (1 a {MAX_TICKER} caracteres: letras, numeros, . o -)', 'ticker')
    return prices.price_symbol(t)


def validate_tesis(v):
    if not isinstance(v, str) or not v.strip():
        raise ValidationError('tesis es obligatoria (texto)', 'tesis')
    if len(v) > MAX_TESIS:
        raise ValidationError(f'tesis supera {MAX_TESIS} caracteres', 'tesis')
    return v


def validate_condicion(c):
    if not isinstance(c, dict):
        raise ValidationError('condicion_salida debe ser un objeto', 'condicion_salida')
    extra = set(c) - {'tipo', 'operador', 'valor'}
    if extra:
        raise ValidationError(f'campos desconocidos: {sorted(extra)}', 'condicion_salida')
    tipo = c.get('tipo')
    if tipo not in TIPOS:
        raise ValidationError(f'tipo debe ser uno de {list(TIPOS)}', 'condicion_salida.tipo')
    if tipo == 'fecha':
        if c.get('operador') is not None:
            raise ValidationError("operador no aplica a 'fecha' (es implicito: hoy >= fecha)",
                                  'condicion_salida.operador')
        _iso_date(c.get('valor'), 'condicion_salida.valor')
        return {'tipo': 'fecha', 'operador': None, 'valor': c['valor']}
    if c.get('operador') not in OPERADORES:
        raise ValidationError(f'operador debe ser uno de {list(OPERADORES)}', 'condicion_salida.operador')
    v = _num(c.get('valor'), 'condicion_salida.valor')
    if tipo == 'precio' and v <= 0:
        raise ValidationError('el precio debe ser mayor que 0', 'condicion_salida.valor')
    if tipo == 'peso' and not 0 <= v <= 100:
        raise ValidationError('el peso debe estar entre 0 y 100 (%)', 'condicion_salida.valor')
    return {'tipo': tipo, 'operador': c['operador'], 'valor': v}


def validate_estado(v):
    if v not in ESTADOS:
        raise ValidationError(f'estado debe ser uno de {list(ESTADOS)}', 'estado')
    return v


def validate_nota(v):
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValidationError('nota_cierre debe ser texto', 'nota_cierre')
    if len(v) > MAX_NOTA:
        raise ValidationError(f'nota_cierre supera {MAX_NOTA} caracteres', 'nota_cierre')
    return v


CREATE_FIELDS = {'ticker', 'tesis', 'condicion_salida', 'estado', 'nota_cierre'}
EDIT_FIELDS = {'tesis', 'condicion_salida', 'estado', 'nota_cierre'}


def _check_body(body, allowed):
    if not isinstance(body, dict):
        raise ValidationError('el cuerpo debe ser un objeto JSON', None)
    for k in body:
        if k not in allowed:
            raise ValidationError(f"campo no permitido: '{k}'", k)


def create_entry(body):
    _check_body(body, CREATE_FIELDS)
    for k in ('ticker', 'tesis', 'condicion_salida'):
        if k not in body:
            raise ValidationError(f'falta {k}', k)
    now = _now()
    entry = {'id': str(uuid.uuid4()), 'fecha_alta': now,
             'ticker': normalize_ticker(body['ticker']),
             'tesis': validate_tesis(body['tesis']),
             'condicion_salida': validate_condicion(body['condicion_salida']),
             'estado': validate_estado(body.get('estado', 'abierta')),
             'nota_cierre': validate_nota(body.get('nota_cierre')),
             'actualizado': now}
    with _LOCK:
        entries = load_entries()
        if len(entries) >= MAX_ENTRIES:
            raise LimitError(f'limite de {MAX_ENTRIES} entradas alcanzado', None)
        entries.append(entry)
        _write_entries(entries)
    return entry


def update_entry(entry_id, body):
    """Edita una entrada. Devuelve la entrada o None si no existe."""
    _check_body(body, EDIT_FIELDS)
    with _LOCK:
        entries = load_entries()
        for e in entries:
            if e['id'] == entry_id:
                new = dict(e)
                if 'tesis' in body:
                    new['tesis'] = validate_tesis(body['tesis'])
                if 'condicion_salida' in body:
                    new['condicion_salida'] = validate_condicion(body['condicion_salida'])
                if 'estado' in body:
                    new['estado'] = validate_estado(body['estado'])
                if 'nota_cierre' in body:
                    new['nota_cierre'] = validate_nota(body['nota_cierre'])
                new['actualizado'] = _now()
                e.clear()
                e.update(new)
                _write_entries(entries)
                return new
    return None


# --- Evaluacion (pura) --------------------------------------------------------------
def _clean(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _cmp(actual, op, valor):
    return actual >= valor if op == '>=' else actual <= valor


def _ev(estado, valor_actual, detalle, datos_al):
    return {'estado': estado, 'valor_actual': valor_actual, 'detalle': detalle, 'datos_al': datos_al}


def evaluate(entry, last_prices, weights, today, datos_al, sin_precio=frozenset()):
    """Evalua una entrada. last_prices: {simbolo: cierre|None} o None si no hay precios;
    weights: {simbolo: peso en %} o None si no se pudo calcular la cartera; today: date.
    sin_precio: simbolos con tenencia pero sin precio (peso desconocido, no cero).
    Entradas cerradas -> None."""
    if entry.get('estado') != 'abierta':
        return None
    c, sym = entry['condicion_salida'], prices.price_symbol(entry['ticker'])
    tipo, op, val = c['tipo'], c.get('operador'), c['valor']
    if tipo == 'fecha':
        hoy = today.isoformat()
        ok = today >= date.fromisoformat(val)
        return _ev('cumplida' if ok else 'no_cumplida', hoy,
                   'fecha_alcanzada' if ok else 'fecha_pendiente', hoy)
    if tipo == 'precio':
        if not last_prices:
            return _ev('sin_dato', None, 'sin_precios', None)
        p = _clean(last_prices.get(sym))
        if p is None:
            return _ev('sin_dato', None, 'sin_precio_ticker', datos_al)
        p = round(p, 4)
        ok = _cmp(p, op, val)
        return _ev('cumplida' if ok else 'no_cumplida', p,
                   'precio_cumple' if ok else 'precio_no_cumple', datos_al)
    # peso
    if weights is None:
        return _ev('sin_dato', None, 'sin_precios', None)
    if sym in (sin_precio or ()):
        return _ev('sin_dato', None, 'sin_precio_ticker', datos_al)
    if sym not in weights:
        ok = _cmp(0.0, op, val)
        return _ev('cumplida' if ok else 'no_cumplida', 0.0, 'tenencia_cero', datos_al)
    w = _clean(weights[sym])
    if w is None:
        return _ev('sin_dato', None, 'sin_precio_ticker', datos_al)
    w = round(w, 4)
    ok = _cmp(w, op, val)
    return _ev('cumplida' if ok else 'no_cumplida', w, 'peso_cumple' if ok else 'peso_no_cumple', datos_al)


def summarize(entries_with_eval):
    abiertas = [e for e in entries_with_eval if e['estado'] == 'abierta']
    return {'total': len(entries_with_eval), 'abiertas': len(abiertas),
            'cerradas': len(entries_with_eval) - len(abiertas),
            'cumplidas': sum(1 for e in abiertas if (e['evaluacion'] or {}).get('estado') == 'cumplida'),
            'sin_dato': sum(1 for e in abiertas if (e['evaluacion'] or {}).get('estado') == 'sin_dato')}
