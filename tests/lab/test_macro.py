"""Fase 6: semaforo macro. Logica con datos SINTETICOS (sin red, sin escribir en data/); un unico
test contra el fixture congelado (tests/lab/fixtures/macro_closes_2026-11-13.csv)."""
import copy
import json
import math
import os
import re

import numpy as np
import pandas as pd
import pytest
from flask import Flask

from ._helpers import FIXTURES, ROOT, load_lab

M = load_lab()
api, macro, macro_prices, prices = M['api'], M['macro'], M['macro_prices'], M['prices']

RULES_FILE = os.path.join(ROOT, 'config', 'lab', 'macro_rules.json')
with open(RULES_FILE, encoding='utf-8') as _f:
    RAW = json.load(_f)
RULES = macro.validate_rules(RAW)
IND = {i['id']: i for i in RULES['indicadores']}
SYMS = macro.required_symbols(RULES)


def light(iid, **row):
    """Luz cruda de un indicador con una fila de metricas a mano."""
    full = {'valor': row.get('valor'), 'promedio_movil': row.get('ma', float('nan')),
            'cambio_pct': row.get('chg', float('nan'))}
    return macro.raw_light(IND[iid], full, RULES)


def small_rules(cambio=3, prom=5, hys=5, desde='2024-01-01', minimo=5):
    d = copy.deepcopy(RAW)
    d['ventanas'] = {'cambio_dias_habiles': cambio, 'promedio_movil_dias': prom}
    d['histeresis']['dias_habiles_consecutivos'] = hys
    d['calibracion']['desde'] = desde
    d['sin_datos']['minimo_luces_validas'] = minimo
    return macro.validate_rules(d)


def closes(n=300, **over):
    """Cierres sinteticos de los 7 simbolos (constantes) con columnas sobreescritas."""
    idx = pd.bdate_range('2024-01-01', periods=n)
    base = {'^VIX': 15.0, '^TNX': 4.0, '^IRX': 3.0, 'DX-Y.NYB': 100.0, 'CL=F': 70.0, 'GLD': 200.0, 'SLV': 25.0}
    df = pd.DataFrame({k: [v] * n for k, v in base.items()}, index=idx)
    for k, v in over.items():
        df[k.replace('_', '-') if k == 'DX_Y.NYB' else k] = v
    return df


# --- Reglas en sus tres bandas y bordes exactos ---------------------------------------
@pytest.mark.parametrize('v,exp', [(19.99, 'verde'), (20, 'ambar'), (25, 'ambar'), (30, 'ambar'), (30.01, 'rojo')])
def test_vix_bands_and_edges(v, exp):
    assert light('vix', valor=v) == exp


@pytest.mark.parametrize('v,exp', [(-1, 'verde'), (0.2499, 'verde'), (0.25, 'ambar'), (0.40, 'ambar'),
                                   (0.50, 'ambar'), (0.5001, 'rojo'), (1.2, 'rojo')])
def test_tasa_10a_bands_and_edges(v, exp):
    assert light('tasa_10a', valor=v) == exp


@pytest.mark.parametrize('v,exp', [(1.3, 'verde'), (0.5001, 'verde'), (0.5, 'ambar'), (0.2, 'ambar'),
                                   (0.0, 'ambar'), (-0.0001, 'rojo'), (-1.3, 'rojo')])
def test_curva_bands_and_edges(v, exp):
    assert light('curva', valor=v) == exp


@pytest.mark.parametrize('iid,edge', [('dolar', 3), ('oro_plata', 10)])
def test_position_vs_average_and_change_rules(iid, edge):
    assert light(iid, valor=99, ma=100, chg=edge + 50) == 'verde'          # debajo del promedio: verde aunque suba
    assert light(iid, valor=100, ma=100, chg=edge + 50) == 'verde'         # cierre == promedio: verde (<=)
    assert light(iid, valor=100.01, ma=100, chg=edge - 0.01) == 'ambar'
    assert light(iid, valor=100.01, ma=100, chg=-5) == 'ambar'
    assert light(iid, valor=100.01, ma=100, chg=edge) == 'rojo'            # el borde cuenta como rojo (>=)
    assert light(iid, valor=100.01, ma=100, chg=edge + 0.01) == 'rojo'
    assert light(iid, valor=100.01, ma=float('nan'), chg=edge) == 'sin_datos'
    assert light(iid, valor=100.01, ma=100, chg=float('nan')) == 'sin_datos'
    assert light(iid, valor=float('nan'), ma=100, chg=edge) == 'sin_datos'


