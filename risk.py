"""
Modelo de riesgo determinista (Reporte de Riesgo, fase R1).

Puntaje de 0 a 100: **más alto = más riesgo**. Sin IA: cada punto sale de una métrica
publicada (yfinance vía financials.py) o calculada sobre precios diarios, con su regla
explícita, para que la UI pueda explicar "por qué".

Pilares (máximo de puntos):
- Acciones: valuación (35) + finanzas (35) + mercado (30)
- ETF:      costo y concentración (35) + mercado (30), normalizado a 100
- Cripto:   mercado (30), normalizado a 100

Dato faltante: puntos neutros (la mitad del máximo) y `missing: true`: no saber también es riesgo.
Métrica que no aplica (beta en cripto, deuda en bancos, monedas distintas): `excluded: true`, fuera
del cálculo. Si la cobertura de métricas es menor al 50 %, el nivel es 'insufficient'.
"""
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import financials as fin

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'data', 'risk')
SCHEMA_VERSION = 2            # 2: valuación revaluada al precio del día + regla de mínimo de métricas
TTL_HOURS = 6                 # los precios cambian a diario; los estados contables, por trimestre
MIN_COVERAGE = 0.5
MIN_VALUATION_METRICS = 2     # con menos métricas reales de valuación no se etiqueta barata/justa/cara
# Métricas que dependen del precio: se escalan con el precio del día (BPA, ventas y FCF no cambian
# entre trimestres). El rendimiento FCF se deriva de market_cap, así que se ajusta solo.
PRICE_SCALED = ('pe', 'fwd_pe', 'ps', 'market_cap')

LEVELS = [(30, 'low'), (45, 'moderate'), (60, 'high'), (101, 'very_high')]
VALUATION_LABELS = [(0.35, 'cheap'), (0.60, 'fair'), (1.01, 'expensive')]


# ── Reglas de puntaje ────────────────────────────────────────────────────────

def metric(key, value, max_pts, bands, unit=None):
    """bands: [(límite_superior_excluido | None, puntos, nivel), ...] evaluadas en orden.
    Valor faltante = puntos neutros (redondeo de max/2)."""
    if value is None:
        return {'key': key, 'value': None, 'unit': unit, 'points': round(max_pts / 2), 'max': max_pts,
                'tier': 'unknown', 'missing': True}
    for limit, pts, tier in bands:
        if limit is None or value < limit:
            return {'key': key, 'value': round(value, 4), 'unit': unit, 'points': pts, 'max': max_pts,
                    'tier': tier, 'missing': False}
    raise ValueError(f'Bandas incompletas para {key}')


def fixed(key, value, points, max_pts, tier, unit=None, note=None):
    m = {'key': key, 'value': value, 'unit': unit, 'points': points, 'max': max_pts, 'tier': tier, 'missing': False}
    if note:
        m['note'] = note
    return m


def neutral(key, max_pts, note, unit=None):
    """No aplica o no es comparable: queda fuera del cálculo (no suma puntos ni máximo), con el motivo."""
    return {'key': key, 'value': None, 'unit': unit, 'points': 0, 'max': max_pts, 'tier': 'not_applicable',
            'missing': False, 'excluded': True, 'note': note}


LOW, MOD, HIGH, VHIGH = 'low', 'moderate', 'high', 'very_high'


def split_factor_since(hist, since_iso):
    """Producto de los splits ocurridos después de `since_iso` (fecha de la caché contable).
    Los cierres de yfinance vienen ajustados por split: el precio de referencia también debe ajustarse."""
    if hist is None or getattr(hist, 'empty', True) or 'Stock Splits' not in hist.columns or not since_iso:
        return 1.0
    since = pd.Timestamp(since_iso)
    since = since.tz_convert('UTC') if since.tzinfo else since.tz_localize('UTC')
    factor = 1.0
    for ts, v in hist['Stock Splits'].items():
        v = fin.num(v)
        t = pd.Timestamp(ts)
        t = t.tz_convert('UTC') if t.tzinfo else t.tz_localize('UTC')
        if v and v > 0 and t.normalize() > since.normalize():
            factor *= v
    return factor


