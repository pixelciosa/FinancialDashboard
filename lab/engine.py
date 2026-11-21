"""Motor de calculo del Lab: funciones puras (listas y dicts in, datos out). Sin red ni disco.

Convenciones (plan, seccion 4):
- Solo activos invertidos. Una operacion en dia no habil pasa al siguiente dia habil.
- F_t = compras - ventas al monto de ejecucion (cantidad x cierre si falta el monto).
- V_t = tenencias x cierre.  r_t = (V_t - F_t) / V_(t-1) - 1.  Indice base 100 encadenado.
- Anualizacion con 252 dias. Volatilidad = desvio de r_t x sqrt(252).
- Drawdown contra el maximo previo.
- Mismos aportes en un indice: u0 = V_0/P_0, u += F_t/P_t, valor final u x P_fin.
- Venta mayor a la tenencia (redondeo): se recorta a la tenencia.
- Dividendo en unidades: unidades a costo cero (entran a la tenencia, flujo 0).
Los operaciones llevan `ticker` ya en simbolo de precio (ver prices.to_price_ops).
Datos faltantes son None, nunca 0. Nada de NaN/inf en la salida (`_clean`)."""
import bisect
import math

import numpy as np

TRADING_DAYS = 252
FLOW_TYPES = ('compra', 'venta', 'canje')       # mueven plata desde/hacia afuera de la cartera
UNIT_TYPES = ('apertura', 'dividendo_unidades')  # entran a la tenencia con flujo 0


def _clean(x):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# --- Calendario y operaciones -----------------------------------------------------
def day_index(dates, fecha):
    """Indice del dia habil de `fecha`: ese dia si es habil, si no el siguiente.
    Fecha anterior al calendario -> 0. Posterior al ultimo dia -> None."""
    i = bisect.bisect_left(dates, fecha)
    return i if i < len(dates) else None


def _fill_prices(prices):
    """Arrastra el ultimo cierre conocido (ffill). Los huecos iniciales quedan None."""
    out = {}
    for t, col in prices.items():
        last, filled = None, []
        for v in col:
            v = _clean(v)
            if v is not None:
                last = v
            filled.append(last)
        out[t] = filled
    return out