@pytest.mark.parametrize('v,exp', [(-20, 'verde'), (14.99, 'verde'), (15, 'ambar'), (22, 'ambar'),
                                   (30, 'ambar'), (30.01, 'rojo')])
def test_petroleo_bands_and_edges(v, exp):
    assert light('petroleo', valor=v) == exp


# --- Cambio a 63 dias y promedio de 200 dias ------------------------------------------
def test_change_63d_and_average_200d_values():
    n = 260
    df = closes(n, **{'^TNX': 4.0 + 0.01 * np.arange(n), 'DX-Y.NYB': 1.0 + np.arange(n)})
    ds = macro.prepare_closes(df, SYMS)
    tasa = macro.metric_frame(IND['tasa_10a'], ds, RULES)['valor']
    assert tasa.iloc[:63].isna().all()
    assert tasa.iloc[63] == pytest.approx(0.63) and tasa.iloc[-1] == pytest.approx(0.63)
    dol = macro.metric_frame(IND['dolar'], ds, RULES)
    assert dol['promedio_movil'].iloc[:199].isna().all()
    assert dol['promedio_movil'].iloc[199] == pytest.approx(100.5)          # media de 1..200
    assert dol['promedio_movil'].iloc[259] == pytest.approx(np.mean(np.arange(61, 261)))
    assert dol['cambio_pct'].iloc[100] == pytest.approx((101 / 38 - 1) * 100)
    assert macro.metric_frame(IND['curva'], ds, RULES)['valor'].iloc[0] == pytest.approx(1.0)


def test_float_noise_does_not_break_exact_edges():
    # 3.9 + 0.5 - 3.9 = 0.5000000000000004 en float: debe seguir siendo ambar (borde exacto)
    n = 70
    tnx = [3.9] * 63 + [4.4] * (n - 63)
    ev = macro.evaluate(closes(n, **{'^TNX': tnx}), RULES)
    assert ev['indicadores']['tasa_10a']['frame']['valor'].iloc[63] == 0.5
    assert ev['indicadores']['tasa_10a']['raw'][63] == 'ambar'
    # 100 -> 103 en 63 dias: +3% exacto -> rojo (sobre el promedio de 200d)
    n = 270
    ev = macro.evaluate(closes(n, **{'DX-Y.NYB': [100.0] * (n - 63) + [103.0] * 63}), RULES)
    assert ev['indicadores']['dolar']['frame']['cambio_pct'].iloc[-1] == 3.0
    assert ev['indicadores']['dolar']['raw'][-1] == 'rojo'


def test_oil_with_nonpositive_close_in_window_is_sin_datos_that_day():
    n = 160
    cl = np.full(n, 70.0)
    cl[80] = -5.0
    cl[81] = 0.0
    ev = macro.evaluate(closes(n, **{'CL=F': cl}), RULES)
    raw = ev['indicadores']['petroleo']['raw']
    assert raw[63] == 'verde' and raw[79] == 'verde'
    assert all(r == 'sin_datos' for r in raw[80:81 + 64])                   # mientras un <= 0 este en la ventana (t-63..t)
    assert raw[81 + 64] == 'verde' and raw[-1] == 'verde'
    f = ev['indicadores']['petroleo']['frame']['valor']
    assert not np.isinf(f.dropna()).any()


def test_gold_silver_ratio_with_zero_denominator_is_undefined_not_inf():
    n = 260
    slv = np.full(n, 25.0)
    slv[250] = 0.0
    ev = macro.evaluate(closes(n, SLV=slv), RULES)
    v = ev['indicadores']['oro_plata']['frame']['valor']
    assert v.iloc[0] == pytest.approx(8.0) and pd.isna(v.iloc[250]) and not np.isinf(v.dropna()).any()
    assert ev['indicadores']['oro_plata']['raw'][250] == 'sin_datos'


# --- Histeresis -------------------------------------------------------------------------
def hy(raw, n=5):
    return macro.apply_hysteresis(raw, n)[0]


def test_hysteresis_initial_state_is_first_valid_raw():
    assert hy(['sin_datos', 'sin_datos', 'rojo', 'rojo']) == ['sin_datos', 'sin_datos', 'rojo', 'rojo']
    shown, since = macro.apply_hysteresis(['sin_datos', 'ambar', 'ambar'], 5)
    assert shown == ['sin_datos', 'ambar', 'ambar'] and since == [0, 1, 1]


