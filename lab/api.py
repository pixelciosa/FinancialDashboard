"""Blueprint del Portfolio Lab. Rutas finas; el cálculo vive en módulos puros."""
import json
import logging
import os
import re

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lab_ui')

ROOT_DIR = os.path.dirname(UI_DIR)
_ABS_PATH = re.compile(r'(?:/Users|/home)/[^\s\'"<>,;)\]]+')


def _redact(msg):
    """Quita rutas absolutas de un mensaje: raiz del proyecto -> '.', home y /Users/... -> '…/'."""
    s = str(msg)
    for base in sorted({ROOT_DIR, os.path.realpath(ROOT_DIR)}, key=len, reverse=True):
        s = s.replace(base + os.sep, '').replace(base, '.')
    home = os.path.expanduser('~')
    if home and home != '~' and len(home) > 1:
        s = s.replace(home, '…')
    return _ABS_PATH.sub('…/', s)


lab_bp = Blueprint('lab', __name__, url_prefix='/api/lab')


# --- Codigos de error ---------------------------------------------------------------
# Campo `codigo`: cadena corta y estable que el frontend traduce. `error` (texto) se mantiene.
# Lista completa documentada en README.md (seccion Portfolio Lab) y en el skill portfolio-lab.
def _err(codigo, mensaje, http=200, **extra):
    return jsonify(dict({'ok': False, 'codigo': codigo, 'error': mensaje}, **extra)), http


NO_PRICES_MSG = ('No hay precios en cache. Corre POST /api/lab/refresh '
                 'o actualiza los precios del Lab.')
NO_OPS_MSG = 'El libro no tiene operaciones dentro del calendario de precios.'


def _no_prices():
    return _err('sin_precios', NO_PRICES_MSG, 200, status='sin_precios')


def _no_ops():
    return _err('sin_operaciones', NO_OPS_MSG, 200, status='sin_operaciones')


@lab_bp.route('/health')
def health():
    """Aditivo: {ok, phase} se conserva. `caches.*.datos_al` = ultimo dia de cada cache (null si no
    existe); `ultimo_refresh` = resumen del ultimo POST /refresh de este proceso (null si no hubo)."""
    from . import refresh
    fechas = refresh.cache_dates()
    return jsonify({'ok': True, 'phase': 0,
                    'caches': {k: {'datos_al': v} for k, v in fechas.items()},
                    'ultimo_refresh': refresh.last_refresh()})


@lab_bp.route('/refresh', methods=['POST'])
def refresh_route():
    """Recalcula precios (libro + indices), cache de estres y cache macro, de forma sincrona. 415 si
    no es JSON; 409 si ya hay un refresh corriendo. Las fallas de una cache no impiden las demas."""
    from . import refresh
    if not request.is_json:
        return _err('tipo_contenido_invalido', 'Content-Type debe ser application/json', 415)
    if _read_limited() is None:
        return _err('cuerpo_demasiado_grande', 'cuerpo demasiado grande', 413)
    if not refresh.REFRESH_LOCK.acquire(blocking=False):
        return _err('refresh_en_curso', 'Ya hay un refresh en curso', 409, status='en_curso')
    try:
        return jsonify(refresh.run_refresh())
    finally:
        refresh.REFRESH_LOCK.release()


@lab_bp.route('/ledger')
def ledger_route():
    from . import ledger
    res = ledger.load_ledger()
    return jsonify({'ok': True, 'ops': res['ops'], 'errors': res['errors'], 'counts': res['counts']})


@lab_bp.route('/reconcile')
def reconcile_route():
    from . import ledger
    res = ledger.load_ledger()
    try:
        with open(ledger.PORTFOLIO_CSV, encoding='utf-8-sig', newline='') as f:
            holdings = ledger.parse_portfolio_holdings(f.read())
    except OSError:
        return _err('csv_portfolio_ilegible', 'Could not read the portfolio CSV (PORTFOLIO_CSV)', 404)
    rows = ledger.reconcile(ledger.final_quantities(res['ops']), holdings,
                            excluded_qty=ledger.excluded_quantities(res.get('excluded_ops')))
    return jsonify({'ok': True, 'tolerancia': 0.02, 'rows': rows,
                    'resumen': ledger.summarize_reconcile(rows),
                    'fuera_de_tolerancia': [r for r in rows if r['estado'] != 'ok'],
                    'ventas_recortadas': _trimmed_sales_block(),
                    'errors': res['errors']})