def portfolio_series(dates, prices, ops):
    """Serie de la cartera: tenencias, V_t, F_t, retornos e indice base 100.

    La serie arranca en el primer dia habil con operaciones (valor inicial V_0 incluye lo que
    se opero ese dia: los flujos del dia 0 no cuentan como aportes).
    Devuelve dict con: dates, values, flows, returns, index, v0, v_end, net_flows, start_idx,
    holdings_end, warnings, y para la atribucion: events (cada operacion tal como se aplico:
    cantidad recortada, flujo con signo, dia i absoluto) y ticker_prices ({ticker: {i: cierre
    usado}} solo para los dias con tenencia, igual al que valora V_t)."""
    prices = _fill_prices(prices)
    warnings = []
    n = len(dates)
    by_day = {}
    ignored = 0
    for o in ops:
        if o.get('cantidad') is None or o['tipo'] == 'dividendo_efectivo':
            continue  # dividendos en efectivo: ingreso, no flujo
        i = day_index(dates, o['fecha'])
        if i is None:
            ignored += 1
            continue
        by_day.setdefault(i, []).append(o)
    if ignored:
        warnings.append({'tipo': 'operaciones_posteriores_al_calendario', 'cantidad': ignored})
    if not by_day or n == 0:
        return {'dates': [], 'values': [], 'flows': [], 'returns': [], 'index': [], 'v0': None,
                'v_end': None, 'net_flows': None, 'start_idx': None, 'holdings_end': {},
                'warnings': warnings}
    start = min(by_day)
    seed = {}  # precio implicito de la primera operacion, solo si el activo no cotiza todavia
    holdings = {}
    values, flows = [], []
    clipped, no_price = [], set()
    events, tprices = [], {}

    def px(t, i):
        v = prices.get(t, [None] * n)[i] if t in prices else None
        if v is None:
            v = seed.get(t)
            if v is None:
                no_price.add(t)
        return v

    for i in range(start, n):
        f = 0.0
        for o in by_day.get(i, []):
            t, q, tipo = o['ticker'], o['cantidad'], o['tipo']
            if t not in seed and o.get('monto_usd') and q:
                seed[t] = abs(o['monto_usd'] / q)
            if tipo in UNIT_TYPES:
                holdings[t] = holdings.get(t, 0.0) + q
                events.append({'i': i, 'fecha': o['fecha'], 'ticker': t, 'tipo': tipo, 'q': q,
                               'flow': 0.0, 'nota': o.get('nota')})
                continue
            if tipo not in FLOW_TYPES:
                continue
            monto = o.get('monto_usd')
            if q > 0:
                holdings[t] = holdings.get(t, 0.0) + q
                if monto is None:
                    p = px(t, i)
                    monto = q * p if p is not None else 0.0
                f += monto
                events.append({'i': i, 'fecha': o['fecha'], 'ticker': t, 'tipo': tipo, 'q': q,
                               'flow': monto, 'nota': o.get('nota')})
            else:
                have = holdings.get(t, 0.0)
                sell = -q
                if sell > have:  # recorte por redondeo
                    clipped.append({'fecha': o['fecha'], 'ticker': t, 'pedido': sell, 'tenencia': have})
                    ratio = (have / sell) if sell else 0.0
                    sell = have
                else:
                    ratio = 1.0
                holdings[t] = have - sell
                if monto is None:
                    p = px(t, i)
                    monto = sell * p if p is not None else 0.0
                else:
                    monto = monto * ratio
                f -= monto
                events.append({'i': i, 'fecha': o['fecha'], 'ticker': t, 'tipo': tipo, 'q': -sell,
                               'flow': -monto, 'nota': o.get('nota')})
        v = 0.0
        for t, h in holdings.items():
            if abs(h) < 1e-12:
                continue
            p = px(t, i)
            if p is not None:
                v += h * p
                tprices.setdefault(t, {})[i] = p
        values.append(v)
        flows.append(f)
    if clipped:
        warnings.append({'tipo': 'ventas_recortadas', 'detalle': clipped})
    if no_price:
        warnings.append({'tipo': 'sin_precio', 'tickers': sorted(no_price)})
    rets, index = [None], [100.0]
    for k in range(1, len(values)):
        prev = values[k - 1]
        r = (values[k] - flows[k]) / prev - 1 if prev > 0 else 0.0
        rets.append(r)
        index.append(index[-1] * (1 + r))
    return {'dates': dates[start:], 'values': values, 'flows': flows, 'returns': rets,
            'index': index, 'v0': values[0], 'v_end': values[-1],
            'net_flows': sum(flows[1:]), 'start_idx': start,
            'holdings_end': {t: h for t, h in holdings.items() if abs(h) > 1e-9},
            'warnings': warnings, 'events': events, 'ticker_prices': tprices}


def asset_series(prices_col, start_idx):
    """Indice base 100 y retornos diarios de un precio, desde start_idx. None si no hay precio
    en el dia inicial."""
    col = [_clean(v) for v in prices_col[start_idx:]]
    if not col or col[0] is None or col[0] <= 0:
        return None
    index, rets, last = [100.0], [None], col[0]
    for v in col[1:]:
        v = v if v is not None else last
        index.append(100.0 * v / col[0])
        rets.append(v / last - 1)
        last = v
    return {'index': index, 'returns': rets}


# --- Estadisticas -----------------------------------------------------------------
def drawdown(index):
    """Caida contra el maximo previo, como fraccion (0 o negativa)."""
    peak, out = -math.inf, []
    for v in index:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak > 0 else 0.0)
    return out


def stats(index, returns):
    """cum, ann (252 dias), vol (desvio muestral x sqrt(252)) y max_dd, todo en fracciones."""
    rets = [r for r in returns if r is not None]
    n = len(rets)
    if len(index) < 2 or n == 0:
        return {'cum': None, 'ann': None, 'vol': None, 'max_dd': None, 'dias': n}
    cum = index[-1] / index[0] - 1
    ann = (1 + cum) ** (TRADING_DAYS / n) - 1 if (1 + cum) > 0 else None
    vol = float(np.std(rets, ddof=1)) * math.sqrt(TRADING_DAYS) if n > 1 else None
    return {'cum': _clean(cum), 'ann': _clean(ann), 'vol': _clean(vol),
            'max_dd': _clean(min(drawdown(index))), 'dias': n}


