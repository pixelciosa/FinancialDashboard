"""Atribucion del Lab (Fase 5): de donde salio el resultado en dolares. Funciones puras, sin red.

Entrada: el resultado de `engine.portfolio_series` (usa sus `events`, `ticker_prices`, `values`
y `flows`; NO reimplementa las convenciones del motor: ventas recortadas, dia no habil, canje en
dos patas, dividendo en unidades a costo cero).

Periodo [desde, hasta] (dias habiles del calendario): V(desde) es el cierre de ese dia (incluye
lo operado ese dia) y los flujos que cuentan son los de los dias posteriores a `desde` hasta
`hasta` inclusive, igual que `net_flows` del motor.

1) POR POSICION.  resultado_i = V_i(hasta) - V_i(desde) - F_i.   sum(resultado_i) = V(hasta) -
   V(desde) - sum(F) = resultado en dolares de la cartera. Aporte en puntos porcentuales por
   Dietz modificado: resultado_i / (V(desde) + sum(w_t F_t)), con w_t = dias de calendario que
   quedan del periodo tras el flujo / dias de calendario del periodo. sum(aporte_i) = Dietz
   modificado de la cartera. NO es el retorno ponderado por tiempo de /performance.

2) POR OPERACION (lotes FIFO por ticker, historia completa).  Abren lote: compra, pata de compra
   de un canje, dividendo en unidades (costo 0) y apertura. Cada venta (y pata de venta de canje)
   consume lotes en orden FIFO. Se devuelve un tramo por cada pedazo de lote:
   - vendido en el periodo: realizado = cantidad x (precio de venta - costo del lote);
   - todavia en cartera en `hasta`: no realizado = cantidad x (cierre(hasta) - costo del lote).
   Costo del lote = precio de ejecucion si el lote se abrio DENTRO del periodo. Los lotes que ya
   existian en `desde` (abiertos hasta ese dia inclusive, aperturas incluidas) se valoran al
   cierre de `desde` (el costo original queda en `precio_ejecucion`). Una apertura posterior a
   `desde` entra con costo 0, como el motor (flujo 0).
   Con esas reglas, por ticker: sum(tramos) = V_i(hasta) - V_i(desde) - F_i, es decir la suma por
   operacion reconcilia con la suma por posicion del mismo rango.

Los dividendos en efectivo no son flujo ni valor en el motor y quedan fuera.
Faltantes son None, nunca 0. Nada de NaN/inf (`_clean`)."""
import math
from collections import deque
from datetime import datetime

from .engine import _clean

TOL_USD = 0.5          # tolerancia de reconciliacion y umbral para "total ~ 0"
EPS_QTY = 1e-9

METODO = 'Dietz modificado'
NOTA = ('Aporte por posicion = resultado en USD de la posicion / (valor inicial + flujos ponderados '
        'por el tiempo que quedaba del periodo tras cada flujo, en dias de calendario). La suma de los '
        'aportes es el rendimiento Dietz modificado de la cartera. No es el retorno ponderado por '
        'tiempo de /performance: puede diferir, sobre todo si hubo aportes grandes o la cartera '
        'subio y bajo dentro del periodo. % del resultado total = resultado de la posicion / '
        'resultado total de la cartera (con signo: si el total es negativo, una posicion que perdio '
        'tiene un porcentaje positivo); es null si |total| < 0,5 USD.')
METODO_OPERACIONES = 'Lotes FIFO'
NOTA_OPERACIONES = ('Cada compra, dividendo en unidades (costo 0), pata de compra de un canje y apertura '
                    'abre un lote; las ventas y patas de venta consumen lotes FIFO por ticker. Un tramo '
                    'vendido es resultado realizado; uno que sigue en cartera es no realizado, '
                    'valuado al cierre del ultimo dia del rango. Los lotes que ya existian al inicio '
                    'del rango se valoran al cierre de ese dia (su costo original figura en '
                    '`precio_ejecucion`); una apertura posterior al inicio entra con costo 0.')