def _trimmed_sales_block():
    """Ventas que el motor recorto a la tenencia, sobre el umbral de config.json. Reutiliza los
    warnings de engine.portfolio_series. Sin precios en cache no hay calendario: lista null."""
    from . import engine, ledger
    umbral = ledger.trim_threshold_pct()
    dates, px, ops = _load_inputs()
    if not dates:
        return {'status': 'sin_precios', 'umbral_pct': umbral, 'lista': None, 'bajo_umbral': None}
    port = engine.portfolio_series(dates, px, ops)
    clipped = next((w['detalle'] for w in port['warnings'] if w['tipo'] == 'ventas_recortadas'), [])
    return dict(ledger.trimmed_sales(clipped, umbral), status='ok')


# --- Motor: performance, correlacion, riesgo-retorno --------------------------------
def _pct(x, nd=2):
    return None if x is None else round(x * 100, nd)


def _series_pct(xs, nd=4):
    return [_pct(x, nd) for x in xs]


def _load_inputs():
    """(dates, prices, ops) desde el libro y la cache de precios. Los tests lo reemplazan."""
    from . import ledger, prices
    ops = prices.to_price_ops(ledger.load_ledger()['ops'])
    dates, px = prices.load_aligned()
    return dates, px, ops


def _load_stress_closes():
    """Cierres de la cache de estres (DataFrame, vacio si no existe). Los tests lo reemplazan."""
    from . import stress_prices
    return stress_prices.read_stress_cache()


def _analysis(benchmarks):
    """Devuelve (resultado, None) o (None, respuesta de estado). Nunca lanza por falta de datos."""
    from . import engine
    dates, px, ops = _load_inputs()
    if not dates:
        return None, _no_prices()
    res = engine.analyze(dates, px, ops, benchmarks)
    if res is None:
        return None, _no_ops()
    return res, None


def _bench_key(raw):
    from . import prices
    key = prices.price_symbol(raw or 'SPY')
    return key if key in prices.INDEX_TICKERS else None


def _stats_pct(st):
    return {'cum_pct': _pct(st['cum']), 'ann_pct': _pct(st['ann']), 'vol_pct': _pct(st['vol']),
            'max_dd_pct': _pct(st['max_dd']), 'dias': st['dias']}


@lab_bp.route('/performance')
def performance_route():
    from . import engine, prices
    bench = _bench_key(request.args.get('benchmark'))
    if bench is None:
        return _err('benchmark_invalido', 'benchmark invalido', 400, validos=prices.INDEX_TICKERS)
    res, bad = _analysis([bench])
    if bad:
        return bad
    if bench not in res['series']:
        return _err('sin_precios', f'No hay precios de {bench} en cache.', 200, status='sin_precios')
    port, dates = res['port'], res['dates']
    body = {'ok': True, 'status': 'ok', 'benchmark': bench, 'desde': dates[0], 'hasta': dates[-1],
            'dates': dates, 'series': {}, 'stats': {}, 'monthly': {}, 'yearly': {}}
    for name, key in (('portfolio', 'PORT'), ('benchmark', bench)):
        s = res['series'][key]
        body['series'][name] = {'index': [round(v, 4) for v in s['index']],
                                'drawdown_pct': _series_pct(s['dd'])}
        body['stats'][name] = _stats_pct(s['stats'])
        for label, n in (('monthly', 7), ('yearly', 4)):
            body[label][name] = [{'periodo': r['periodo'], 'retorno_pct': _pct(r['retorno']),
                                  'dias': r['dias']}
                                 for r in engine.period_returns(dates, s['index'], n)]
    invertido = port['v0'] + port['net_flows']
    mw = res['mw'].get(bench)
    body['cash'] = {'valor_inicial': round(port['v0'], 2), 'valor_final': round(port['v_end'], 2),
                    'aportes_netos': round(port['net_flows'], 2), 'invertido': round(invertido, 2),
                    'resultado_usd': round(port['v_end'] - invertido, 2)}
    body['mismos_aportes'] = {'benchmark': bench, 'valor_final': None if mw is None else round(mw, 2),
                              'diferencia_vs_cartera': None if mw is None else round(mw - port['v_end'], 2)}
    body['warnings'] = port['warnings']
    return jsonify(body)


