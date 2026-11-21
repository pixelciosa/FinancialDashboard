"""
Sondeo del screener de yfinance (para diseñar el modo "Explorar" del Portfolio Agent).

Uso, desde la carpeta del proyecto:
    venv/bin/python scripts/probe_screener.py

No lee ni escribe datos del portfolio. Solo consulta Yahoo y guarda qué funciona en
data/screener_probe.json (cantidad de resultados, tiempo, campos que trae cada resultado, errores).
"""
import json
import os
import time
import traceback

import yfinance as yf

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'screener_probe.json')
report = {'yfinance_version': getattr(yf, '__version__', None), 'api': {}, 'probes': [], 'search': [], 'catalog': {}}


def has(name):
    report['api'][name] = hasattr(yf, name)
    return report['api'][name]


for n in ('screen', 'EquityQuery', 'FundQuery', 'PREDEFINED_SCREENER_QUERIES', 'Search', 'Lookup'):
    has(n)

# Catálogo de campos y valores válidos (cambia entre versiones)
try:
    from yfinance import const
    for attr in ('EQUITY_SCREENER_FIELDS', 'EQUITY_SCREENER_EQ_MAP', 'SECTOR_INDUSTY_MAPPING'):
        v = getattr(const, attr, None)
        if isinstance(v, dict):
            report['catalog'][attr] = {str(k): (sorted(map(str, x))[:80] if isinstance(x, (set, list, tuple, dict)) else str(x))
                                       for k, x in v.items()}
except Exception as e:
    report['catalog']['error'] = repr(e)
if report['api'].get('PREDEFINED_SCREENER_QUERIES'):
    report['catalog']['predefined'] = sorted(yf.PREDEFINED_SCREENER_QUERIES)


def probe(name, build, **kw):
    t0 = time.time()
    entry = {'name': name, 'kwargs': {k: v for k, v in kw.items()}}
    try:
        q = build()
        res = yf.screen(q, **kw)
        quotes = (res or {}).get('quotes') or []
        entry.update(ok=True, count=len(quotes), total=(res or {}).get('total'),
                     symbols=[x.get('symbol') for x in quotes[:12]],
                     fields=sorted(set().union(*[set(x) for x in quotes[:20]])) if quotes else [],
                     sample={k: quotes[0].get(k) for k in ('symbol', 'shortName', 'quoteType', 'exchange', 'region',
                                                           'sector', 'industry', 'marketCap', 'trailingPE', 'forwardPE',
                                                           'dividendYield', 'trailingAnnualDividendYield',
                                                           'regularMarketPrice', 'averageAnalystRating',
                                                           'fiftyTwoWeekChangePercent', 'currency')} if quotes else None)
    except Exception as e:
        entry.update(ok=False, error=f'{type(e).__name__}: {str(e)[:300]}', trace=traceback.format_exc()[-600:])
    entry['seconds'] = round(time.time() - t0, 2)
    report['probes'].append(entry)
    print(f"{'OK ' if entry.get('ok') else 'ERR'} {name:<42} {entry.get('count', '-'):>4} res  {entry['seconds']:>5}s  "
          f"{entry.get('error', '')[:90]}")


if report['api'].get('screen'):
    # 1) Consultas predefinidas de Yahoo
    for key in ('undervalued_large_caps', 'undervalued_growth_stocks', 'growth_technology_stocks',
                'most_actives', 'aggressive_small_caps', 'portfolio_anchors'):
        probe(f'predef:{key}', lambda k=key: k, count=25)

    if report['api'].get('EquityQuery'):
        Q = yf.EquityQuery
        # 2) Filtros propios: uno por uno, para saber cuál falla
        probe('eq:region=us', lambda: Q('eq', ['region', 'us']), size=25)
        probe('eq:sector Healthcare (us)', lambda: Q('and', [Q('eq', ['region', 'us']), Q('eq', ['sector', 'Healthcare'])]), size=25)
        probe('is-in:sector Utilities,Energy', lambda: Q('and', [Q('eq', ['region', 'us']),
              Q('is-in', ['sector', 'Utilities', 'Energy'])]), size=25)
        probe('gt:intradaymarketcap 10B', lambda: Q('and', [Q('eq', ['region', 'us']), Q('gt', ['intradaymarketcap', 1e10])]), size=25)
        probe('btwn:peratio.lasttwelvemonths 5-20', lambda: Q('and', [Q('eq', ['region', 'us']), Q('gt', ['intradaymarketcap', 2e9]),
              Q('btwn', ['peratio.lasttwelvemonths', 5, 20])]), size=25)
        probe('gt:forward_dividend_yield 3', lambda: Q('and', [Q('eq', ['region', 'us']), Q('gt', ['intradaymarketcap', 2e9]),
              Q('gt', ['forward_dividend_yield', 3])]), size=25)
        probe('lt:beta 1', lambda: Q('and', [Q('eq', ['region', 'us']), Q('gt', ['intradaymarketcap', 2e9]),
              Q('lt', ['beta', 1])]), size=25)
        probe('eq:region ar', lambda: Q('eq', ['region', 'ar']), size=25)
        probe('combo: Healthcare, >5B, PE 5-25, sort mcap',
              lambda: Q('and', [Q('eq', ['region', 'us']), Q('eq', ['sector', 'Healthcare']),
                                Q('gt', ['intradaymarketcap', 5e9]), Q('btwn', ['peratio.lasttwelvemonths', 5, 25])]),
              size=25, sortField='intradaymarketcap', sortAsc=False)

# 3) Validar nombres sueltos (por si Gemma propone empresas por nombre)
if report['api'].get('Search'):
    for q in ('Johnson & Johnson', 'Mercado Libre', 'Coca-Cola', 'ZZZNOEXISTE123'):
        t0 = time.time()
        try:
            s = yf.Search(q, max_results=3)
            report['search'].append({'q': q, 'ok': True, 'seconds': round(time.time() - t0, 2),
                                     'quotes': [{k: x.get(k) for k in ('symbol', 'shortname', 'quoteType', 'exchange')}
                                                for x in (s.quotes or [])[:3]]})
        except Exception as e:
            report['search'].append({'q': q, 'ok': False, 'error': f'{type(e).__name__}: {str(e)[:200]}'})
        print(f"search {q!r}: {report['search'][-1].get('quotes') or report['search'][-1].get('error')}")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=1, default=str)
ok = sum(1 for p in report['probes'] if p.get('ok'))
print(f"\nyfinance {report['yfinance_version']} · {ok}/{len(report['probes'])} consultas OK · guardado en {OUT}")
