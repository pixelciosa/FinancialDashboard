"""Semaforo macro (Fase 6): funciones puras. Reglas deterministas de config/lab/macro_rules.json,
SIN IA. Describen el contexto del mercado; no recomiendan operar.

Convenciones (documentadas porque el JSON de reglas no las fija):
- Calendario: union de las fechas de todas las series; cada serie se rellena hacia adelante (ffill)
  hasta MAX_FFILL dias por feriados distintos entre mercados. Mas alla, el dato queda faltante.
  Las ventanas (ventanas.cambio_dias_habiles y ventanas.promedio_movil_dias del JSON) se cuentan en
  filas de ese calendario.
- Cambio = valor(t) - valor(t-N) (tasa, en pp) o (valor(t)/valor(t-N) - 1) * 100 (en %), N = ventana de cambio.
  Promedio movil = media de las W ultimas filas (incluida la de hoy; exige las W), W = ventana del promedio.
- Claves del payload independientes de la ventana: 'promedio_movil' y 'cambio_pct'.
- Bordes: los valores se redondean (6 decimales) antes de comparar, para que 4.55 - 4.30 cuente como
  0,25 exacto y no como 0,2500000000000004.
- Petroleo: si hay un cierre <= 0 en la ventana (t-N..t) el cambio % no esta definido: 'sin_datos'.
  Ratio GLD/SLV: sin cierres <= 0 en ninguno de los dos; si no, el ratio no esta definido.
- Luces crudas: 'verde' | 'ambar' | 'rojo' | 'sin_datos' (gris, no cuenta).

HISTERESIS (por indicador, sobre la luz cruda diaria):
- Estado inicial: el primer dia con dato valido toma la luz cruda de ese dia.
- Una luz cambia a un estado NUEVO solo si la luz cruda de ese estado se repite N dias habiles
  seguidos (N = histeresis.dias_habiles_consecutivos); el cambio ocurre el N-esimo dia. Antes
  conserva el estado anterior.
- Si el crudo vuelve al estado actual, la racha se reinicia. Si el crudo oscila entre dos estados
  distintos del actual (p. ej. actual verde, crudo rojo y luego ambar) la racha se reinicia en 1
  con el nuevo candidato: nada cambia hasta que UN mismo estado complete N dias seguidos.
- Un dia 'sin_datos' se muestra como 'sin_datos' y borra la memoria: cuando vuelven los datos, el
  primer dia valido es de nuevo un estado inicial (toma el crudo).

ETIQUETA DE REGIMEN (sobre las luces con histeresis del dia): cuenta las rojas (las ambar no).
0 o 1 = 'favorable', 2 = 'mixto', 3 o mas = 'adverso'; con menos de `minimo_luces_validas`
luces con dato = 'sin_datos_suficientes'."""
import json
import math
import os
from collections import OrderedDict

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(os.environ.get('LAB_CONFIG_DIR') or os.path.join(ROOT, 'config', 'lab'), 'macro_rules.json')

MAX_FFILL = 5
ROUND_DIGITS = 6
LIGHTS = ('verde', 'ambar', 'rojo', 'sin_datos')
LABELS = ('favorable', 'mixto', 'adverso', 'sin_datos_suficientes')
TIPOS = ('nivel', 'cambio_abs', 'cambio_pct', 'diferencia', 'promedio_cambio')


class MacroRulesError(ValueError):
    """macro_rules.json ausente o invalido."""


