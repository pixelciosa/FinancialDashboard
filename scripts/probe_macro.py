"""
Sondeo de las series del semaforo macro (Fase 6). Se corre a mano, CON RED:
    venv/bin/python scripts/probe_macro.py

Una sola llamada a yf.download para ^VIX, ^TNX, ^IRX, DX-Y.NYB, CL=F, GLD y SLV desde 2024-01-01.
Verifica disponibilidad, rango de fechas, escala (^TNX/^IRX deben venir en porcentaje, p. ej. 4,3,
no multiplicados por 10), huecos y cierres <= 0 (CL=F). No lee ni escribe datos del portfolio.
Guarda el resultado en data/lab/macro_probe.json. No inventa datos: si algo falta lo informa.
"""
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'data', 'lab', 'macro_probe.json')

SYMBOLS = ['^VIX', '^TNX', '^IRX', 'DX-Y.NYB', 'CL=F', 'GLD', 'SLV']
START = '2024-01-01'
# Rango esperado del ultimo cierre para detectar una escala equivocada (x10 o /10).
EXPECTED_LAST = {'^VIX': (8, 90), '^TNX': (0.5, 9), '^IRX': (0.1, 9), 'DX-Y.NYB': (70, 130),
                 'GLD': (100, 600), 'SLV': (10, 150)}
# Sustitutos documentados (se consultan solo si el simbolo principal falla).
SUBSTITUTES = {'^VIX': ['VIXY (ETF de futuros de VIX, no es el indice)'],
               '^TNX': ['FRED DGS10 (fuera de yfinance)', 'IEF (ETF 7-10a, precio no tasa)'],
               '^IRX': ['FRED DGS3MO (fuera de yfinance)', 'BIL (ETF 1-3m, precio no tasa)'],
               'DX-Y.NYB': ['UUP (ETF del dolar; nivel distinto, sirve para promedio y cambio)'],
               'CL=F': ['USO (ETF de petroleo, sin cierres negativos)'],
               'GLD': ['IAU'], 'SLV': ['SIVR']}


def analyze(series):
    s = series.dropna()
    if s.empty:
        return {'disponible': False}
    gaps = s.index.to_series().diff().dt.days.dropna()
    big = gaps[gaps > 5]
    res = {'disponible': True, 'primer_dato': s.index[0].strftime('%Y-%m-%d'),
           'ultimo_dato': s.index[-1].strftime('%Y-%m-%d'), 'n_dias': int(len(s)),
           'ultimo_cierre': round(float(s.iloc[-1]), 4), 'minimo': round(float(s.min()), 4),
           'maximo': round(float(s.max()), 4), 'mediana': round(float(s.median()), 4),
           'nan_en_rango': int(series.loc[s.index[0]:].isna().sum()),
           'cierres_menores_o_iguales_a_cero': int((s <= 0).sum()),
           'huecos_mayores_a_5_dias': [{'desde': i.strftime('%Y-%m-%d'), 'dias': int(d)} for i, d in big.items()]}
    nonpos = s[s <= 0]
    if len(nonpos):
        res['fechas_cierre_menor_o_igual_a_cero'] = [i.strftime('%Y-%m-%d') for i in nonpos.index]
    return res


def main():
    import yfinance as yf
    from lab import prices
    end = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    report = {'consultado': date.today().isoformat(), 'yfinance_version': getattr(yf, '__version__', None),
              'start': START, 'llamadas': 1, 'series': {}, 'alertas': [], 'sustitutos': {}}
    try:
        raw = yf.download(SYMBOLS, start=START, end=end, auto_adjust=True, progress=False,
                          group_by='ticker', threads=True)
        closes = prices.extract_closes(raw, SYMBOLS)
    except Exception as e:
        report['error'] = repr(e)
        closes = pd.DataFrame()
    for sym in SYMBOLS:
        if sym not in closes.columns:
            report['series'][sym] = {'disponible': False}
            report['alertas'].append(f'{sym}: sin datos')
            report['sustitutos'][sym] = SUBSTITUTES.get(sym, [])
            continue
        info = analyze(closes[sym])
        lo_hi = EXPECTED_LAST.get(sym)
        if info.get('disponible') and lo_hi:
            ok = lo_hi[0] <= info['ultimo_cierre'] <= lo_hi[1]
            info['escala_plausible'] = ok
            if not ok:
                report['alertas'].append(f"{sym}: ultimo cierre {info['ultimo_cierre']} fuera del rango esperado {lo_hi}")
        if info.get('cierres_menores_o_iguales_a_cero'):
            report['alertas'].append(f"{sym}: {info['cierres_menores_o_iguales_a_cero']} cierres <= 0")
        report['series'][sym] = info
    if {'^TNX', '^IRX'} <= set(closes.columns):
        both = closes[['^TNX', '^IRX']].dropna()
        report['curva_tnx_menos_irx'] = {'ultimo': round(float((both['^TNX'] - both['^IRX']).iloc[-1]), 4),
                                         'minimo': round(float((both['^TNX'] - both['^IRX']).min()), 4),
                                         'maximo': round(float((both['^TNX'] - both['^IRX']).max()), 4)}
    report['escala_tnx_irx_en_porcentaje'] = all(
        report['series'].get(s, {}).get('escala_plausible') for s in ('^TNX', '^IRX'))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if len(closes):
        os.makedirs(os.path.dirname(OUT), exist_ok=True)


if __name__ == '__main__':
    main()