def parse_date(s, nombre='fecha'):
    """'YYYY-MM-DD' -> misma cadena normalizada. ValueError legible si no es una fecha valida."""
    try:
        return datetime.strptime(str(s).strip(), '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        raise ValueError(f"'{nombre}' debe tener formato YYYY-MM-DD (recibido: {s!r})") from None


def resolve_range(dates, desde=None, hasta=None):
    """(k_desde, k_hasta) sobre `dates` (ordenadas). Recorta al calendario: desde = primer dia
    habil >= desde; hasta = ultimo dia habil <= hasta. ValueError si el rango es invalido."""
    if not dates:
        raise ValueError('no hay calendario de precios')
    d = parse_date(desde, 'from') if desde else None
    h = parse_date(hasta, 'to') if hasta else None
    if d and h and d > h:
        raise ValueError(f"'from' ({d}) es posterior a 'to' ({h})")
    if d and d > dates[-1]:
        raise ValueError(f"'from' ({d}) es posterior al ultimo dia disponible ({dates[-1]})")
    if h and h < dates[0]:
        raise ValueError(f"'to' ({h}) es anterior al primer dia disponible ({dates[0]})")
    kf = 0
    if d:
        kf = next(k for k, x in enumerate(dates) if x >= d)
    kt = len(dates) - 1
    if h:
        kt = max(k for k, x in enumerate(dates) if x <= h)
    if kf >= kt:
        raise ValueError('el rango debe abarcar al menos dos dias habiles del calendario '
                         f'(disponible: {dates[0]} a {dates[-1]})')
    return kf, kt


def _days(a, b):
    return (datetime.strptime(b, '%Y-%m-%d') - datetime.strptime(a, '%Y-%m-%d')).days


def _r(x, nd=2):
    x = _clean(x)
    return None if x is None else round(x, nd)


def _position_rows(events, tp, kf, kt, dates):
    """Una fila por ticker con V_i(desde), F_i, V_i(hasta). Devuelve (filas, flujos [(k, F)], sin_precio)."""
    by_t = {}
    for e in events:
        by_t.setdefault(e['ticker'], []).append(e)
    rows, flows, no_price = {}, [], set()
    for t, evs in by_t.items():
        h_f = sum(e['q'] for e in evs if e['k'] <= kf)
        h_t = sum(e['q'] for e in evs if e['k'] <= kt)
        f = sum(e['flow'] for e in evs if kf < e['k'] <= kt)
        for e in evs:
            if kf < e['k'] <= kt and e['flow']:
                flows.append((e['k'], e['flow']))

        def val(h, k):
            if abs(h) < EPS_QTY:
                return 0.0
            p = tp.get(t, {}).get(k)
            if p is None:
                no_price.add(t)
                return None          # faltante: None, nunca 0
            return h * p
        v0, v1 = val(h_f, kf), val(h_t, kt)
        res = None if v0 is None or v1 is None else v1 - v0 - f
        rows[t] = {'ticker': t, 'valor_inicial': v0, 'flujo_neto': f, 'valor_final': v1,
                   'resultado_usd': res,
                   # lo que el motor suma para este ticker (sin precio = valor 0), solo para reconciliar
                   '_motor': (v1 or 0.0) - (v0 or 0.0) - f}
    return rows, flows, no_price


def _lot_rows(events, tp, kf, kt, dates):
    """Tramos FIFO. Devuelve (filas, advertencias_sin_costo)."""
    lots, out, no_cost = {}, [], set()

    def price_at(t, k):
        return tp.get(t, {}).get(k)

    def cost_of(lot):
        """Costo unitario usado en el periodo (None si falta el precio)."""
        if lot['k'] <= kf:
            return price_at(lot['ticker'], kf)
        return lot['exec'] if lot['exec'] is not None else 0.0

    def row(lot, q, ref, fecha_ref, dias_to, estado, venta_fecha=None):
        c = cost_of(lot)
        res = None
        if c is None or ref is None:
            no_cost.add(lot['ticker'])
        else:
            res = q * (ref - c)
        return {'fecha': lot['fecha'], 'ticker': lot['ticker'], 'tipo': lot['tipo'], 'nota': lot['nota'],
                'estado': estado, 'cantidad': _r(q, 8), 'cantidad_lote': _r(lot['q0'], 8),
                'precio_ejecucion': _r(lot['exec'], 6), 'costo_unitario_usd': _r(c, 6),
                'costo_lote_usd': None if c is None else _r(q * c), 'valuado_al_inicio': lot['k'] <= kf,
                'precio_referencia': _r(ref, 6), 'fecha_referencia': fecha_ref,
                'resultado_usd': None if res is None else _r(res), '_res': res,
                'dias_en_cartera': _days(lot['fecha'], fecha_ref), 'fecha_venta': venta_fecha}

    for e in events:
        if e['k'] > kt:
            break
        t, q = e['ticker'], e['q']
        if q > EPS_QTY:
            if e['tipo'] == 'dividendo_unidades':
                ex = 0.0
            elif e['tipo'] == 'apertura':
                ex = None
            else:
                ex = e['flow'] / q
            lots.setdefault(t, deque()).append({'ticker': t, 'fecha': e['fecha'], 'k': e['k'], 'tipo': e['tipo'],
                                                'nota': e['nota'], 'q0': q, 'rem': q, 'exec': ex})
        elif q < -EPS_QTY:
            sell_px = (-e['flow']) / (-q)
            need = -q
            dq = lots.setdefault(t, deque())
            while need > EPS_QTY and dq:
                lot = dq[0]
                take = min(lot['rem'], need)
                if e['k'] > kf:
                    out.append(row(lot, take, sell_px, e['fecha'], None, 'realizado', venta_fecha=e['fecha']))
                lot['rem'] -= take
                need -= take
                if lot['rem'] <= EPS_QTY:
                    dq.popleft()
    for t, dq in lots.items():
        for lot in dq:
            if lot['rem'] > EPS_QTY:
                out.append(row(lot, lot['rem'], price_at(t, kt), dates[kt], None, 'no_realizado'))
    out.sort(key=lambda r: (r['fecha'], r['ticker'], r['estado']))
    return out, no_cost


def attribute(port, desde=None, hasta=None):
    """Atribucion de `port` (salida de engine.portfolio_series) en [desde, hasta].
    None si la cartera no tiene datos. ValueError (mensaje legible) si el rango es invalido."""
    dates = port['dates']
    if not dates:
        return None
    start = port['start_idx']
    kf, kt = resolve_range(dates, desde, hasta)
    events = [dict(e, k=e['i'] - start) for e in port['events']]
    tp = {t: {i - start: p for i, p in d.items()} for t, d in port['ticker_prices'].items()}

    pos, flows, no_price = _position_rows(events, tp, kf, kt, dates)
    lotes, no_cost = _lot_rows(events, tp, kf, kt, dates)

    # Cartera, desde las series del motor (control independiente de los desgloses)
    v_from, v_to = port['values'][kf], port['values'][kt]
    f_net = sum(port['flows'][kf + 1:kt + 1])
    total = v_to - v_from - f_net

    # Dietz modificado
    span = _days(dates[kf], dates[kt])
    base = v_from + sum(f * (_days(dates[k], dates[kt]) / span) for k, f in flows)
    dietz = (total / base) if base > 0 else None

    # Tickers sin precio: fuera de la reconciliacion (no se tratan como 0 en silencio)
    excluidos = sorted(no_price | no_cost)
    sum_ops_by_t = {}
    for r in lotes:
        if r['ticker'] not in excluidos and r['_res'] is not None:
            sum_ops_by_t[r['ticker']] = sum_ops_by_t.get(r['ticker'], 0.0) + r['_res']
        del r['_res']
    sum_pos = sum(p['resultado_usd'] for t, p in pos.items() if t not in excluidos)
    sum_ops = sum(sum_ops_by_t.values())
    # resultado de la cartera restringido a lo valuable: se descarta lo que el motor sumo de los excluidos
    total_valuable = total - sum(pos[t]['_motor'] for t in excluidos if t in pos)
    diff_t = []
    for t in sorted(set(pos) | set(sum_ops_by_t)):
        if t in excluidos:
            continue
        a = pos.get(t, {}).get('resultado_usd', 0.0)
        b = sum_ops_by_t.get(t, 0.0)
        if abs(a - b) > 0.005:
            diff_t.append({'ticker': t, 'resultado_posicion': _r(a), 'resultado_operaciones': _r(b),
                           'diferencia': _r(b - a)})
    diferencia = max(abs(sum_pos - total_valuable), abs(sum_ops - total_valuable))

    posiciones = []
    for p in pos.values():
        if not any(p[k] is None or abs(p[k]) > 1e-9 for k in ('valor_inicial', 'flujo_neto', 'valor_final')):
            continue
        res = p['resultado_usd']
        posiciones.append({
            'ticker': p['ticker'], 'valor_inicial': _r(p['valor_inicial']), 'flujo_neto': _r(p['flujo_neto']),
            'valor_final': _r(p['valor_final']), 'resultado_usd': _r(res),
            'pct_del_resultado': _r(res / total * 100) if res is not None and abs(total) >= TOL_USD else None,
            'aporte_pp': _r(res / base * 100, 4) if res is not None and base > 0 else None})
    posiciones.sort(key=lambda r: (-(r['resultado_usd'] or 0), r['ticker']))

    warnings = [w for w in port.get('warnings', [])]
    if no_price or no_cost:
        warnings.append({'tipo': 'sin_precio_en_rango', 'tickers': sorted(no_price | no_cost)})
    if any(e['tipo'] == 'apertura' and e['k'] > kf for e in events if e['k'] <= kt):
        warnings.append({'tipo': 'apertura_posterior_al_inicio',
                         'detalle': 'entra con costo 0, igual que el motor (flujo 0)'})

    return {
        'metodo': METODO, 'nota': NOTA, 'metodo_operaciones': METODO_OPERACIONES,
        'nota_operaciones': NOTA_OPERACIONES,
        'desde': dates[kf], 'hasta': dates[kt], 'dias_calendario': span,
        'resumen': {'valor_inicial': _r(v_from), 'aportes_netos': _r(f_net), 'valor_final': _r(v_to),
                    'resultado_usd': _r(total), 'base_dietz': _r(base),
                    'rentabilidad_dietz_pct': _r(dietz * 100, 4) if dietz is not None else None},
        'posiciones': posiciones, 'operaciones': lotes,
        'reconciliacion': {'resultado_usd_cartera': _r(total), 'suma_posiciones': _r(sum_pos),
                           'suma_operaciones': _r(sum_ops), 'diferencia': _r(diferencia, 4),
                           'resultado_usd_valuable': _r(total_valuable), 'excluidos_sin_precio': excluidos,
                           'tolerancia_usd': TOL_USD,
                           'ok': bool(math.isfinite(diferencia) and diferencia <= TOL_USD and not excluidos),
                           'diferencias_por_ticker': diff_t},
        'warnings': warnings,
    }