def test_hysteresis_changes_on_the_fifth_consecutive_day():
    raw = ['verde'] * 3 + ['rojo'] * 5 + ['rojo'] * 2
    shown, since = macro.apply_hysteresis(raw, 5)
    assert shown[:7] == ['verde'] * 7                                      # 4 dias de rojo: sigue verde
    assert shown[7:] == ['rojo'] * 3                                       # el quinto dia cambia
    assert since[6] == 0 and since[7] == 7 and since[9] == 7


def test_hysteresis_does_not_change_with_four_days():
    assert hy(['verde'] * 3 + ['rojo'] * 4 + ['verde'] * 3) == ['verde'] * 10


def test_hysteresis_streak_resets_when_raw_returns_to_current():
    raw = ['verde'] + ['rojo'] * 4 + ['verde'] + ['rojo'] * 4
    assert hy(raw) == ['verde'] * 10


def test_hysteresis_streak_restarts_when_candidate_switches_between_two_new_states():
    # actual verde; crudo rojo x3, luego ambar x4, luego rojo x4: ninguno llega a 5 seguidos
    raw = ['verde'] + ['rojo'] * 3 + ['ambar'] * 4 + ['rojo'] * 4
    assert hy(raw) == ['verde'] * 12
    # ambar x5 seguidos tras la oscilacion: cambia al quinto
    shown = hy(['verde'] + ['rojo'] * 3 + ['ambar'] * 5)
    assert shown[:-1] == ['verde'] * 8 and shown[-1] == 'ambar'


def test_hysteresis_sin_datos_is_shown_and_reinitializes():
    raw = ['verde', 'verde', 'sin_datos', 'sin_datos', 'rojo', 'rojo']
    assert hy(raw) == ['verde', 'verde', 'sin_datos', 'sin_datos', 'rojo', 'rojo']


def test_hysteresis_custom_days():
    assert hy(['verde', 'rojo', 'rojo'], 2) == ['verde', 'verde', 'rojo']


def test_hysteresis_runs_end_to_end_through_evaluate():
    # VIX verde 30 dias, luego 40 (rojo crudo) a partir del dia 30: rojo con histeresis el dia 34
    vix = [15.0] * 30 + [40.0] * 20
    ev = macro.evaluate(closes(50, **{'^VIX': vix}), RULES)
    e = ev['indicadores']['vix']
    assert e['raw'][30] == 'rojo' and e['luz'][30] == 'verde' and e['luz'][33] == 'verde'
    assert e['luz'][34] == 'rojo' and e['since'][-1] == 34


# --- Etiqueta de regimen ------------------------------------------------------------------
@pytest.mark.parametrize('rojas,esperada', [(0, 'favorable'), (1, 'favorable'), (2, 'mixto'),
                                             (3, 'adverso'), (4, 'adverso'), (6, 'adverso')])
def test_regime_label_counts_reds(rojas, esperada):
    luces = ['rojo'] * rojas + ['verde'] * (6 - rojas)
    r = macro.regime_label(luces, RULES)
    assert r == {'etiqueta': esperada, 'n_rojas': rojas, 'n_validas': 6}


def test_amber_does_not_count_for_the_label():
    r = macro.regime_label(['ambar'] * 5 + ['rojo'], RULES)
    assert r['etiqueta'] == 'favorable' and r['n_rojas'] == 1
    assert macro.regime_label(['ambar'] * 6, RULES)['n_rojas'] == 0


def test_fewer_than_five_valid_lights_is_sin_datos_suficientes():
    r = macro.regime_label(['rojo', 'rojo', 'rojo', 'rojo', 'sin_datos', 'sin_datos'], RULES)
    assert r == {'etiqueta': 'sin_datos_suficientes', 'n_rojas': 4, 'n_validas': 4}
    assert macro.regime_label(['rojo'] * 5 + ['sin_datos'], RULES)['etiqueta'] == 'adverso'   # 5 validas alcanzan


def test_missing_series_make_lights_gray_and_label_insufficient():
    df = closes(260).drop(columns=['GLD', 'SLV', 'CL=F'])
    ev = macro.evaluate(df, RULES)
    assert ev['indicadores']['oro_plata']['luz'][-1] == 'sin_datos'
    assert ev['indicadores']['petroleo']['luz'][-1] == 'sin_datos'
    assert ev['regimen'][-1] == {'etiqueta': 'sin_datos_suficientes', 'n_rojas': 0, 'n_validas': 4}


