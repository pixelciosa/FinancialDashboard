"""Correlacion movil de la cartera contra referencias macro (Fase 4). Funcion pura sobre las
series de retornos del motor (engine.analyze). Faltantes son None, nunca 0."""
from . import engine

DOLLAR = 'DX-Y.NYB'
# Referencias fijas, en el orden de la UI: (simbolo de precio, nombre es/en).
REFERENCES = [
    ('BTC-USD', {'es': 'Bitcoin', 'en': 'Bitcoin'}),
    ('GLD', {'es': 'Oro', 'en': 'Gold'}),
    ('QQQ', {'es': 'Nasdaq', 'en': 'Nasdaq'}),
    (DOLLAR, {'es': 'Dólar', 'en': 'US dollar'}),
]


def rolling_vs_references(series, window, references=REFERENCES):
    """series: {nombre: retornos diarios alineados} con 'PORT' (engine.analyze()['series'] ->
    returns). Devuelve [{'id', 'nombre', 'estado', 'aviso', 'ultimo', 'valores'}].
    Referencia ausente de `series` -> estado 'sin_datos', valores y ultimo None, aviso con el
    codigo 'sin_datos_en_cache' (nunca 0)."""
    port = series['PORT']
    out = []
    for sym, nombre in references:
        r = series.get(sym)
        if r is None:
            out.append({'id': sym, 'nombre': nombre, 'estado': 'sin_datos',
                        'aviso': 'sin_datos_en_cache', 'ultimo': None, 'valores': None})
            continue
        vals = [None if v is None else round(v, 4) for v in engine.rolling_correlation(port, r, window)]
        last = next((v for v in reversed(vals) if v is not None), None)
        ok = last is not None
        out.append({'id': sym, 'nombre': nombre, 'estado': 'ok' if ok else 'sin_datos',
                    'aviso': None if ok else 'ventana_insuficiente', 'ultimo': last, 'valores': vals})
    return out