@lab_bp.route('/correlation')
def correlation_route():
    from . import correlation, engine, prices
    try:
        window = int(request.args.get('window', 60))
    except ValueError:
        return _err('ventana_invalida', 'window debe ser un entero', 400)
    if not 5 <= window <= 250:
        return _err('rango_invalido', 'window debe estar entre 5 y 250 dias', 400)
    res, bad = _analysis(prices.INDEX_TICKERS)
    if bad:
        return bad
    series = {k: v['returns'] for k, v in res['series'].items()}
    names, matrix = engine.correlation_matrix(series)
    port_r = series['PORT']
    rolling = {k: engine.rolling_correlation(port_r, r, window) for k, r in series.items() if k != 'PORT'}
    return jsonify({'ok': True, 'status': 'ok', 'names': names, 'matrix': matrix, 'window': window,
                    'dates': res['dates'],
                    'rolling': {k: [None if v is None else round(v, 4) for v in vs]
                                for k, vs in rolling.items()},
                    'movil': {'ventana': window,
                              'referencias': correlation.rolling_vs_references(series, window)},
                    'missing': res['missing']})


@lab_bp.route('/exposure')
def exposure_route():
    """Peso por bloque contra su referencia, bandas 5/25, tope por posicion, aporte al riesgo y
    alertas (modo descriptivo). Solo lectura."""
    from . import blocks, engine, exposure, stress
    try:
        cfg = blocks.load_blocks()
    except blocks.BlocksError as e:
        return _err('config_invalida', _redact(e), 200, status='config_invalida')
    dates, px, ops = _load_inputs()
    if not dates:
        return _no_prices()
    port = engine.portfolio_series(dates, px, ops)
    if not port['dates']:
        return _no_ops()
    vol = engine.stats(port['index'], port['returns'])['vol']
    body = exposure.compute(port['holdings_end'], stress.last_prices_from(px), px, port['start_idx'],
                            cfg, dates=dates, realized_vol=vol)
    body.update(ok=True, status='ok', datos_al=dates[-1],
                advertencias=[dict(w) for w in port['warnings']] + cfg['warnings'])
    return jsonify(body)


@lab_bp.route('/risk-return')
def risk_return_route():
    from . import prices
    res, bad = _analysis(prices.INDEX_TICKERS)
    if bad:
        return bad
    def row(key, tipo, st, **extra):
        return dict({'id': key, 'tipo': tipo, 'vol_pct': _pct(st['vol']), 'ann_pct': _pct(st['ann']),
                     'cum_pct': _pct(st['cum']), 'dias': st['dias']}, **extra)
    items = [row('PORT', 'cartera', res['series']['PORT']['stats'])]
    items += [row(k, 'indice', v['stats']) for k, v in res['series'].items() if k != 'PORT']
    items += [{'id': p['ticker'], 'tipo': 'posicion', 'vol_pct': _pct(p['vol']), 'ann_pct': _pct(p['ann']),
               'cum_pct': _pct(p['cum']), 'dias': p['dias'], 'peso_pct': _pct(p['peso'])}
              for p in res['positions']]
    return jsonify({'ok': True, 'status': 'ok', 'desde': res['dates'][0], 'hasta': res['dates'][-1],
                    'items': items, 'missing': res['missing']})


@lab_bp.route('/attribution')
def attribution_route():
    """Aporte por posicion (Dietz modificado) y por operacion (lotes FIFO) en [from, to]."""
    from . import attribution, engine
    desde, hasta = request.args.get('from') or None, request.args.get('to') or None
    try:   # valida formato y orden antes de tocar datos
        for nombre, v in (('from', desde), ('to', hasta)):
            if v:
                attribution.parse_date(v, nombre)
    except ValueError as e:
        return _err('fecha_invalida', _redact(e), 400)
    if desde and hasta and attribution.parse_date(desde) > attribution.parse_date(hasta):
        return _err('rango_invalido', f"'from' ({desde}) es posterior a 'to' ({hasta})", 400)
    dates, px, ops = _load_inputs()
    if not dates:
        return _no_prices()
    port = engine.portfolio_series(dates, px, ops)
    try:
        res = attribution.attribute(port, desde, hasta)
    except ValueError as e:   # rango fuera del calendario o de menos de dos dias habiles
        return _err('rango_invalido', _redact(e), 400)
    if res is None:
        return _no_ops()
    return jsonify(dict(res, ok=True, status='ok', solicitado={'from': desde, 'to': hasta}))