# --- Calibracion ------------------------------------------------------------------------------
def fake_ev(luces_por_id, n, regimen=None):
    dates = list(pd.bdate_range('2024-11-25', periods=n))
    inds = {}
    for iid, luz in luces_por_id.items():
        inds[iid] = {'frame': pd.DataFrame({'valor': np.arange(n, dtype=float)}, index=dates),
                     'raw': luz, 'luz': luz, 'since': [0] * n}
    return {'dates': dates, 'indicadores': inds,
            'regimen': regimen or [{'etiqueta': 'favorable', 'n_rojas': 0, 'n_validas': 6}] * n}


def test_calibration_counts_start_at_desde_and_review_threshold():
    rules = small_rules(desde='2024-12-02')
    n = 25                                  # 2024-11-25 .. 2024-12-27 (los primeros 5 dias son anteriores a 'desde')
    base = ['verde'] * n
    ev_ids = {i['id']: list(base) for i in rules['indicadores']}
    ev = fake_ev(ev_ids, n)
    d0 = ev['dates']
    assert d0[5] == pd.Timestamp('2024-12-02')
    # vix: 20 dias de calibracion; 7 rojos (35%) NO se marca; 8 rojos (40%) si
    ev['indicadores']['vix']['luz'] = ['rojo'] * 5 + ['verde'] * 8 + ['rojo'] * 7 + ['ambar'] * 5
    ev['indicadores']['curva']['luz'] = ['verde'] * 5 + ['verde'] * 12 + ['rojo'] * 8
    ev['indicadores']['dolar']['luz'] = ['rojo'] * 5 + ['sin_datos'] * 10 + ['ambar'] * 10
    cal = macro.calibrate(ev, rules)
    assert cal['dias'] == 20 and cal['desde'] == '2024-12-02' and cal['hasta'] == ev['dates'][-1].strftime('%Y-%m-%d')
    v = cal['indicadores']['vix']
    assert v['dias'] == {'verde': 8, 'ambar': 5, 'rojo': 7, 'sin_datos': 0}      # los 5 rojos previos no cuentan
    assert v['pct']['rojo'] == 35.0 and v['pct']['verde'] == 40.0 and v['pct']['ambar'] == 25.0
    assert cal['indicadores']['dolar']['dias'] == {'verde': 0, 'ambar': 10, 'rojo': 0, 'sin_datos': 10}
    assert cal['indicadores']['dolar']['pct']['sin_datos'] == 50.0
    ids = [x['id'] for x in cal['para_revision']]
    assert ids == ['curva']                                                  # curva: 8/20 = 40% > 35; vix: 35% no
    c = cal['para_revision'][0]
    assert c['pct_rojo'] == 40.0 and c['dias_rojo'] == 8 and len(c['periodos_rojo']) == 1
    assert c['periodos_rojo'][0]['dias'] == 8 and c['valor_en_rojo']['min'] <= c['valor_en_rojo']['max']
    assert cal['umbral_revision_pct_rojo'] == 35.0


def test_calibration_regime_distribution_and_empty_window():
    rules = small_rules(desde='2024-12-02')
    n = 10
    ev = fake_ev({i['id']: ['verde'] * n for i in rules['indicadores']}, n,
                 regimen=[{'etiqueta': e, 'n_rojas': 0, 'n_validas': 6} for e in
                          ['adverso'] * 5 + ['mixto'] * 3 + ['favorable'] * 2])
    cal = macro.calibrate(ev, rules)
    assert cal['dias'] == 5 and cal['regimen']['dias'] == {'favorable': 2, 'mixto': 3, 'adverso': 0, 'sin_datos_suficientes': 0}
    assert cal['regimen']['pct']['mixto'] == 60.0 and cal['para_revision'] == []
    assert macro.calibrate(fake_ev({i['id']: ['verde'] * 3 for i in rules['indicadores']}, 3),
                           small_rules(desde='2030-01-01'))['dias'] == 0


def test_calibration_through_evaluate_flags_a_permanently_red_light():
    rules = small_rules(cambio=3, prom=5, desde='2024-02-01')
    ev = macro.evaluate(closes(80, **{'^VIX': 45.0}), rules)
    cal = macro.calibrate(ev, rules)
    assert cal['indicadores']['vix']['pct']['rojo'] == 100.0
    assert [x['id'] for x in cal['para_revision']] == ['vix']
    assert cal['para_revision'][0]['valor_en_rojo']['mediana'] == 45.0