def reprice_metrics(m, price_now, split_factor=1.0):
    """Lleva las métricas que dependen del precio al precio del día.

    `m['price']` es el precio con el que Yahoo calculó P/E, P/S y capitalización el día de la
    descarga contable. k = precio_hoy / precio_ref (ajustado por splits posteriores).
    Devuelve (métricas, base) donde base explica qué se hizo; sin precio válido no se toca nada."""
    ref = m.get('price')
    base = {'repriced': False, 'price_ref': ref, 'price_now': price_now, 'factor': None}
    if not ref or not price_now or ref <= 0 or price_now <= 0 or not split_factor or split_factor <= 0:
        return dict(m), base
    k = price_now / (ref / split_factor)
    out = dict(m)
    for key in PRICE_SCALED:
        if out.get(key) is not None:
            out[key] = out[key] * k
    out['price'] = price_now
    base.update({'repriced': True, 'factor': round(k, 6)})
    if split_factor != 1.0:
        base['split_factor'] = split_factor
    return out, base


def valuation_pillar(m):
    pe, fwd, ps = m.get('pe'), m.get('fwd_pe'), m.get('ps')
    ni = m.get('net_income_ttm')
    out = []
    if pe is None and ni is not None and ni < 0:
        out.append(fixed('pe', None, 10, 12, HIGH, 'x', note='losses'))
    elif pe is not None and pe <= 0:
        out.append(fixed('pe', pe, 10, 12, HIGH, 'x', note='losses'))
    else:
        out.append(metric('pe', pe, 12, [(15, 2, LOW), (25, 5, MOD), (40, 8, HIGH), (None, 12, VHIGH)], 'x'))
    # P/E futuro vs actual: si el mercado espera ganancias menores, el riesgo sube
    if pe and fwd and pe > 0 and fwd > 0:
        ratio = fwd / pe
        out.append(metric('fwd_vs_pe', ratio, 8, [(0.85, 2, LOW), (1.0, 4, MOD), (None, 8, HIGH)], 'ratio'))
    elif fwd is not None and fwd <= 0:
        out.append(fixed('fwd_vs_pe', None, 8, 8, HIGH, 'ratio', note='expected_losses'))
    else:
        out.append(metric('fwd_vs_pe', None, 8, [], 'ratio'))
    out.append(metric('ps', ps, 8, [(2, 1, LOW), (5, 3, MOD), (10, 6, HIGH), (None, 8, VHIGH)], 'x'))
    fcf, mcap = m.get('fcf_ttm'), m.get('market_cap')
    if m.get('financial_currency') != m.get('currency'):
        out.append(neutral('fcf_yield', 7, 'currency_mismatch', '%'))
    else:
        y = fcf / mcap * 100 if fcf is not None and mcap else None
        out.append(metric('fcf_yield', y, 7, [(0, 7, VHIGH), (2, 5, HIGH), (5, 3, MOD), (None, 1, LOW)], '%'))
    return {'key': 'valuation', 'metrics': out}


def financial_pillar(m, quarters, ttm_margin, bank):
    out = [metric('net_margin', ttm_margin, 8, [(0, 8, VHIGH), (10, 5, HIGH), (20, 3, MOD), (None, 1, LOW)], '%')]
    yoy = next((q['rev_yoy'] for q in reversed(quarters or []) if q.get('rev_yoy') is not None), None)
    out.append(metric('revenue_growth', yoy, 8,
                      [(-10, 8, VHIGH), (0, 6, HIGH), (5, 4, MOD), (15, 2, LOW), (None, 1, LOW)], '%'))
    if bank:
        # En bancos el apalancamiento y la liquidez corriente no se leen como en una empresa industrial
        out.append(neutral('debt_to_equity', 8, 'bank', 'x'))
        out.append(neutral('current_ratio', 5, 'bank', 'x'))
    else:
        dte = m.get('debt_to_equity')
        if dte is not None and dte < 0:
            out.append(fixed('debt_to_equity', dte, 8, 8, VHIGH, 'x', note='negative_equity'))
        else:
            out.append(metric('debt_to_equity', dte, 8, [(0.5, 1, LOW), (1, 3, MOD), (2, 5, HIGH), (None, 8, VHIGH)], 'x'))
        out.append(metric('current_ratio', m.get('current_ratio'), 5,
                          [(1, 5, VHIGH), (1.2, 3, HIGH), (2, 2, MOD), (None, 1, LOW)], 'x'))
    fcf = m.get('fcf_ttm')
    if bank:
        out.append(neutral('fcf_positive', 6, 'bank'))
    elif fcf is None:
        out.append(metric('fcf_positive', None, 6, []))
    else:
        out.append(fixed('fcf_positive', fcf, 1 if fcf >= 0 else 6, 6, LOW if fcf >= 0 else VHIGH, 'money'))
    return {'key': 'financial', 'metrics': out}


