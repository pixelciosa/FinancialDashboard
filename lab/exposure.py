"""Exposicion del Lab (Fase 4): peso por bloque contra su referencia, banda 5/25, tope por
posicion y aporte al riesgo. Funciones puras (listas/dicts in, datos out), sin red ni IA ni disco.
Modo descriptivo: la referencia es el peso de una fecha, no un objetivo; una alerta dice que el
bloque "se alejo de su referencia", nunca que esta mal. Los textos son CODIGOS de plantilla que
resuelve la UI (nada de prosa generada).

Convenciones:
- Pesos = valor de cada posicion al ultimo cierre / valor total (stress.current_weights, la misma
  convencion de /stress). Los activos sin precio no pesan.
- Banda = referencia +/- min(absoluta_pp, relativa x referencia). Se calcula siempre desde la
  referencia: cambiar la referencia en blocks.json cambia la banda. Estado: 'dentro' (bordes
  incluidos), 'arriba' o 'abajo'.
- Tope por posicion: nivel 'ok' | 'aviso' (>= aviso_pct) | 'alerta' (>= alerta_pct). Las
  excepciones (p. ej. un ETF nucleo) llevan etiqueta 'excepcion' y nivel 'ok' aunque superen el tope.
- Aporte al riesgo del bloque = suma_i en bloque de w_i x cov(r_i, r_p) / var(r_p), con pesos
  actuales CONSTANTES y retornos diarios de cierre a cierre sobre la misma ventana que la
  volatilidad del motor (desde el primer dia con operaciones hasta hoy). No coincide
  exactamente con la volatilidad realizada de /performance, que usa los pesos de cada dia.
  Un ticker sin historial en la ventana (sin cierre el primer dia) queda con aporte None y va a
  `sin_dato`; el resto se calcula con lo disponible y se informa la cobertura.
- Faltantes son None, nunca 0. Nada de NaN/Infinity (`_clean`)."""
import math

import numpy as np

from . import blocks as blocks_mod
from . import engine

MIN_RISK_DAYS = engine.MIN_DAYS_ANNUALIZE   # retornos minimos para estimar covarianzas
ALERT_BLOCK = 'bloque_se_alejo_de_referencia'
ALERT_POS_WARN = 'posicion_sobre_aviso'
ALERT_POS_ALERT = 'posicion_sobre_alerta'

METODO = ('Aporte al riesgo = peso x covarianza del activo con la cartera / varianza de la cartera, '
          'con los pesos actuales constantes y retornos diarios de cierre a cierre sobre toda la '
          'historia de la cartera (la misma ventana que la volatilidad). Los aportes suman 100%.')
NOTA = ('No coincide exactamente con la volatilidad realizada de Rendimiento, que usa los pesos '
        'de cada dia (con aportes y retiros); aqui los pesos son los de hoy.')


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


def _r(x, nd=4):
    x = _clean(x)
    return None if x is None else round(x, nd)


# --- Banda 5/25 -------------------------------------------------------------------
def band(ref_pct, rule):
    """Banda de un bloque (en %): {'min_pct', 'max_pct', 'ancho_pp', 'manda'}.
    ancho = min(absoluta_pp, relativa x referencia); 'manda' dice cual de las dos lo fija.
    Sin referencia -> None."""
    ref = _clean(ref_pct)
    if ref is None:
        return None
    rel = rule['relativa'] * ref
    absol = rule['absoluta_pp']
    ancho = min(absol, rel)
    return {'min_pct': max(0.0, ref - ancho), 'max_pct': ref + ancho, 'ancho_pp': ancho,
            'manda': 'absoluta' if absol <= rel else 'relativa'}


def band_state(peso_pct, b):
    """'dentro' | 'arriba' | 'abajo' (None sin peso o sin banda)."""
    peso = _clean(peso_pct)
    if peso is None or b is None:
        return None
    if peso > b['max_pct']:
        return 'arriba'
    if peso < b['min_pct']:
        return 'abajo'
    return 'dentro'


# --- Tope por posicion ------------------------------------------------------------
def position_cap(peso_pct, ticker, cap):
    """(nivel, etiqueta) de una posicion. Excepcion -> ('ok', 'excepcion') siempre."""
    if ticker in cap['excepciones']:
        return 'ok', 'excepcion'
    peso = _clean(peso_pct)
    if peso is None:
        return None, None
    if peso >= cap['alerta_pct']:
        return 'alerta', None
    if peso >= cap['aviso_pct']:
        return 'aviso', None
    return 'ok', None