# --- Texto vs parametros (el usuario edita el texto: si no coincide, este test avisa) ------------------
def numbers(*texts):
    out = set()
    for t in texts:
        for m in re.findall(r'\d+(?:,\d+)?(?:\.\d+)?', t):
            out.add(float(m.replace(',', '.')))
    return out


def _bands(ind):
    """Texto de cada banda -> (operador, numero) o None si no tiene la forma '<op> numero'."""
    out = {}
    for color in ('verde', 'ambar', 'rojo'):
        t = ind[color].replace(',', '.')
        m = re.fullmatch(r'\s*(<=|>=|<|>)\s*([+-]?\d+(?:\.\d+)?)\s*%?\s*(?:\(.*\))?\s*', t)
        out[color] = (m.group(1), float(m.group(2))) if m else None
    return out


def _amber_range(ind):
    """'>= a y <= b' -> ((op1, a), (op2, b))."""
    t = ind['ambar'].replace(',', '.')
    m = re.fullmatch(r'\s*(>=|>|<=|<)\s*([+-]?\d+(?:\.\d+)?)\s*%?\s*y\s*(<=|>=|<|>)\s*([+-]?\d+(?:\.\d+)?)\s*%?\s*', t)
    assert m, f"{ind['id']}: banda ambar con forma inesperada: {ind['ambar']!r}"
    return (m.group(1), float(m.group(2))), (m.group(3), float(m.group(4)))