def etf_pillar(m, fin_payload):
    exp = fin_payload.get('expense_ratio_pct')
    out = [metric('expense_ratio', exp, 12, [(0.2, 1, LOW), (0.5, 4, MOD), (1.0, 8, HIGH), (None, 12, VHIGH)], '%')]
    holdings = [s for s in fin_payload.get('segs') or [] if s.get('sym')]
    top = sum(s['v'] for s in holdings[:10]) if holdings else None
    out.append(metric('top10_weight', top, 12, [(30, 2, LOW), (50, 6, MOD), (70, 9, HIGH), (None, 12, VHIGH)], '%'))
    sectors = fin_payload.get('sectors') or []
    max_sector = max((s['v'] for s in sectors), default=None)
    out.append(metric('max_sector_weight', max_sector, 11, [(30, 2, LOW), (50, 5, MOD), (70, 8, HIGH), (None, 11, VHIGH)], '%'))
    return {'key': 'cost', 'metrics': out}


def market_pillar(stats, beta, is_crypto=False):
    out = [
        metric('volatility', stats.get('volatility_pct'), 10, [(20, 2, LOW), (35, 4, MOD), (50, 7, HIGH), (None, 10, VHIGH)], '%'),
        # drawdown es negativo: -60 % < -50 %
        metric('max_drawdown', stats.get('max_drawdown_pct'), 8, [(-50, 8, VHIGH), (-30, 6, HIGH), (-15, 3, MOD), (None, 1, LOW)], '%'),
    ]
    above, rising = stats.get('above_sma200'), stats.get('sma200_rising')
    if above is None:
        out.append(metric('trend', None, 6, []))
    else:
        pts, tier, code = ((1, LOW, 'above_rising') if above and rising else
                           (2, MOD, 'above') if above else
                           (6, VHIGH, 'below_falling') if rising is False else (4, HIGH, 'below'))
        out.append(fixed('trend', code, pts, 6, tier))
    if is_crypto:
        out.append(neutral('beta', 6, 'not_applicable'))
    else:
        out.append(metric('beta', beta, 6, [(0.8, 1, LOW), (1.2, 3, MOD), (1.6, 4, HIGH), (None, 6, VHIGH)], 'x'))
    return {'key': 'market', 'metrics': out}


PILLAR_MAX = {'valuation': 35, 'financial': 35, 'cost': 35, 'market': 30}


def finalize_pillar(p):
    assert sum(x['max'] for x in p['metrics']) == PILLAR_MAX[p['key']], p['key']
    used = [x for x in p['metrics'] if not x.get('excluded')]
    p['points'] = sum(x['points'] for x in used)
    p['max'] = sum(x['max'] for x in used)
    return p


def level_for(score):
    return next(lv for lim, lv in LEVELS if score < lim)


def compose(pillars):
    pillars = [finalize_pillar(p) for p in pillars]
    pts = sum(p['points'] for p in pillars)
    mx = sum(p['max'] for p in pillars)
    expected = [x for p in pillars for x in p['metrics'] if not x.get('excluded')]
    coverage = round(sum(1 for x in expected if not x['missing']) / len(expected), 2) if expected else 0
    # Sin métricas aplicables no hay puntaje: 0 significaría "riesgo mínimo".
    score = round(pts / mx * 100) if mx else None
    val = next((p for p in pillars if p['key'] == 'valuation'), None)
    valuation_label, reason = None, None
    if val is None:
        reason = 'not_applicable'
    elif coverage < MIN_COVERAGE:
        reason = 'insufficient_coverage'
    else:
        known = sum(1 for x in val['metrics'] if not x.get('excluded') and not x['missing'])
        if known < MIN_VALUATION_METRICS or not val['max']:
            # Sin datos, los faltantes suman la mitad del máximo y darían "justa": no se etiqueta.
            reason = 'insufficient_valuation'
        else:
            r = val['points'] / val['max']
            valuation_label = next(lb for lim, lb in VALUATION_LABELS if r < lim)
    return {
        'risk_score': score, 'coverage': coverage,
        'level': level_for(score) if score is not None and coverage >= MIN_COVERAGE else 'insufficient',
        'valuation_label': valuation_label, 'valuation_reason': reason,
        'pillars': pillars,
    }