# --- Reglas -----------------------------------------------------------------------
def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_rules(data):
    """Valida la forma que necesita el motor y devuelve un dict normalizado."""
    try:
        win = data['ventanas']
        cambio, prom = int(win['cambio_dias_habiles']), int(win['promedio_movil_dias'])
        hys = int(data['histeresis']['dias_habiles_consecutivos'])
        minimo = int(data['sin_datos']['minimo_luces_validas'])
        calib = data['calibracion']
        et = data['etiqueta_regimen']['parametros']
        fav, mix, adv = int(et['favorable_max_rojas']), int(et['mixto_rojas']), int(et['adverso_min_rojas'])
        umbral_rev = float(calib['umbral_revision_pct_rojo'])
        desde = str(calib['desde'])
        pd.Timestamp(desde)
        inds = data['indicadores']
    except (KeyError, TypeError, ValueError) as e:
        raise MacroRulesError(f'macro_rules.json incompleto o con tipos invalidos: {e!r}')
    if min(cambio, prom, hys, minimo) < 1 or not (fav + 1 == mix and mix + 1 == adv):
        raise MacroRulesError('ventanas, histeresis o etiqueta con valores invalidos')
    out, seen = [], set()
    for ind in inds:
        try:
            iid, p = ind['id'], ind['parametros']
            tipo, series = p['tipo'], list(ind['series'])
        except (KeyError, TypeError) as e:
            raise MacroRulesError(f'indicador sin id/parametros/series: {e!r}')
        if iid in seen or tipo not in TIPOS:
            raise MacroRulesError(f"indicador '{iid}' duplicado o con tipo invalido")
        seen.add(iid)
        need = {'nivel': ('ambar_desde', 'rojo_sobre'), 'cambio_abs': ('ambar_desde', 'rojo_sobre'),
                'cambio_pct': ('ambar_desde', 'rojo_sobre'), 'diferencia': ('verde_sobre', 'rojo_bajo'),
                'promedio_cambio': ('rojo_desde_cambio_pct',)}[tipo]
        if not all(_num(p.get(k)) for k in need):
            raise MacroRulesError(f"indicador '{iid}': parametros numericos faltantes {need}")
        if tipo in ('nivel', 'cambio_abs', 'cambio_pct') and not p['ambar_desde'] < p['rojo_sobre']:
            raise MacroRulesError(f"indicador '{iid}': ambar_desde debe ser menor que rojo_sobre")
        if tipo == 'diferencia' and not p['rojo_bajo'] <= p['verde_sobre']:
            raise MacroRulesError(f"indicador '{iid}': rojo_bajo debe ser <= verde_sobre")
        nser = 2 if tipo == 'diferencia' else (1 if tipo != 'promedio_cambio' else len(series))
        if len(series) != nser or (tipo == 'promedio_cambio' and nser not in (1, 2)):
            raise MacroRulesError(f"indicador '{iid}': cantidad de series invalida")
        out.append({'id': iid, 'nombre': ind.get('nombre') or {'es': iid, 'en': iid},
                    'metrica': ind.get('metrica'), 'unidad': p.get('unidad_valor'),
                    'tipo': tipo, 'series': series, 'p': dict(p)})
    if not out:
        raise MacroRulesError('sin indicadores')
    return {'estado': data.get('estado'), 'ventana_cambio': cambio, 'ventana_promedio': prom, 'histeresis': hys,
            'minimo_validas': minimo, 'favorable_max': fav, 'mixto': mix, 'adverso_min': adv,
            'calibracion_desde': desde, 'umbral_revision': umbral_rev, 'indicadores': out}


def load_rules(path=None):
    try:
        with open(path or RULES_PATH, encoding='utf-8') as f:
            return validate_rules(json.load(f))
    except OSError as e:
        raise MacroRulesError(f'No se pudo leer macro_rules.json: {e}')
    except json.JSONDecodeError as e:
        raise MacroRulesError(f'macro_rules.json no es JSON valido: {e}')


def required_symbols(rules):
    out = []
    for ind in rules['indicadores']:
        for s in ind['series']:
            if s not in out:
                out.append(s)
    return out


# --- Series -----------------------------------------------------------------------
def prepare_closes(closes, symbols=None):
    """Cierres crudos -> DataFrame con el calendario union y ffill limitado (MAX_FFILL). Los no
    finitos pasan a NaN; los cierres <= 0 se conservan (petroleo y ratio los detectan). Las columnas
    ausentes se agregan como NaN (luz 'sin_datos')."""
    if closes is None or len(closes) == 0:
        return pd.DataFrame()
    df = closes.copy()
    df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df = df.apply(pd.to_numeric, errors='coerce').replace([float('inf'), float('-inf')], float('nan'))
    for s in symbols or []:
        if s not in df.columns:
            df[s] = float('nan')
    return df.ffill(limit=MAX_FFILL)


def _r(s):
    return s.round(ROUND_DIGITS)


def _pct_change(s, n):
    return _r((s / s.shift(n) - 1.0) * 100.0)


def metric_frame(ind, df, rules):
    """Columnas diarias del indicador: 'valor' (metrica principal) y auxiliares ('promedio_movil',
    'cambio_pct') cuando aplica. NaN = no definido. Las ventanas salen de `rules`."""
    n, w = rules['ventana_cambio'], rules['ventana_promedio']
    tipo, ser = ind['tipo'], ind['series']
    out = pd.DataFrame(index=df.index)
    nan = pd.Series(float('nan'), index=df.index)
    col = lambda s: df[s] if s in df.columns else nan  # noqa: E731
    if tipo == 'nivel':
        out['valor'] = _r(col(ser[0]))
    elif tipo == 'cambio_abs':
        s = col(ser[0])
        out['valor'] = _r(s - s.shift(n))
    elif tipo == 'diferencia':
        out['valor'] = _r(col(ser[0]) - col(ser[1]))
    elif tipo == 'cambio_pct':
        s = col(ser[0])
        # cierre <= 0 en la ventana (t-N..t): cambio % no definido
        all_pos = (s > 0).astype(float).where(s.notna()).rolling(n + 1, min_periods=n + 1).min() == 1.0
        out['valor'] = _pct_change(s, n).where(all_pos)
    else:  # promedio_cambio
        if len(ser) == 2:
            a, b = col(ser[0]), col(ser[1])
            s = (a / b).where((a > 0) & (b > 0))
        else:
            s = col(ser[0])
        s = s.replace([float('inf'), float('-inf')], float('nan'))
        out['valor'] = _r(s)
        out['promedio_movil'] = _r(s.rolling(w, min_periods=w).mean())
        out['cambio_pct'] = _pct_change(s, n)
    return out