@lab_bp.route('/stress')
def stress_route():
    """Escenarios historicos aplicados a los pesos actuales. Solo lectura: no descarga precios
    (la cache de estres se arma con `python -m lab.stress_prices`)."""
    from . import blocks, engine, stress
    only = request.args.get('escenario') or None
    try:
        scenarios, cfg = stress.load_scenarios(), blocks.load_blocks()
    except (stress.ScenariosError, blocks.BlocksError) as e:
        return _err('config_invalida', _redact(e), 200, status='config_invalida')
    ids = [s['id'] for s in scenarios]
    if only and only not in ids:
        return _err('escenario_invalido', f"escenario invalido: '{only}'", 400, validos=ids)
    dates, px, ops = _load_inputs()
    if not dates:
        return _no_prices()
    port = engine.portfolio_series(dates, px, ops)
    if not port['dates']:
        return _no_ops()
    positions, total, sin_precio = stress.current_weights(port['holdings_end'], stress.last_prices_from(px))
    order = [b['id'] for b in cfg['blocks']]
    pesos, pesos_bloque = stress.weights_payload(positions, cfg['map'], cfg['names'], order)
    datos_al = dates[-1]
    results = stress.run_all(scenarios, positions, _load_stress_closes(), cfg, datos_al,
                             only=[only] if only else None)
    ok = any(r['status'] == 'ok' for r in results)
    advertencias = [dict(w) for w in port['warnings']] + cfg['warnings']
    if sin_precio:
        advertencias.append({'tipo': 'sin_precio_actual', 'tickers': sin_precio,
                             'mensaje': 'Posiciones sin ultimo cierre: no entran en los pesos.'})
    body = {'ok': True, 'status': 'ok' if ok else 'sin_precios_estres', 'datos_al': datos_al,
            'valor_total_usd': None if total is None else round(total, 2),
            'pesos_actuales': pesos, 'pesos_por_bloque': pesos_bloque,
            'escenarios': results, 'advertencias': advertencias}
    if not ok:
        body['mensaje'] = ('No hay precios historicos de escenarios en la cache de estres. '
                           'Corre `python -m lab.stress_prices` para descargarlos.')
    return jsonify(body)


# --- Semaforo macro -----------------------------------------------------------------
def _load_macro_closes():
    """Cierres de la cache macro (DataFrame, vacio si no existe). Los tests lo reemplazan."""
    from . import macro_prices
    return macro_prices.read_macro_cache()


@lab_bp.route('/macro')
def macro_route():
    """Luces, etiqueta de regimen y calibracion del semaforo macro. Solo lectura: no descarga
    precios (la cache se arma con `python -m lab.macro_prices`). Sin cache: 200 con status claro."""
    from . import macro
    try:
        rules = macro.load_rules()
    except macro.MacroRulesError as e:
        return _err('config_invalida', _redact(e), 200, status='config_invalida', datos_al=None)
    closes = _load_macro_closes()
    body = macro.build_payload(closes, rules) if closes is not None and len(closes) else None
    if body is None:
        return _err('sin_precios_macro', 'No hay precios macro en cache. Corre `python -m lab.macro_prices`.',
                    200, status='sin_precios_macro', datos_al=None)
    faltan = [s for s in macro.required_symbols(rules) if s not in closes.columns]
    body.update({'ok': True, 'status': 'ok', 'series_faltantes': faltan,
                 'estado_reglas': rules['estado']})
    return jsonify(body)


# --- Diario de decisiones -----------------------------------------------------------
MAX_JOURNAL_BODY = 16 * 1024


def _today():
    """Fecha de hoy (los tests lo reemplazan)."""
    from datetime import date
    return date.today()


def _journal_inputs():
    """(last_prices, weights_pct, datos_al, sin_precio); (None, None, None, frozenset()) si no hay
    precios o libro. sin_precio: tickers con tenencia pero sin precio (dato faltante, no cero)."""
    from . import engine, stress
    dates, px, ops = _load_inputs()
    if not dates:
        return None, None, None, frozenset()
    last = stress.last_prices_from(px)
    port = engine.portfolio_series(dates, px, ops)
    if not port['dates']:
        return last, None, dates[-1], frozenset()
    positions, total, sin_precio = stress.current_weights(port['holdings_end'], last)
    weights = None if total is None else {p['ticker']: p['peso'] * 100 for p in positions}
    return last, weights, dates[-1], frozenset(sin_precio)