# ── Estadísticas de mercado (precios diarios) ────────────────────────────────

def market_stats(closes):
    """closes: Serie de cierres diarios con índice de fechas (≈ 1 año)."""
    c = pd.Series(closes).dropna()
    c = c[c > 0]
    if len(c) < 30:
        return {'n_days': int(len(c))}
    rets = np.log(c / c.shift(1)).dropna()
    vol = float(rets.std(ddof=1) * math.sqrt(252) * 100)
    peak = c.cummax()
    dd = (c / peak - 1) * 100
    out = {
        'n_days': int(len(c)), 'price': round(float(c.iloc[-1]), 4),
        'volatility_pct': round(vol, 2),
        'max_drawdown_pct': round(float(dd.min()), 2),
        'drawdown_from_high_pct': round(float(c.iloc[-1] / c.max() * 100 - 100), 2),
        'high_52w': round(float(c.max()), 4), 'low_52w': round(float(c.min()), 4),
        'rsi14': rsi(c, 14),
        'above_sma200': None, 'sma200': None, 'sma200_rising': None,
        # Técnico diario (mismo cálculo que el gráfico: EMA-20 y Bollinger 20, 2σ). No suman puntos.
        'ema20': ema_last(c, 20),
        'bb_upper': None, 'bb_lower': None,
    }
    if len(c) >= 20:
        mid, sd = c.rolling(20).mean().iloc[-1], c.rolling(20).std(ddof=0).iloc[-1]
        out['bb_upper'], out['bb_lower'] = round(float(mid + 2 * sd), 4), round(float(mid - 2 * sd), 4)
    if len(c) >= 200:
        sma = c.rolling(200).mean()
        out['sma200'] = round(float(sma.iloc[-1]), 4)
        out['above_sma200'] = bool(c.iloc[-1] > sma.iloc[-1])
        if len(c) >= 220 and not math.isnan(sma.iloc[-21]):
            out['sma200_rising'] = bool(sma.iloc[-1] > sma.iloc[-21])
    return out


def ema_last(c, period=20):
    """Último valor de la EMA sembrada con la SMA inicial (igual que server._compute_ema del gráfico)."""
    v = [float(x) for x in c]
    if len(v) < period:
        return None
    k = 2 / (period + 1)
    e = sum(v[:period]) / period
    for x in v[period:]:
        e = x * k + e * (1 - k)
    return round(e, 4)


def rsi(c, period=14):
    """RSI de Wilder."""
    d = c.diff().dropna()
    if len(d) < period:
        return None
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.iloc[:period].mean()
    al = loss.iloc[:period].mean()
    for g, l in zip(gain.iloc[period:], loss.iloc[period:]):
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
    if al == 0:
        return 100.0
    return round(float(100 - 100 / (1 + ag / al)), 1)


def monthly_closes(closes, n=12):
    """Últimos n cierres de fin de mes con su fecha real (para el gráfico de 12 meses)."""
    c = pd.Series(closes).dropna()
    if c.empty:
        return []
    idx = pd.to_datetime(c.index)
    if getattr(idx, 'tz', None) is not None:
        idx = idx.tz_localize(None)
    c.index = idx
    m = c.groupby(c.index.to_period('M')).last().tail(n)
    return [{'month': str(p), 'close': round(float(v), 4)} for p, v in m.items()]


def price_events(hist, earnings_dates=None, quarter_ends=None):
    """Eventos reales del último año: resultados presentados, dividendos y splits.
    Si no hay fechas de presentación, se usan los cierres de trimestre (tipo 'quarter_end',
    nunca presentados como fecha de resultados)."""
    events = []
    if hist is None or hist.empty:
        return events
    idx = pd.to_datetime(hist.index)
    start = idx.min()
    if 'Dividends' in hist.columns:
        for ts, v in hist['Dividends'].items():
            if fin.num(v):
                events.append({'date': pd.Timestamp(ts).date().isoformat(), 'type': 'dividend', 'value': round(float(v), 4)})
    if 'Stock Splits' in hist.columns:
        for ts, v in hist['Stock Splits'].items():
            if fin.num(v):
                events.append({'date': pd.Timestamp(ts).date().isoformat(), 'type': 'split', 'value': float(v)})
    if earnings_dates is not None and not getattr(earnings_dates, 'empty', True):
        now = pd.Timestamp(datetime.now(timezone.utc))
        for ts, row in earnings_dates.iterrows():
            t = pd.Timestamp(ts)
            t_utc = t.tz_convert('UTC') if t.tzinfo else t.tz_localize('UTC')
            s_utc = start.tz_convert('UTC') if start.tzinfo else start.tz_localize('UTC')
            if s_utc <= t_utc <= now:
                surprise = fin.num(row.get('Surprise(%)'))
                events.append({'date': t.date().isoformat(), 'type': 'earnings',
                               'value': round(surprise, 2) if surprise is not None else None})
    if not any(e['type'] == 'earnings' for e in events) and quarter_ends:
        lo, hi = start.date().isoformat(), idx.max().date().isoformat()
        for q in quarter_ends:
            if lo <= q['period_end'] <= hi:
                events.append({'date': q['period_end'], 'type': 'quarter_end', 'value': q.get('label')})
    return sorted(events, key=lambda e: e['date'])