def test_text_and_parameters_agree():
    n, w = RAW['ventanas']['cambio_dias_habiles'], RAW['ventanas']['promedio_movil_dias']
    for ind in RAW['indicadores']:
        p, t = ind['parametros'], numbers(ind['verde'], ind['ambar'], ind['rojo'])
        expected = {float(v) for k, v in p.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if p['tipo'] == 'promedio_cambio':
            expected |= {float(n), float(w)}                  # "promedio 200d" y "cambio 63d" del texto
        assert t == expected, f"{ind['id']}: texto {sorted(t)} != parametros {sorted(expected)}"
    by = {i['id']: i for i in RAW['indicadores']}
    # ventanas del texto de dolar/oro_plata == ventanas del JSON
    for iid in ('dolar', 'oro_plata'):
        txt = numbers(by[iid]['ambar'], by[iid]['verde'])
        assert float(w) in txt and float(n) in txt


def test_text_matches_parameters_per_band_operator_and_sign():
    """Cada banda de texto (verde/ambar/rojo) debe decir lo mismo que el motor: el MISMO numero en
    el MISMO umbral (ambar vs rojo), el mismo operador y el mismo signo. Si el usuario cambia un operador
    (< por <=) o intercambia numeros sin tocar los parametros, salta."""
    for ind in RAW['indicadores']:
        p, tipo, iid = ind['parametros'], ind['parametros']['tipo'], ind['id']
        if tipo in ('nivel', 'cambio_abs', 'cambio_pct'):
            a, r = p['ambar_desde'], p['rojo_sobre']
            b = _bands(ind)
            lo, hi = _amber_range(ind)
            # motor: rojo si v > rojo_sobre; ambar si ambar_desde <= v <= rojo_sobre; verde si v < ambar_desde
            assert b['verde'] == ('<', float(a)), f'{iid}: verde {ind["verde"]!r} != < {a}'
            assert lo == ('>=', float(a)) and hi == ('<=', float(r)), f'{iid}: ambar {ind["ambar"]!r}'
            assert b['rojo'] == ('>', float(r)), f'{iid}: rojo {ind["rojo"]!r} != > {r}'
        elif tipo == 'diferencia':
            vs, rb = p['verde_sobre'], p['rojo_bajo']
            b = _bands(ind)
            lo, hi = _amber_range(ind)
            # motor: verde si v > verde_sobre; rojo si v < rojo_bajo; ambar en medio (ambos bordes incluidos)
            assert b['verde'] == ('>', float(vs)), f'{iid}: verde {ind["verde"]!r} != > {vs}'
            assert lo == ('>=', float(rb)) and hi == ('<=', float(vs)), f'{iid}: ambar {ind["ambar"]!r}'
            assert b['rojo'] == ('<', float(rb)), f'{iid}: rojo {ind["rojo"]!r} != < {rb}'
        else:  # promedio_cambio: texto libre; operadores y umbral del cambio
            thr = p['rojo_desde_cambio_pct']
            assert re.search(r'(cierre|ratio)\s*<=\s*promedio', ind['verde']), f'{iid}: verde {ind["verde"]!r}'
            assert re.search(r'>\s*promedio', ind['ambar']) and re.search(rf'<\s*\+{thr:g}\s*%', ind['ambar']), \
                f'{iid}: ambar {ind["ambar"]!r} debe decir "> promedio" y "< +{thr:g}%"'
            assert re.search(r'>\s*promedio', ind['rojo']) and re.search(rf'>=\s*\+{thr:g}\s*%', ind['rojo']), \
                f'{iid}: rojo {ind["rojo"]!r} debe decir "> promedio" y ">= +{thr:g}%"'
    # signo: el texto de tasa_10a usa "+" delante de cada numero (cambio con signo) y debe coincidir con el parametro
    t10 = next(i for i in RAW['indicadores'] if i['id'] == 'tasa_10a')
    for color in ('verde', 'ambar', 'rojo'):
        assert '+' in t10[color], f'tasa_10a.{color} perdio el signo +'
    # los parametros y los operadores ya verificados deben dar las mismas luces que el motor en cada borde
    for iid, ind in {i['id']: i for i in RAW['indicadores']}.items():
        p = ind['parametros']
        if p['tipo'] in ('nivel', 'cambio_abs', 'cambio_pct'):
            a, r = p['ambar_desde'], p['rojo_sobre']
            assert (light(iid, valor=a), light(iid, valor=r)) == ('ambar', 'ambar')
            assert (light(iid, valor=a - 0.01), light(iid, valor=r + 0.01)) == ('verde', 'rojo')


def test_text_and_parameters_agree_for_hysteresis_label_and_calibration():
    h = RAW['histeresis']
    assert numbers(h['descripcion']) == {float(h['dias_habiles_consecutivos'])}
    e, ep = RAW['etiqueta_regimen'], RAW['etiqueta_regimen']['parametros']
    assert numbers(e['favorable']) == {0.0, float(ep['favorable_max_rojas'])}
    assert numbers(e['mixto']) == {float(ep['mixto_rojas'])}
    assert numbers(e['adverso']) == {float(ep['adverso_min_rojas'])}
    assert 'minimo_luces_validas' in e['sin_datos_suficientes'] and RAW['sin_datos']['minimo_luces_validas'] == 5
    c = RAW['calibracion']
    assert numbers(c['accion']) == {float(c['umbral_revision_pct_rojo'])}
    # el motor lee exactamente estos numeros
    assert (RULES['ventana_cambio'], RULES['ventana_promedio'], RULES['histeresis']) == (63, 200, 5)
    assert (RULES['favorable_max'], RULES['mixto'], RULES['adverso_min'], RULES['minimo_validas']) == (1, 2, 3, 5)


def test_rules_validation_rejects_bad_config(tmp_path):
    bad = copy.deepcopy(RAW)
    del bad['indicadores'][0]['parametros']['rojo_sobre']
    with pytest.raises(macro.MacroRulesError):
        macro.validate_rules(bad)
    p = tmp_path / 'r.json'
    p.write_text('{no json', encoding='utf-8')
    with pytest.raises(macro.MacroRulesError):
        macro.load_rules(str(p))
    with pytest.raises(macro.MacroRulesError):
        macro.load_rules(str(tmp_path / 'no_existe.json'))


# --- Adaptador de precios macro --------------------------------------------------------------------
def _multi(data):
    cols = {}
    for t, s in data.items():
        cols[(t, 'Close')] = s
    df = pd.DataFrame(cols)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


class FakeDL:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, tickers, start, end):
        self.calls.append((list(tickers), start, end))
        return self.fn(list(tickers), start, end)


def _frame(tickers, start, end):
    idx = pd.bdate_range('2024-01-01', periods=5)
    return _multi({t: pd.Series(np.arange(5) + 10.0, index=idx) for t in tickers})