def period_returns(dates, index, key_len):
    """Retornos por periodo (key_len 7 = mes 'YYYY-MM', 4 = anio). El primer periodo parte de
    la base 100; los siguientes, del ultimo nivel del periodo anterior."""
    out, prev_level, cur, last, days = [], index[0], None, None, 0
    for d, lv in zip(dates, index):
        k = d[:key_len]
        if cur is not None and k != cur:
            out.append((cur, last / prev_level - 1, days))
            prev_level, days = last, 0
        cur, last, days = k, lv, days + 1
    if cur is not None:
        out.append((cur, last / prev_level - 1, days))
    return [{'periodo': k, 'retorno': _clean(v), 'dias': d} for k, v, d in out]


def same_contributions(v0, flows, prices_col, start_idx):
    """Mismos aportes en un indice: u0 = V_0/P_0; u += F_t/P_t; valor final u x P_fin."""
    col = prices_col[start_idx:]
    p0 = _clean(col[0]) if col else None
    if p0 is None or p0 <= 0 or v0 is None:
        return None
    u, last = v0 / p0, p0
    for k in range(1, len(col)):
        p = _clean(col[k])
        p = p if p is not None else last
        last = p
        if flows[k]:
            u += flows[k] / p
    return _clean(u * last)


# --- Correlacion ------------------------------------------------------------------
def _corr(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    x, y = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
    if x.std() == 0 or y.std() == 0:
        return None
    return _clean(np.corrcoef(x, y)[0, 1])


def correlation_matrix(series):
    """series: {nombre: retornos diarios alineados}. Devuelve (nombres, matriz con None)."""
    names = list(series)
    m = [[1.0 if a == b else _corr(series[a], series[b]) for b in names] for a in names]
    return names, m


def rolling_correlation(a, b, window):
    """Correlacion movil de ventana `window` dias; None hasta tener ventana completa."""
    out = []
    for k in range(len(a)):
        if k + 1 < window:
            out.append(None)
        else:
            out.append(_corr(a[k + 1 - window:k + 1], b[k + 1 - window:k + 1]))
    return out


MIN_DAYS_ANNUALIZE = 20


def position_stats(dates, prices, ops, port):
    """Rendimiento y volatilidad de cada posicion abierta, desde el primer dia en que se
    tuvo (no desde el inicio de la cartera) y su peso en el valor final.
    prices debe venir con ffill. Con menos de MIN_DAYS_ANNUALIZE dias, ann/vol son None."""
    first = {}
    for o in ops:
        if o.get('cantidad') and o['cantidad'] > 0 and o['tipo'] != 'dividendo_efectivo':
            i = day_index(dates, o['fecha'])
            if i is not None:
                first[o['ticker']] = min(first.get(o['ticker'], i), i)
    out = []
    v_end = port['v_end'] or 0.0
    for t, h in port['holdings_end'].items():
        col = prices.get(t)
        last = _clean(col[-1]) if col else None
        peso = (h * last / v_end) if (last is not None and v_end > 0) else None
        row = {'ticker': t, 'peso': _clean(peso), 'cum': None, 'ann': None, 'vol': None, 'dias': 0}
        a = asset_series(col, max(first.get(t, 0), port['start_idx'])) if col else None
        if a:
            st = stats(a['index'], a['returns'])
            row.update(cum=st['cum'], dias=st['dias'])
            if st['dias'] >= MIN_DAYS_ANNUALIZE:
                row.update(ann=st['ann'], vol=st['vol'])
        out.append(row)
    out.sort(key=lambda r: -(r['peso'] or 0))
    return out


# --- Orquestador ------------------------------------------------------------------
def analyze(dates, prices, ops, benchmarks):
    """Calcula cartera + cada indice sobre el mismo periodo. `benchmarks` son claves de `prices`.
    Devuelve None si no hay operaciones/precios."""
    prices = _fill_prices(prices)
    port = portfolio_series(dates, prices, ops)
    if not port['dates']:
        return None
    s = port['start_idx']
    out = {'port': port, 'dates': port['dates'], 'series': {}, 'missing': []}
    out['positions'] = position_stats(dates, prices, ops, port)
    out['series']['PORT'] = {'index': port['index'], 'returns': port['returns']}
    out['mw'] = {}
    for b in benchmarks:
        if b not in prices:
            out['missing'].append(b)
            continue
        a = asset_series(prices[b], s)
        if a is None:
            out['missing'].append(b)
            continue
        out['series'][b] = a
        out['mw'][b] = same_contributions(port['v0'], port['flows'], prices[b], s)
    for k, v in out['series'].items():
        v['dd'] = drawdown(v['index'])
        v['stats'] = stats(v['index'], v['returns'])
    return out