# ── Armado del reporte (único lugar con red) ─────────────────────────────────

def _history(sym):
    try:
        return yf.Ticker(sym).history(period='1y', interval='1d', actions=True)
    except Exception:
        return None


_REPORTED_ERRORS = set()


def _earnings_dates(sym):
    """Fechas reales de presentación de resultados. yfinance las lee del HTML de Yahoo con
    pandas.read_html, que necesita `lxml`. Si falla, se informa en consola (una vez por tipo de error)."""
    try:
        return yf.Ticker(sym).get_earnings_dates(limit=8)
    except Exception as e:
        kind = type(e).__name__
        if kind not in _REPORTED_ERRORS:
            _REPORTED_ERRORS.add(kind)
            print(f'[risk] fechas de presentación de resultados no disponibles ({sym}): {kind}: {e}. '
                  'Se usan los cierres de trimestre. Si es ImportError: pip install lxml')
        return None


def _earnings_from_events(events):
    """Fechas de resultados ya obtenidas (de un reporte anterior) como DataFrame para price_events."""
    rows = [e for e in events or [] if e.get('type') == 'earnings']
    if not rows:
        return None
    idx = pd.DatetimeIndex([pd.Timestamp(e['date']) for e in rows]).tz_localize('UTC')
    return pd.DataFrame({'Surprise(%)': [e.get('value') for e in rows]}, index=idx)


def build_report(ticker, crypto_symbols=None, now=None, hist=None, prev=None):
    """hist: precios diarios ya descargados (actualización en lote); si es None se descargan acá.
    prev: reporte anterior; si el trimestre contable no cambió se reutilizan sus fechas de
    resultados en vez de volver a leerlas de Yahoo."""
    ticker = ticker.upper().strip()
    now = now or datetime.now(timezone.utc)
    crypto = crypto_symbols if crypto_symbols is not None else fin.CRYPTO_SYMBOLS
    is_crypto = fin.asset_kind(ticker, crypto_symbols=crypto) == 'crypto'
    sym = source_symbol(ticker, crypto)

    if hist is None:
        hist = _history(sym)
    closes = hist['Close'] if hist is not None and not hist.empty and 'Close' in hist.columns else pd.Series(dtype=float)
    stats = market_stats(closes)
    price_date = pd.Timestamp(closes.dropna().index[-1]).date().isoformat() if len(closes.dropna()) else None
    basis = None

    if is_crypto:
        fp, kind, metrics = {}, 'crypto', {}
        pillars = [market_pillar(stats, None, is_crypto=True)]
        events = price_events(hist)
    else:
        fp = fin.get_financials(ticker, crypto_symbols=crypto, now=now)
        kind = fp.get('kind', 'stock')
        metrics = fp.get('metrics') or {}
        if kind == 'etf':
            pillars = [etf_pillar(metrics, fp), market_pillar(stats, metrics.get('beta'))]
            events = price_events(hist)
        else:
            metrics, basis = reprice_metrics(metrics, stats.get('price'),
                                             split_factor_since(hist, fp.get('fetched_at')))
            basis.update({'price_ref_date': (fp.get('fetched_at') or '')[:10] or None, 'price_date': price_date})
            ttm_margin = fp['nm'][-1] if fp.get('has_ttm') and fp.get('nm') else (fp['nm'][-1] if fp.get('nm') else None)
            pillars = [valuation_pillar(metrics), financial_pillar(metrics, fp.get('quarters'), ttm_margin, fp.get('bankNote')),
                       market_pillar(stats, metrics.get('beta'))]
            reuse = prev and prev.get('financials_period') == fp.get('latest_period_end') \
                and any(e.get('type') == 'earnings' for e in prev.get('events') or [])
            edates = _earnings_from_events(prev['events']) if reuse else _earnings_dates(sym)
            events = price_events(hist, edates, fp.get('quarters'))

    report = compose(pillars)
    report.update({
        'ticker': ticker, 'kind': kind, 'name': fp.get('name') or ticker, 'type': fp.get('type') or kind,
        'source_symbol': sym, 'market': stats, 'prices_12m': monthly_closes(closes), 'events': events,
        'financials_period': fp.get('latest_period_end'), 'financials_period_label': fp.get('latest_period_label'),
        'financials_fetched_at': fp.get('fetched_at'), 'valuation_basis': basis, 'price_date': price_date,
        'computed_at': now.isoformat(), 'model_version': SCHEMA_VERSION,
    })
    report['inputs_hash'] = inputs_hash(report)
    return report