def _read_limited():
    """Lee a lo sumo MAX_JOURNAL_BODY+1 bytes (con o sin Content-Length). None si se pasa."""
    if (request.content_length or 0) > MAX_JOURNAL_BODY:
        return None
    raw = request.stream.read(MAX_JOURNAL_BODY + 1)
    return None if len(raw) > MAX_JOURNAL_BODY else raw


def _journal_json_body():
    if not request.is_json:
        return None, _err('tipo_contenido_invalido', 'Content-Type debe ser application/json', 415)
    raw = _read_limited()
    if raw is None:
        return None, _err('cuerpo_demasiado_grande', 'cuerpo demasiado grande', 413)
    try:
        body = json.loads(raw.decode('utf-8')) if raw else None
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return None, _err('cuerpo_invalido', 'el cuerpo debe ser un objeto JSON', 400, campo=None)
    return body, None


def _journal_corrupt(e):
    return _err('journal_corrupto', _redact(e), 200, status='journal_corrupto', entries=[],
                resumen=None, datos_al=None)


@lab_bp.route('/journal', methods=['GET'])
def journal_list():
    """Entradas + evaluacion calculada al leer (no escribe nada)."""
    from . import journal
    try:
        entries = journal.load_entries()
    except journal.JournalError as e:
        return _journal_corrupt(e)
    last, weights, datos_al, sin_precio = _journal_inputs()
    today, out = _today(), []
    for e in entries:
        out.append(dict(e, evaluacion=journal.evaluate(e, last, weights, today, datos_al, sin_precio)))
    return jsonify({'ok': True, 'status': 'ok' if last is not None else 'sin_precios',
                    'entries': out, 'resumen': journal.summarize(out), 'datos_al': datos_al})


@lab_bp.route('/journal', methods=['POST'])
def journal_create():
    from . import journal
    body, bad = _journal_json_body()
    if bad:
        return bad
    try:
        entry = journal.create_entry(body)
    except journal.JournalError as e:
        return _err('journal_corrupto', _redact(e), 409, status='journal_corrupto')
    except journal.LimitError as e:
        return _err('limite_alcanzado', _redact(e), 409, campo=e.campo)
    except journal.ValidationError as e:
        return _err('validacion', _redact(e), 400, campo=e.campo)
    return jsonify({'ok': True, 'entry': dict(entry, evaluacion=None)}), 201


@lab_bp.route('/journal/<entry_id>', methods=['PUT'])
def journal_update(entry_id):
    from . import journal
    body, bad = _journal_json_body()
    if bad:
        return bad
    try:
        entry = journal.update_entry(entry_id, body)
    except journal.JournalError as e:
        return _err('journal_corrupto', _redact(e), 409, status='journal_corrupto')
    except journal.ValidationError as e:
        return _err('validacion', _redact(e), 400, campo=e.campo)
    if entry is None:
        return _err('entrada_no_encontrada', 'Entrada no encontrada', 404)
    return jsonify({'ok': True, 'entry': entry})


HTTP_CODES = {400: 'solicitud_invalida', 404: 'no_encontrado', 405: 'metodo_no_permitido',
              409: 'conflicto', 413: 'cuerpo_demasiado_grande', 415: 'tipo_contenido_invalido'}


@lab_bp.errorhandler(Exception)
def _json_error(e):
    if isinstance(e, HTTPException):
        return _err(HTTP_CODES.get(e.code, 'error_http'), e.description, e.code)
    log.exception('Error en /api/lab: %s: %s', type(e).__name__, e)
    return _err('error_interno', 'Error interno del servidor', 500)


@lab_bp.app_errorhandler(404)
def _json_404(e):
    if request.path.startswith('/api/lab/'):
        return _err('ruta_no_encontrada', 'Ruta del Lab no encontrada', 404)
    return e


@lab_bp.app_errorhandler(405)
def _json_405(e):
    if request.path.startswith('/api/lab/'):
        return _err('metodo_no_permitido', 'Metodo no permitido en esta ruta del Lab', 405)
    return e


def page():
    return send_from_directory(UI_DIR, 'index.html')


# La página vive fuera del prefijo /api/lab: se registra con un Blueprint propio.
page_bp = Blueprint('lab_page', __name__)
page_bp.add_url_rule('/portfolio', 'portfolio_page', page)
page_bp.add_url_rule('/lab_ui/<path:filename>', 'lab_static',
                     lambda filename: send_from_directory(UI_DIR, filename))


def register(app):
    """Registra la API y la página del Lab en la app Flask."""
    app.register_blueprint(lab_bp)
    app.register_blueprint(page_bp)