def raw_light(ind, row, rules):
    """Luz cruda de un dia. `row` = fila de metric_frame (dict o Series)."""
    p, tipo = ind['p'], ind['tipo']
    v = row['valor']
    if v is None or pd.isna(v):
        return 'sin_datos'
    if tipo in ('nivel', 'cambio_abs', 'cambio_pct'):
        if v > p['rojo_sobre']:
            return 'rojo'
        return 'ambar' if v >= p['ambar_desde'] else 'verde'
    if tipo == 'diferencia':
        if v > p['verde_sobre']:
            return 'verde'
        return 'rojo' if v < p['rojo_bajo'] else 'ambar'
    ma, ch = row['promedio_movil'], row['cambio_pct']
    if pd.isna(ma) or pd.isna(ch):
        return 'sin_datos'
    if v <= ma:
        return 'verde'
    return 'rojo' if ch >= p['rojo_desde_cambio_pct'] else 'ambar'


def apply_hysteresis(raw, dias):
    """Lista de luces crudas -> (luces con histeresis, indices de inicio del estado actual).
    Ver la docstring del modulo para las reglas."""
    shown, since = [], []
    cur = cand = None
    streak = start = 0
    for i, r in enumerate(raw):
        if r == 'sin_datos':
            cur = cand = None
            streak = 0
            since.append(since[-1] if shown and shown[-1] == 'sin_datos' else i)
            shown.append('sin_datos')
            continue
        if cur is None:  # estado inicial: el crudo
            cur, cand, streak, start = r, None, 0, i
        elif r == cur:
            cand, streak = None, 0
        else:
            streak = streak + 1 if r == cand else 1
            cand = r
            if streak >= dias:
                cur, cand, streak, start = r, None, 0, i
        shown.append(cur)
        since.append(start)
    return shown, since


def regime_label(lights, rules):
    """Luces del dia -> {'etiqueta', 'n_rojas', 'n_validas'}."""
    validas = [x for x in lights if x != 'sin_datos']
    rojas = sum(1 for x in validas if x == 'rojo')
    if len(validas) < rules['minimo_validas']:
        et = 'sin_datos_suficientes'
    elif rojas >= rules['adverso_min']:
        et = 'adverso'
    elif rojas == rules['mixto']:
        et = 'mixto'
    else:
        et = 'favorable'
    return {'etiqueta': et, 'n_rojas': rojas, 'n_validas': len(validas)}


def evaluate(closes, rules):
    """Recalcula todo dia por dia (con histeresis sobre TODA la historia disponible).
    Devuelve {'dates': [Timestamp], 'indicadores': {id: {...}}, 'regimen': [dict por dia]}."""
    df = prepare_closes(closes, required_symbols(rules))
    if df.empty:
        return {'dates': [], 'indicadores': OrderedDict(), 'regimen': []}
    dates = list(df.index)
    inds = OrderedDict()
    for ind in rules['indicadores']:
        mf = metric_frame(ind, df, rules)
        recs = mf.to_dict('records')
        raw = [raw_light(ind, r, rules) for r in recs]
        shown, since = apply_hysteresis(raw, rules['histeresis'])
        inds[ind['id']] = {'frame': mf, 'raw': raw, 'luz': shown, 'since': since}
    regimen = [regime_label([inds[i]['luz'][k] for i in inds], rules) for k in range(len(dates))]
    return {'dates': dates, 'indicadores': inds, 'regimen': regimen}


# --- Calibracion --------------------------------------------------------------------
def _runs(flags, dates):
    """Tramos consecutivos de True -> [{'desde', 'hasta', 'dias'}]."""
    out, start = [], None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append({'desde': _d(dates[start]), 'hasta': _d(dates[i - 1]), 'dias': i - start})
            start = None
    return out


def _d(ts):
    return pd.Timestamp(ts).strftime('%Y-%m-%d')


def _pct(n, total):
    return None if not total else round(n * 100.0 / total, 2)