def positions_cap(positions, mapping, cap):
    """Una fila por posicion (orden de `positions`: valor desc) con su nivel de tope."""
    out = []
    for p in positions:
        peso = _pct(p['peso'])
        nivel, etiqueta = position_cap(peso, p['ticker'], cap)
        out.append({'ticker': p['ticker'], 'bloque': blocks_mod.block_of(p['ticker'], mapping),
                    'peso_pct': peso, 'nivel': nivel, 'etiqueta': etiqueta,
                    'motivo': cap['excepciones'].get(p['ticker']) if etiqueta else None})
    return out


# --- Aporte al riesgo -------------------------------------------------------------
def risk_contributions(returns, weights, min_days=MIN_RISK_DAYS):
    """Aporte de cada activo a la varianza de la cartera de pesos constantes.

    returns: {ticker: lista de retornos diarios alineados (sin None)}; weights: {ticker: peso}.
    Solo entran los tickers presentes en ambos. Devuelve {'aportes': {ticker: fraccion},
    'vol_anual': float|None, 'dias': n} con aportes None si no hay datos suficientes o la
    varianza es 0. Los aportes suman 1 (la cartera es la suma de los activos incluidos)."""
    tickers = [t for t in weights if t in returns]
    n = min((len(returns[t]) for t in tickers), default=0)
    if not tickers or n < max(min_days, 3):
        return {'aportes': {t: None for t in tickers}, 'vol_anual': None, 'dias': n}
    R = np.array([returns[t][-n:] for t in tickers], dtype=float)   # k x n
    w = np.array([weights[t] for t in tickers], dtype=float)
    rp = w @ R
    var_p = float(np.var(rp, ddof=1))
    if not math.isfinite(var_p) or var_p <= 1e-18:
        return {'aportes': {t: None for t in tickers}, 'vol_anual': None, 'dias': n}
    Rc, rpc = R - R.mean(axis=1, keepdims=True), rp - rp.mean()
    cov = (Rc @ rpc) / (n - 1)
    contrib = w * cov / var_p
    return {'aportes': {t: _clean(c) for t, c in zip(tickers, contrib)},
            'vol_anual': _clean(math.sqrt(var_p) * math.sqrt(engine.TRADING_DAYS)), 'dias': n}


def window_returns(prices, start_idx, tickers):
    """Retornos diarios (cierre a cierre) desde start_idx para los tickers con cierre ese dia.
    Devuelve ({ticker: [retornos]}, [tickers sin historial])."""
    out, missing = {}, []
    for t in tickers:
        col = prices.get(t)
        a = engine.asset_series(col, start_idx) if col else None
        if a is None or len(a['returns']) < 2:
            missing.append(t)
            continue
        out[t] = a['returns'][1:]
    return out, missing


# --- Bloques ----------------------------------------------------------------------
def _block_rows(positions, cfg, rets_contrib):
    mapping, rule = cfg['map'], cfg['rule']
    order = [b['id'] for b in cfg['blocks']]
    agg = {b: {'peso': 0.0, 'tickers': [], 'aporte': 0.0, 'n_aporte': 0, 'sin_dato': [], 'peso_dato': 0.0}
           for b in order}
    for p in positions:
        a = agg[blocks_mod.block_of(p['ticker'], mapping)]
        a['peso'] += p['peso'] or 0.0
        a['tickers'].append(p['ticker'])
        c = rets_contrib.get(p['ticker'])
        if c is None:
            a['sin_dato'].append(p['ticker'])
        else:
            a['aporte'] += c
            a['n_aporte'] += 1
            a['peso_dato'] += p['peso'] or 0.0
    rows = []
    for b in cfg['blocks']:
        a = agg[b['id']]
        peso, ref = _pct(a['peso']), b['referencia_pct']
        banda = band(ref, rule)
        estado = band_state(peso, banda)
        desvio = None if (peso is None or ref is None) else peso - ref
        alerta = None
        if estado in ('arriba', 'abajo'):
            alerta = {'codigo': ALERT_BLOCK, 'tipo': 'bloque', 'bloque': b['id'], 'direccion': estado,
                      'desvio_pp': _r(desvio), 'peso_pct': peso, 'referencia_pct': ref,
                      'banda_min_pct': _r(banda['min_pct']), 'banda_max_pct': _r(banda['max_pct'])}
        rows.append({
            'id': b['id'], 'nombre': b['nombre'], 'comodin': b['comodin'],
            'tickers': a['tickers'], 'peso_pct': peso, 'referencia_pct': ref,
            'banda': None if banda is None else {'min_pct': _r(banda['min_pct']),
                                                 'max_pct': _r(banda['max_pct']),
                                                 'ancho_pp': _r(banda['ancho_pp']), 'manda': banda['manda']},
            'estado': estado, 'desvio_pp': _r(desvio), 'alerta': alerta,
            'aporte_riesgo_pct': _pct(a['aporte']) if a['n_aporte'] else None,
            'cobertura_riesgo_pct': _pct(a['peso_dato'] / a['peso']) if a['peso'] > 0 else None,
            'sin_dato': sorted(a['sin_dato'])})
    return rows