def source_symbol(ticker, crypto_symbols=None):
    """Símbolo a consultar en yfinance: cripto siempre con -USD (un símbolo cripto sin sufijo puede ser otro activo)."""
    ticker = ticker.upper().strip()
    crypto = crypto_symbols if crypto_symbols is not None else fin.CRYPTO_SYMBOLS
    if fin.asset_kind(ticker, crypto_symbols=crypto) == 'crypto' and not ticker.endswith('-USD'):
        return f'{ticker}-USD'
    return ticker


def inputs_hash(report):
    """Huella de los datos de entrada: la narrativa IA (R2) solo se regenera si cambia."""
    core = {'score': report['risk_score'], 'level': report['level'], 'period': report.get('financials_period'),
            'metrics': [(x['key'], x['tier']) for p in report['pillars'] for x in p['metrics']]}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


# ── Caché ────────────────────────────────────────────────────────────────────

def _path(ticker):
    return os.path.join(CACHE_DIR, f'{ticker.upper()}.json')


def read_cache(ticker):
    try:
        with open(_path(ticker), encoding='utf-8') as f:
            r = json.load(f)
        return r if r.get('model_version') == SCHEMA_VERSION else None
    except (OSError, ValueError):
        return None


def write_cache(ticker, report):
    import tempfile
    os.makedirs(CACHE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=f'.{ticker.upper()}.', suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(ticker))


def is_stale(report, now=None, fin_fetched_at=None):
    if not report:
        return True
    now = now or datetime.now(timezone.utc)
    if now - datetime.fromisoformat(report['computed_at']) > timedelta(hours=TTL_HOURS):
        return True
    # Si la tab financiera descargó un trimestre nuevo, el riesgo se recalcula
    return fin_fetched_at is not None and fin_fetched_at != report.get('financials_fetched_at')


def get_risk(ticker, crypto_symbols=None, now=None, force=False):
    """force: recalcula aunque la caché esté vigente (precios del día). Los estados contables
    siguen saliendo de la caché de financials.py (no se fuerza su descarga)."""
    ticker = ticker.upper().strip()
    now = now or datetime.now(timezone.utc)
    fin_entry = fin.read_cache(ticker)
    fin_fetched = fin_entry.get('fetched_at') if fin_entry else None
    cached = read_cache(ticker)
    if not force and not is_stale(cached, now, fin_fetched):
        return {**cached, 'cached': True}
    with fin.ticker_lock('risk:' + ticker):
        cached = read_cache(ticker)
        if not force and not is_stale(cached, now, fin_fetched):
            return {**cached, 'cached': True}
        report = build_report(ticker, crypto_symbols, now, prev=cached)
        return store(ticker, report, cached)


def store(ticker, report, cached):
    """Guarda el reporte salvo que la fuente de precios haya venido vacía (se conserva el anterior)."""
    if report['market'].get('n_days', 0) < 30 and cached:
        return {**cached, 'cached': True, 'stale_source': True}
    write_cache(ticker, report)
    return {**report, 'cached': False}


def summary(tickers):
    """Puntaje y nivel desde la caché (sin red), para el sidebar."""
    out = {}
    for t in tickers:
        r = read_cache(t)
        out[t.upper()] = ({'risk_score': r['risk_score'], 'level': r['level'],
                           'valuation_label': r.get('valuation_label'),
                           'valuation_reason': r.get('valuation_reason'), 'computed_at': r['computed_at']}
                          if r else None)
    return out