def test_macro_prices_single_call_writes_atomically_to_given_dir(tmp_path):
    dl = FakeDL(_frame)
    r = macro_prices.update_macro_prices(downloader=dl, cache_dir=str(tmp_path), today=pd.Timestamp('2026-11-13').date())
    assert r['ok'] and r['missing'] == [] and len(dl.calls) == 1
    assert dl.calls[0][0] == macro_prices.MACRO_SYMBOLS and dl.calls[0][1] == '2024-01-01'
    assert dl.calls[0][2] == '2026-11-14'
    assert sorted(os.listdir(tmp_path)) == ['macro_closes.csv']               # sin .tmp residual
    assert list(macro_prices.read_macro_cache(str(tmp_path)).columns) == macro_prices.MACRO_SYMBOLS


def test_macro_prices_failure_and_empty_download_keep_cache(tmp_path):
    macro_prices.update_macro_prices(downloader=FakeDL(_frame), cache_dir=str(tmp_path))
    before = open(macro_prices.cache_path(str(tmp_path)), 'rb').read()

    def boom(*a):
        raise RuntimeError('sin red')
    r = macro_prices.update_macro_prices(downloader=boom, cache_dir=str(tmp_path))
    assert not r['ok'] and 'sin red' in r['error']
    r = macro_prices.update_macro_prices(downloader=FakeDL(lambda *a: pd.DataFrame()), cache_dir=str(tmp_path))
    assert not r['ok'] and r['last_date'] is not None
    assert open(macro_prices.cache_path(str(tmp_path)), 'rb').read() == before


def test_macro_prices_symbol_without_data_keeps_cached_column(tmp_path):
    macro_prices.update_macro_prices(downloader=FakeDL(_frame), cache_dir=str(tmp_path))
    r = macro_prices.update_macro_prices(downloader=FakeDL(lambda t, s, e: _frame([x for x in t if x != 'SLV'], s, e)),
                                         cache_dir=str(tmp_path))
    assert r['ok'] and r['missing'] == []                                      # SLV sigue en la cache
    assert macro_prices.read_macro_cache(str(tmp_path))['SLV'].notna().all()


def test_macro_prices_cache_is_separate_file_and_symbols_match_rules():
    assert macro_prices.MACRO_CACHE_NAME == 'macro_closes.csv' != prices.CACHE_NAME
    assert set(macro_prices.MACRO_SYMBOLS) == set(SYMS)
    assert macro_prices.read_macro_cache('/no/existe') .empty


# --- Endpoint ----------------------------------------------------------------------------------------
def _client(monkeypatch, closes_df):
    monkeypatch.setattr(api, '_load_macro_closes', lambda: closes_df)
    app = Flask(__name__)
    api.register(app)
    return app.test_client()


def _strict(resp):
    def bad(c):
        raise AssertionError(f'constante no JSON en la respuesta: {c}')
    return json.loads(resp.data.decode(), parse_constant=bad)


def test_macro_endpoint_empty_cache_is_200_with_clear_status(monkeypatch):
    for empty in (pd.DataFrame(), None):
        r = _client(monkeypatch, empty).get('/api/lab/macro')
        d = _strict(r)
        assert r.status_code == 200 and d['ok'] is False and d['status'] == 'sin_precios_macro' and d['datos_al'] is None
        assert 'macro_prices' in d['error']


def test_macro_endpoint_invalid_config_is_200(monkeypatch, tmp_path):
    p = tmp_path / 'bad.json'
    p.write_text('{"version": 1}', encoding='utf-8')
    monkeypatch.setattr(macro, 'RULES_PATH', str(p))
    r = _client(monkeypatch, closes(10)).get('/api/lab/macro')
    d = _strict(r)
    assert r.status_code == 200 and d['status'] == 'config_invalida' and d['ok'] is False


def test_macro_endpoint_payload_shape_and_values(monkeypatch):
    vix = [15.0] * 250 + [40.0] * 10                                          # rojo crudo 10 dias: rojo con histeresis
    r = _client(monkeypatch, closes(260, **{'^VIX': vix})).get('/api/lab/macro')
    d = _strict(r)
    assert r.status_code == 200 and d['status'] == 'ok' and d['ok'] is True and d['series_faltantes'] == []
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', d['datos_al'])
    assert [i['id'] for i in d['indicadores']] == [i['id'] for i in RULES['indicadores']]
    v = d['indicadores'][0]
    assert {'id', 'nombre', 'metrica', 'unidad', 'valor_actual', 'luz', 'luz_cruda', 'desde', 'serie',
            'auxiliar_actual', 'series_origen'} <= set(v)
    assert set(v['nombre']) == {'es', 'en'} and v['valor_actual'] == 40.0
    assert v['luz'] == 'rojo' and v['luz_cruda'] == 'rojo'
    s = v['serie']
    assert len(s['fechas']) == len(s['valores']) == len(s['luces']) and s['fechas'] == sorted(s['fechas'])
    dolar = next(i for i in d['indicadores'] if i['id'] == 'dolar')
    assert set(dolar['serie']['auxiliares']) == {'promedio_movil', 'cambio_pct'}
    assert set(dolar['auxiliar_actual']) == {'promedio_movil', 'cambio_pct'}
    assert set(d['regimen']) >= {'etiqueta', 'n_rojas', 'n_validas'} and d['regimen']['n_rojas'] == 1
    assert d['regimen']['etiqueta'] == 'favorable'
    assert d['calibracion']['desde'] == '2024-12-01' and d['para_revision'] == d['calibracion']['para_revision']
    assert d['ventanas'] == {'cambio_dias_habiles': 63, 'promedio_movil_dias': 200}