def compute(holdings, last_prices, prices, start_idx, cfg, dates=None, realized_vol=None):
    """Exposicion completa. holdings = engine.portfolio_series()['holdings_end']; last_prices y
    prices (alineados con ffill) vienen del adaptador; start_idx = primer dia con operaciones.
    `cfg` = blocks.load_blocks(). Devuelve un dict serializable (sin NaN)."""
    from . import stress   # misma convencion de pesos que /stress
    positions, total, sin_precio = stress.current_weights(holdings, last_prices)
    ret_by_t, _ = window_returns(prices, start_idx, [p['ticker'] for p in positions])
    w_avail = {p['ticker']: p['peso'] for p in positions if p['ticker'] in ret_by_t and p['peso'] is not None}
    risk = risk_contributions(ret_by_t, w_avail)
    contrib = {t: c for t, c in risk['aportes'].items()}
    # los pesos de los activos sin historial no entran al riesgo (aporte None, nunca 0)
    rows = _block_rows(positions, cfg, contrib)
    cobertura = sum(w_avail.values()) if total else None
    sin_dato = sorted(p['ticker'] for p in positions if contrib.get(p['ticker']) is None)
    aportes_ok = [c for c in contrib.values() if c is not None]
    posiciones = positions_cap(positions, cfg['map'], cfg['cap'])
    alertas = [r['alerta'] for r in rows if r['alerta']]
    for p in posiciones:
        if p['nivel'] in ('aviso', 'alerta'):
            alertas.append({'codigo': ALERT_POS_ALERT if p['nivel'] == 'alerta' else ALERT_POS_WARN,
                            'tipo': 'posicion', 'ticker': p['ticker'], 'bloque': p['bloque'],
                            'nivel': p['nivel'], 'peso_pct': p['peso_pct'],
                            'umbral_pct': cfg['cap']['alerta_pct' if p['nivel'] == 'alerta' else 'aviso_pct']})
    pesos_bloque = [r['peso_pct'] for r in rows if r['peso_pct'] is not None]
    return {
        'modo': cfg['meta'].get('modo'), 'fecha_referencia': cfg['meta'].get('fecha_referencia'),
        'regla_banda': {'nombre': cfg['rule']['nombre'], 'absoluta_pp': cfg['rule']['absoluta_pp'],
                        'relativa': cfg['rule']['relativa']},
        'tope_posicion': {'aviso_pct': cfg['cap']['aviso_pct'], 'alerta_pct': cfg['cap']['alerta_pct'],
                          'excepciones': sorted(cfg['cap']['excepciones'])},
        'valor_total_usd': None if total is None else round(total, 2),
        'bloques': rows,
        'suma_pesos_pct': _r(sum(pesos_bloque)) if pesos_bloque else None,
        'posiciones': posiciones,
        'alertas': alertas,
        'riesgo': {
            'metodo': METODO, 'nota': NOTA,
            'ventana': {'desde': None if dates is None or start_idx is None else dates[start_idx],
                        'hasta': None if not dates else dates[-1], 'dias': risk['dias']},
            'cobertura_pct': _pct(cobertura),
            'suma_aportes_pct': _pct(sum(aportes_ok)) if aportes_ok else None,
            'sin_dato': sin_dato,
            'vol_pesos_constantes_pct': _pct(risk['vol_anual']),
            'vol_realizada_pct': _pct(realized_vol),
        },
        'sin_precio': sin_precio,
    }