def calibrate(ev, rules):
    """Porcentaje de dias por luz e indicador y distribucion de la etiqueta desde
    `calibracion.desde` hasta el ultimo dato, con histeresis. NO ajusta umbrales."""
    desde = pd.Timestamp(rules['calibracion_desde'])
    idx = [i for i, d in enumerate(ev['dates']) if d >= desde]
    out = {'desde': rules['calibracion_desde'], 'hasta': _d(ev['dates'][-1]) if ev['dates'] else None,
           'dias': len(idx), 'umbral_revision_pct_rojo': rules['umbral_revision'],
           'indicadores': OrderedDict(), 'regimen': {}, 'para_revision': []}
    if not idx:
        return out
    dates = [ev['dates'][i] for i in idx]
    for ind in rules['indicadores']:
        e = ev['indicadores'][ind['id']]
        luces = [e['luz'][i] for i in idx]
        dias = {k: luces.count(k) for k in LIGHTS}
        pct = {k: _pct(dias[k], len(idx)) for k in LIGHTS}
        out['indicadores'][ind['id']] = {'dias': dias, 'pct': pct}
        if dias['rojo'] * 100.0 / len(idx) > rules['umbral_revision']:
            vals = [e['frame']['valor'].iloc[i] for i in idx if e['luz'][i] == 'rojo'
                    and not pd.isna(e['frame']['valor'].iloc[i])]
            ser = pd.Series(vals, dtype=float)
            out['para_revision'].append({
                'id': ind['id'], 'pct_rojo': pct['rojo'], 'dias_rojo': dias['rojo'],
                'periodos_rojo': _runs([x == 'rojo' for x in luces], dates),
                'valor_en_rojo': None if ser.empty else {
                    'min': round(float(ser.min()), 4), 'p25': round(float(ser.quantile(.25)), 4),
                    'mediana': round(float(ser.median()), 4), 'p75': round(float(ser.quantile(.75)), 4),
                    'max': round(float(ser.max()), 4)}})
    labels = [ev['regimen'][i]['etiqueta'] for i in idx]
    dias = {k: labels.count(k) for k in LABELS}
    out['regimen'] = {'dias': dias, 'pct': {k: _pct(dias[k], len(idx)) for k in LABELS}}
    return out


# --- Payload ------------------------------------------------------------------------
def clean(x, nd=4):
    """Numero finito redondeado o None (nunca NaN/Infinity ni 0 inventado)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _series_list(s, idx):
    return [clean(s.iloc[i]) for i in idx]


def build_payload(closes, rules, max_points=None):
    """Payload completo de GET /macro (sin 'ok'/'status'). `closes` = cache cruda (puede ser vacia)."""
    ev = evaluate(closes, rules)
    dates = ev['dates']
    if not dates:
        return None
    last = len(dates) - 1
    desde = pd.Timestamp(rules['calibracion_desde'])
    idx = [i for i, d in enumerate(dates) if d >= desde] or list(range(len(dates)))
    if max_points:
        idx = idx[-max_points:]
    w, n = rules['ventana_promedio'], rules['ventana_cambio']
    indicadores = []
    for ind in rules['indicadores']:
        e = ev['indicadores'][ind['id']]
        mf = e['frame']
        aux = {c: _series_list(mf[c], idx) for c in mf.columns if c != 'valor'}
        indicadores.append({
            'id': ind['id'], 'nombre': ind['nombre'], 'metrica': ind['metrica'], 'unidad': ind['unidad'],
            'series_origen': ind['series'],
            'valor_actual': clean(mf['valor'].iloc[last]),
            'luz': e['luz'][last], 'luz_cruda': e['raw'][last],
            'desde': _d(dates[e['since'][last]]),
            'auxiliar_actual': {c: clean(mf[c].iloc[last]) for c in mf.columns if c != 'valor'},
            'serie': {'fechas': [_d(dates[i]) for i in idx], 'valores': _series_list(mf['valor'], idx),
                      'luces': [e['luz'][i] for i in idx], 'auxiliares': aux}})
    reg = ev['regimen']
    cambios, prev = [], None
    for i in idx:
        if reg[i]['etiqueta'] != prev:
            cambios.append({'desde': _d(dates[i]), 'etiqueta': reg[i]['etiqueta']})
            prev = reg[i]['etiqueta']
    k = last
    while k > 0 and reg[k - 1]['etiqueta'] == reg[last]['etiqueta']:
        k -= 1
    cal = calibrate(ev, rules)
    return {'datos_al': _d(dates[last]),
            'ventanas': {'cambio_dias_habiles': n, 'promedio_movil_dias': w},
            'histeresis_dias': rules['histeresis'],
            'indicadores': indicadores,
            'regimen': dict(reg[last], desde=_d(dates[k]), minimo_luces_validas=rules['minimo_validas'],
                            cambios=cambios),
            'calibracion': cal, 'para_revision': cal['para_revision']}