def test_macro_endpoint_missing_series_are_null_not_zero(monkeypatch):
    df = closes(260).drop(columns=['SLV'])
    d = _strict(_client(monkeypatch, df).get('/api/lab/macro'))
    oro = next(i for i in d['indicadores'] if i['id'] == 'oro_plata')
    assert d['series_faltantes'] == ['SLV'] and oro['valor_actual'] is None and oro['luz'] == 'sin_datos'
    assert all(x is None for x in oro['serie']['valores']) and set(oro['serie']['luces']) == {'sin_datos'}
    assert d['regimen']['n_validas'] == 5 and d['regimen']['etiqueta'] == 'favorable'


def test_macro_endpoint_zero_and_negative_prices_do_not_produce_nan(monkeypatch):
    df = closes(260)
    df.loc[df.index[-3:], 'SLV'] = 0.0
    df.loc[df.index[-5:], 'CL=F'] = -10.0
    r = _client(monkeypatch, df).get('/api/lab/macro')
    d = _strict(r)
    assert r.status_code == 200
    oro = next(i for i in d['indicadores'] if i['id'] == 'oro_plata')
    pet = next(i for i in d['indicadores'] if i['id'] == 'petroleo')
    assert oro['valor_actual'] is None and pet['valor_actual'] is None and pet['luz'] == 'sin_datos'


# --- Aislamiento: nada escrito en data/ -----------------------------------------------------------------
def _snapshot(root):
    snap = {}
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            st = os.stat(p)
            snap[p] = (st.st_size, st.st_mtime_ns)
    return snap


def test_tests_do_not_write_into_real_data_dir(monkeypatch, tmp_path):
    data_dir = os.path.join(ROOT, 'data')
    before = _snapshot(data_dir)
    macro_prices.update_macro_prices(downloader=FakeDL(_frame), cache_dir=str(tmp_path))
    d = _strict(_client(monkeypatch, closes(260)).get('/api/lab/macro'))
    assert d['status'] == 'ok'
    assert _snapshot(data_dir) == before


# --- Fixture congelado -----------------------------------------------------------------------------------
def test_frozen_fixture_calibration_runs_and_payload_has_no_nan(monkeypatch):
    df = pd.read_csv(os.path.join(FIXTURES, 'macro_closes_2026-11-13.csv'), index_col=0, parse_dates=True)
    r = _client(monkeypatch, df).get('/api/lab/macro')
    d = _strict(r)                                                            # falla ante NaN/Infinity
    assert d['status'] == 'ok' and d['datos_al'] == '2026-11-13'
    cal = d['calibracion']
    assert cal['desde'] == '2024-12-01' and cal['dias'] > 400
    for iid, row in cal['indicadores'].items():
        assert sum(row['dias'].values()) == cal['dias']
        assert row['dias']['sin_datos'] == 0                                  # el historial cubre las ventanas
        assert sum(row['pct'].values()) == pytest.approx(100.0, abs=0.05)
    assert sum(cal['regimen']['dias'].values()) == cal['dias']
    assert all(i['luz'] in macro.LIGHTS for i in d['indicadores'])
    assert d['regimen']['etiqueta'] in macro.LABELS
    for i in d['indicadores']:
        assert i['valor_actual'] is not None
    # la escala de ^TNX/^IRX es porcentaje (no x10): la curva queda entre -3 y +3 pp
    curva = next(i for i in d['indicadores'] if i['id'] == 'curva')
    assert all(-3 < x < 3 for x in curva['serie']['valores'] if x is not None)
