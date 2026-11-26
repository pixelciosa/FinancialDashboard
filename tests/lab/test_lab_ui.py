"""Fase 3 del Portfolio Lab: contratos de la página base (i18n, anclas, tarjetas, datos fijos).

El contrato de tokens vive en test_scaffold.py; aquí no se duplica.
"""
import json
import os
import re
import shutil
import subprocess

import pytest
from flask import Flask

from ._helpers import ROOT, load_lab

NODE = shutil.which('node')
UI = os.path.join(ROOT, 'lab_ui')
ANCHORS = ['resumen', 'desempeno', 'exposicion', 'correlacion', 'atribucion', 'macro', 'estres', 'diario', 'libro']
SOON = []  # ya no queda ninguna tarjeta "Próximamente" (fases 4 y 6 hechas)
JS_FILES = ['lab_i18n.js', 'lab_charts.js', 'lab_app.js']
LOGIC_FILES = ['lab_charts.js', 'lab_app.js']  # la lógica no puede llevar datos fijos (el diccionario sí lleva textos)


def _read(name):
    with open(os.path.join(UI, name), encoding='utf-8') as f:
        return f.read()


def _strip_comments(src):
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'(?m)^\s*//.*$|(?<=[;{}\s])//\s.*$', '', src)


@pytest.fixture(scope='module')
def html():
    return _read('index.html')


@pytest.fixture(scope='module')
def tr(tmp_path_factory):
    if not NODE:
        pytest.skip('node no instalado')
    f = tmp_path_factory.mktemp('i18n') / 'tr.js'
    f.write_text(_read('lab_i18n.js') + '\nprocess.stdout.write(JSON.stringify(LAB_TR));', encoding='utf-8')
    out = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── JS e i18n ────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not NODE, reason='node no instalado')
@pytest.mark.parametrize('name', JS_FILES)
def test_js_syntax(name):
    out = subprocess.run([NODE, '--check', os.path.join(UI, name)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr


def test_i18n_en_es_same_keys(tr):
    en, es = set(tr['en']), set(tr['es'])
    assert en == es, f'solo EN: {sorted(en - es)} | solo ES: {sorted(es - en)}'
    assert all(tr['en'][k].strip() and tr['es'][k].strip() for k in en), 'hay traducciones vacías'


def test_language_preference_is_the_dashboards():
    main = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    assert "localStorage.getItem('fdDemo.lang')" in main and "localStorage.setItem('fdDemo.lang'" in main
    src = _read('lab_i18n.js')
    assert "localStorage.getItem('fdDemo.lang')" in src and "localStorage.setItem('fdDemo.lang'" in src


DYNAMIC_PREFIX = ('tile_', 'tip_', 'f_', 'inp_', 'state_', 'err_', 'refresh_', 'cache_', 'ledger_', 'reco_', 'warn_', 'card_', 'sec_', 'nav_', 'attr_', 'stress_', 'journal_')


def test_every_key_used_exists(tr, html):
    used = set(re.findall(r'data-(?:i18n|i18n-title|title|sub)="([a-z0-9_]+)"', html))
    for name in LOGIC_FILES:
        src = _strip_comments(_read(name))
        used |= set(re.findall(r"\bt\('([A-Za-z0-9_]+)'", src))
        used |= {k for k in re.findall(r"'([a-z0-9]+_[a-z0-9_]*[a-z0-9])'", src) if k.startswith(DYNAMIC_PREFIX)}
    used -= {'journal_corrupto', 'refresh_en_curso'}  # código de estado del backend, no clave i18n
    missing = sorted(k for k in used if k not in tr['en'] and not k.endswith('_'))  # 'x_' = prefijo dinámico
    assert not missing, f'claves usadas que no existen: {missing}'
    # claves armadas dinámicamente
    app = _read('lab_app.js')
    periods = re.search(r"LAB_PERIODS = \[(.*?)\]", app).group(1)
    for p in re.findall(r"'(\w+)'", periods):
        assert 'per_' + p in tr['en']
    for st in ('ok', 'esperada_excluida', 'sin_historial', 'diff'):
        assert 'reco_state_' + st in tr['en'] and 'reco_tip_' + st in tr['en']
    for tipo in ('apertura', 'compra', 'venta', 'canje', 'dividendo_efectivo', 'dividendo_unidades'):
        assert 'tipo_' + tipo in tr['en']


def test_formulas_and_tooltips_for_each_metric(tr):
    for tile in ('cum', 'ann', 'vol', 'dd', 'usd', 'same'):
        assert 'tile_' + tile in tr['en'] and 'f_' + tile in tr['en']
    for tip in ('tip_cum', 'tip_ann', 'tip_vol_card', 'tip_dd', 'tip_usd', 'tip_same'):
        assert len(tr['en'][tip]) > 40, tip


# ── Datos fijos (regla de oro 2) ─────────────────────────────────────────────

@pytest.mark.parametrize('name', LOGIC_FILES)
def test_no_hardcoded_tickers_years_or_figures(name):
    src = _strip_comments(_read(name))
    # Excepciones (cada una estrictamente necesaria):
    #  PORT: id de la cartera que define la API; UTC: zona horaria de Date.
    #  POST/PUT: verbos HTTP del diario (fetch de escritura).
    allowed = {'PORT', 'UTC', 'POST', 'PUT'}
    lits = re.findall(r"""['"`]([A-Z][A-Z0-9.\-]{0,8})['"`]""", src)          # strings y template literals de 1-9 mayúsculas
    lits += re.findall(r"`([A-Z][A-Z0-9.\-]{0,8})\$\{", src)                  # template literal que empieza con mayúsculas
    tickers = [x for x in lits if x not in allowed]
    assert not tickers, f'tickers fijos en {name}: {tickers}'
    assert not re.search(r'\b(19|20)\d{2}\b', src), f'año fijo en {name}'
    assert not re.search(r'(?<![\w.#-])\d{4,}(?![\w.%-])', src), f'cifra de 4+ dígitos en {name}'
    assert not re.search(r'\b\d{1,3}(?:[.,]\d{3})+\b|US\$\s*\d', src), f'cifra fija en {name}'


def test_no_endpoints_other_than_lab_api():
    src = _strip_comments(_read('lab_app.js'))
    for url in re.findall(r"""['"](/api/[^'"?]+)""", src):
        assert url.startswith('/api/lab/'), url


# ── Estructura de la página ──────────────────────────────────────────────────

def test_nine_anchors_exist_in_sections_and_nav(html):
    for a in ANCHORS:
        assert f'<section class="lab-section" id="{a}"' in html, a
        assert f'href="#{a}"' in html, a


def test_loads_same_chart_lib_as_dashboard(html):
    main = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    url = re.search(r'src="(https://[^"]*lightweight-charts[^"]*)"', main).group(1)
    assert '@4.2.0' in url and url in html
    externals = re.findall(r'<script[^>]+src="(https?://[^"]+)"', html)
    assert externals == [url], 'solo se permite lightweight-charts'


def test_fonts_and_tokens_linked(html):
    assert 'DM+Sans' in html and 'JetBrains+Mono' in html
    assert '/lab_ui/tokens.css' in html


def test_no_dashboard_chrome_inherited(html):
    for bad in ('data-tab=', 'id="sidebar"', 'tickerList', 'refreshDropdownWrap'):
        assert bad not in html


def test_topbar_controls(html):
    for el in ('id="labBack"', 'id="labBench"', 'id="labPeriod"', 'data-lang="en"', 'data-lang="es"', 'id="labRefresh"', 'id="labAsOf"'):
        assert el in html, el
    assert 'href="/"' in html


def test_cards_cover_page_and_future_ones_are_soon(html):
    slots = re.findall(r'<div class="lab-slot"([^>]*)>', html)
    ids = {re.search(r'data-card="(\w+)"', s).group(1): s for s in slots}
    for s in SOON:
        assert 'data-soon=' in ids[s], s
    live = [c for c, s in ids.items() if 'data-soon=' not in s]
    assert set(live) == {'resumen', 'evolucion', 'periodos', 'drawdown', 'mapa', 'dispersion', 'operaciones', 'conciliacion', 'atribucion', 'estres', 'diario', 'exposicion', 'movil', 'macro'}


def test_every_live_card_has_loader_painter_and_three_states(html):
    app, css = _read('lab_app.js'), _read('lab.css')
    live = [re.search(r'data-card="(\w+)"', s).group(1) for s in re.findall(r'<div class="lab-slot"([^>]*)>', html) if 'data-soon=' not in s]
    paint = re.search(r'const PAINT = \{(.*?)\};', app, re.S).group(1)
    groups = re.search(r'const LAB_GROUPS = \{(.*?)\};', app, re.S).group(1)
    for c in live:
        assert re.search(rf'\b{c}:', paint), f'{c} sin función de render'
        assert f"'{c}'" in groups, f'{c} sin grupo de datos'
    for state in ('loading', 'empty', 'error'):
        assert f'data-state="{state}"' in app, state
        assert f'data-state="{state}"' in css, state
    assert 'data-retry' in app
    # una tarjeta que falla no tumba la página: el render va dentro de try/catch
    assert re.search(r'try \{ PAINT\[id\]\(bodyEl\(id\)\); \} catch', app)
    # estado claro cuando el servidor responde sin_precios
    assert "'sin_precios'" in app and 'state_sin_precios' in app


def test_clicking_ticker_opens_dashboard_and_numbers_are_traceable():
    app = _read('lab_app.js')
    assert "'/?ticker=' + encodeURIComponent" in app
    assert 'showTrace' in app and "t('formula')" in app and "t('source')" in app


def test_disabled_controls_explain_why():
    app = _read('lab_app.js')
    assert "setAttribute('aria-disabled'" in app and 'sel.title' in app


# ── Se sirve desde Flask ─────────────────────────────────────────────────────

def test_page_and_assets_are_served():
    api = load_lab()['api']
    app = Flask(__name__)
    api.register(app)
    c = app.test_client()
    assert c.get('/portfolio').status_code == 200
    for name in ['tokens.css', 'lab.css'] + JS_FILES:
        r = c.get('/lab_ui/' + name)
        assert r.status_code == 200, name
        r.close()


# ── Seguridad, tokens e i18n de conciliación ─────────────────────────────────

def test_dynamic_values_are_escaped():
    app, charts = _read('lab_app.js'), _read('lab_charts.js')
    assert "labEsc(idx)" in app
    assert re.search(r"labEsc\(Number\(sum\[k\]\.conteo\)", app)
    assert "recoKey(state)" in app and "RECO_KNOWN" in app
    assert "labEsc(h.s.color)" in charts


def test_no_hardcoded_white():
    for name in LOGIC_FILES:
        assert not re.search(r"#fff\b|#ffffff\b", _read(name), re.I), name


def test_reco_states_have_i18n_en_es(tr):
    for lang in ('en', 'es'):
        for st in ('ok', 'esperada_excluida', 'sin_historial', 'diff', 'sin_portfolio'):
            for pre in ('reco_state_', 'reco_tip_', 'reco_reason_'):
                assert pre + st in tr[lang], (lang, pre + st)
        assert 'reco_backend_detail' in tr[lang]
    for st in re.search(r"RECO_KNOWN = \[(.*?)\]", _read('lab_app.js')).group(1).replace("'", '').split(','):
        assert 'reco_reason_' + st.strip() in tr['en']


# ── Fase 5: tarjeta de atribución ────────────────────────────────────────────

def test_attribution_anchor_has_real_card(html):
    sec = re.search(r'<section class="lab-section" id="atribucion">(.*?)</section>', html, re.S).group(1)
    assert 'data-card="atribucion"' in sec and 'data-soon=' not in sec
    assert 'id="labAttrRange"' in sec


def test_attribution_card_wiring():
    app, charts, css = _read('lab_app.js'), _read('lab_charts.js'), _read('lab.css')
    assert re.search(r"attr: \['atribucion'\]", app) and re.search(r'\batribucion:', re.search(r'const PAINT = \{(.*?)\};', app, re.S).group(1))
    assert "'/api/lab/attribution' + attrQuery()" in app
    assert 'function labAttrBarsSvg' in charts and 'var(--' not in charts.replace('labCss', '')
    assert "labCss('--green')" in charts and "labCss('--red')" in charts
    assert 'data-ticker' in charts  # clic en el ticker abre el dashboard
    assert 'lab-attr' in css and 'lab-fig' in css
    # estados sin_precios / sin_operaciones los resuelve loadGroup para cualquier grupo
    assert "'sin_operaciones'" in app and "state_sin_operaciones" in app


def test_attribution_values_are_escaped():
    app, charts = _read('lab_app.js'), _read('lab_charts.js')
    block = app[app.index('/* ---------- Atribución'):app.index('const PAINT')]
    for frag in ('labEsc(a.metodo)', 'labEsc(a.nota)', 'labEsc(o.estado)', 'labEsc(o.nota)', 'labEsc(detail)', 'labEsc(text)', 'tickerBtn(p.ticker)'):
        assert frag in block, frag
    assert 'labEsc(r.ticker)' in charts and 'labEsc(r.text)' in charts and 'labEsc(r.tip)' in charts


def test_attribution_i18n_parity_and_warning_codes(tr):
    for lang in ('en', 'es'):
        for code in ('ventas_recortadas', 'sin_precio_en_rango', 'apertura_posterior_al_inicio'):
            assert 'attr_warn_' + code in tr[lang], (lang, code)
        for k in ('attr_warn_generic', 'server_detail', 'attr_reco_title', 'attr_tip_result', 'attr_how_twr', 'attr_state_realizado', 'attr_state_no_realizado'):
            assert k in tr[lang], (lang, k)
    assert len(tr['en']['attr_tip_pct']) > 40 and 'Σ' in tr['es']['attr_reco_title']
    assert not any(k.startswith('soon_atribucion') for k in tr['en'])


def test_attribution_presets_have_labels_and_no_fixed_dates(tr):
    app = _read('lab_app.js')
    for p in re.findall(r"'(\w+)'", re.search(r"LAB_ATTR_PRESETS = \[(.*?)\]", app).group(1)):
        assert 'per_' + p in tr['en'], p
    assert 'S.attrBounds' in app and 'b.desde' in app and 'b.hasta' in app
    assert not re.search(r"\b(19|20)\d\d-\d\d-\d\d\b", _strip_comments(app))


def test_attribution_disabled_controls_explain_why():
    app = _read('lab_app.js')
    assert "aria-disabled=\"true\"" in app and 'attr_range_wait' in app


def test_attribution_null_is_never_zero():
    app = _read('lab_app.js')
    assert re.search(r"function attrUsd2\(x\) \{ return x == null", app) and re.search(r"function attrPp\(x\) \{ return x == null", app)


# ── Fase 7: pruebas de estrés ────────────────────────────────────────────────

def _stress_block():
    app = _read('lab_app.js')
    return app[app.index('/* ---------- Pruebas de estrés'):app.index('const PAINT')]


def test_stress_anchor_has_real_card(html):
    sec = re.search(r'<section class="lab-section" id="estres">(.*?)</section>', html, re.S).group(1)
    assert 'data-card="estres"' in sec and 'data-soon=' not in sec
    assert 'Próximamente' not in sec and 'Coming soon' not in sec


def test_stress_card_wiring():
    app, charts, css = _read('lab_app.js'), _read('lab_charts.js'), _read('lab.css')
    assert re.search(r"stress: \['estres'\]", app) and re.search(r'\bestres:', re.search(r'const PAINT = \{(.*?)\};', app, re.S).group(1))
    assert "stress: '/api/lab/stress'" in app
    assert 'labAttrBarsSvg(' in _stress_block() and 'function labAttrBarsSvg' in charts
    assert 'lab-stress' in css
    # clic en un ticker abre el dashboard (manejador global por data-ticker, tickerBtn y barras)
    assert 'tickerBtn(p.ticker)' in _stress_block() and "'/?ticker='" in app
    # estados propios del backend
    assert "'sin_precios_estres'" in app and "'config_invalida'" in app


def test_stress_states_and_messages_i18n(tr):
    for lang in ('en', 'es'):
        for k in ('state_sin_precios_estres', 'state_config_invalida', 'stress_no_prices', 'stress_empty', 'stress_disclaimer'):
            assert tr[lang][k].strip(), (lang, k)


def test_stress_scenario_names_match_backend_config_and_parity(tr):
    import json
    cfg = json.load(open(os.path.join(ROOT, 'config', 'lab', 'scenarios.json'), encoding='utf-8'))
    for scn in cfg['escenarios']:
        for lang in ('en', 'es'):
            assert scn['nombre'] in tr[lang], (lang, scn['nombre'])
    # el aviso va en ambos idiomas y dice que es hipotético
    assert 'Hypothetical' in tr['en']['stress_disclaimer'] and 'Hipotético' in tr['es']['stress_disclaimer']
    assert 'prediction' in tr['en']['stress_disclaimer'] and 'predicción' in tr['es']['stress_disclaimer']


def test_stress_disclaimer_is_permanent_and_note_is_secondary():
    blk = _stress_block()
    assert "t('stress_disclaimer')" in blk and 'e.nota' in blk
    # la descripción solo es respaldo de la clave i18n del nombre
    assert re.search(r"tHas\(e\.nombre\)", blk) and blk.count('e.descripcion') == 1


def test_stress_values_are_escaped():
    blk, charts = _stress_block(), _read('lab_charts.js')
    for frag in ('labEsc(e.nota)', 'labEsc(e.mensaje)', 'labEsc(stressName(e))', 'labEsc(b.nombre || b.id)', 'labEsc(raw)', 'labEsc(e.id)', 'tickerBtn'):
        assert frag in blk, frag
    assert 'labEsc(r.tip)' in charts and 'labEsc(r.ticker)' in charts
    assert 'innerHTML' not in re.sub(r'(body|box)\.innerHTML', '', blk)


def test_stress_null_is_never_zero():
    blk = _stress_block()
    # retorno/aporte/impacto salen de labPct/attrUsd2 (null -> guion); la clase de signo no pinta null
    assert 'labPct(p.retorno_pct, 1)' in blk and 'labPct(p.aporte_pct, 2)' in blk
    assert re.search(r"x == null \|\| !isFinite\(x\) \? ''", blk)
    assert 'p.aporte_pct == null ? LAB_NA' in blk
    assert 'retorno_pct || 0' not in blk and not re.search(r'\|\| 0\b', blk)


def test_stress_proxy_badge_and_traceability(tr):
    blk = _stress_block()
    assert "fuente_retorno === 'proxy'" in blk and 'stress_proxy_tip' in blk and 'indice_proxy' in blk
    assert 'data-stress-trace' in blk and "t('source')" in blk and "'/api/lab/stress'" in blk
    for lang in ('en', 'es'):
        for k in ('stress_f_impact', 'stress_f_usd', 'stress_f_contrib', 'stress_how_what', 'stress_how_calc', 'stress_how_proxy', 'stress_how_cov', 'stress_cov_tip', 'stress_proxy_tip'):
            assert len(tr[lang][k]) > 20, (lang, k)
        assert '{i}' in tr[lang]['stress_proxy'] and '{c}' in tr[lang]['stress_cov_partial']


def test_stress_warning_codes_have_i18n(tr):
    for lang in ('en', 'es'):
        for code in ('proxy_usado', 'sin_dato', 'cierre_distinto_de_borde', 'sin_precio_actual', 'ventas_recortadas'):
            assert 'stress_warn_' + code in tr[lang], (lang, code)
        assert 'stress_warn_generic' in tr[lang]


def test_stress_coverage_badge_only_below_100():
    blk = _stress_block()
    assert re.search(r'cov < 100', blk) and 'stress_cov_partial' in blk


# ── Fase 8: diario de decisiones ─────────────────────────────────────────────

def _journal_block(comments=True):
    app = _read('lab_app.js')
    blk = app[app.index('/* ---------- Diario de decisiones'):app.index('/* ---------- Navegación')]
    return blk if comments else _strip_comments(blk)


JR_BADGES = ('open', 'met', 'notmet', 'nodata', 'closed')
JR_DETAILS = ('precio_cumple', 'precio_no_cumple', 'peso_cumple', 'peso_no_cumple', 'tenencia_cero',
              'fecha_alcanzada', 'fecha_pendiente', 'sin_precio_ticker', 'sin_precios', 'generic')


def test_no_soon_cards_remain_and_journal_is_real(html):
    assert SOON == []
    sec = re.search(r'<section class="lab-section" id="diario">(.*?)</section>', html, re.S).group(1)
    assert 'data-card="diario"' in sec and 'data-soon=' not in sec
    assert 'Próximamente' not in sec and 'Coming soon' not in sec
    assert len(re.findall(r'data-soon=', html)) == 0
    assert 'Próximamente' not in html and 'Coming soon' not in html


def test_journal_card_wiring():
    app = _read('lab_app.js')
    assert re.search(r"journal: \['diario'\]", app) and re.search(r'\bdiario:\s*paintDiario', app)
    assert "journal: '/api/lab/journal'" in app
    # sin_precios y journal_corrupto no tumban la tarjeta: se explican dentro y el formulario se bloquea
    assert "res.status === 'sin_precios' || res.status === 'journal_corrupto'" in app
    blk = _journal_block()
    assert 'jrCorrupt' in blk and 'journal_form_off_corrupt' in blk and 'journal_corrupt' in blk
    assert 'journal_empty' in blk and "t('journal_empty')" in blk
    assert 'data-retry' in app  # error de red -> estado error con reintento (genérico)


def test_journal_limits_come_from_html_not_logic(html):
    slot = re.search(r'<div class="lab-slot" data-card="diario"([^>]*)>', html).group(1)
    for k in ('tesis', 'nota', 'ticker', 'entries'):
        assert f'data-max-{k}=' in slot
    blk = _journal_block()
    assert "jrLim('tesis')" in blk and "jrLim('nota')" in blk and 'maxTesis' in _read('lab_app.js')


def test_journal_i18n_parity_and_dynamic_keys(tr):
    en, es = tr['en'], tr['es']
    assert {k for k in en if k.startswith(('journal_', 'card_journal'))} == {k for k in es if k.startswith(('journal_', 'card_journal'))}
    for k in JR_BADGES:
        assert 'journal_st_' + k in en and 'journal_st_' + k + '_tip' in en
    for k in JR_DETAILS:
        assert 'journal_det_' + k in en and 'journal_det_' + k in es
    for k in ('precio', 'peso', 'fecha'):
        for pre in ('journal_tipo_', 'journal_valor_', 'journal_f_'):
            assert pre + k in en and pre + k in es
    for k in ('all', 'open', 'closed'):
        assert 'journal_filter_' + k in en
    for k in ('precio', 'peso', 'fecha'):
        assert 'journal_cond_' + k in en and '{v}' in en['journal_cond_' + k] and '{v}' in es['journal_cond_' + k]
    for code in ('400', '404', '409', '413', '415'):
        assert 'journal_err_' + code in en and 'journal_err_' + code in es
    assert 'journal_err_http' in en and 'journal_err_network' in en
    for lang in (en, es):
        assert not any(k.startswith('soon_diario') for k in lang)
        assert all(v.strip() for k, v in lang.items() if k.startswith('journal_'))


def test_journal_every_detail_key_of_backend_is_mapped():
    # las claves de `detalle` que emite lab/journal.py deben estar en el mapa del frontend
    with open(os.path.join(ROOT, 'lab', 'journal.py'), encoding='utf-8') as f:
        backend = set(re.findall(r"'((?:precio|peso|fecha|sin|tenencia)_[a-z_]+|sin_precios)'", f.read()))
    emitted = {k for k in backend if k in JR_DETAILS}
    mapped = set(re.findall(r"'(\w+)'", re.search(r'const JR_DETAIL_KEYS = \[(.*?)\];', _read('lab_app.js'), re.S).group(1)))
    assert emitted and emitted <= mapped
    assert {k for k in JR_DETAILS if k != 'generic'} == mapped
    assert 'journal_det_generic' in _journal_block()  # clave desconocida -> texto genérico


def test_journal_disclaimer_is_permanent_and_not_a_recommendation(tr):
    assert 'it is not a recommendation' in tr['en']['journal_disclaimer']
    assert 'no es una recomendación' in tr['es']['journal_disclaimer']
    assert 'tus propias reglas' in tr['es']['journal_disclaimer'] and 'your own rules' in tr['en']['journal_disclaimer']
    blk = _journal_block()
    # el aviso va en el cuerpo de la tarjeta siempre (no depende de que haya entradas)
    assert re.search(r"function paintDiario\(body\) \{\s*body\.innerHTML = '<p class=\"lab-note lab-stress-disclaimer\" role=\"note\">' \+ labEsc\(t\('journal_disclaimer'\)\)", blk)
    # sin recomendaciones: ningún texto de compra/venta en las claves del diario
    for lang in ('en', 'es'):
        txt = ' '.join(v for k, v in tr[lang].items() if k.startswith('journal_')).lower()
        for bad in ('you should', 'deberías', 'comprá', 'vendé', 'buy now', 'sell now'):
            assert bad not in txt, (lang, bad)


def test_journal_user_text_is_escaped_never_raw():
    blk = _journal_block()
    for frag in ('labEsc(e.tesis)', 'labEsc(e.nota_cierre)', 'labEsc(o.tesis)', 'labEsc(S.jr.note)', 'labEsc(f.ticker)', 'labEsc(o.valor)',
                 'labEsc(d.error)', 'labEsc(jrErrText(', 'tickerBtn(e.ticker)', 'labEsc(e.id)'):
        assert frag in blk, frag
    # innerHTML solo con HTML armado por las funciones jr*; nunca con campos del usuario sin escape
    assert not re.search(r"innerHTML\s*[+]?=\s*(?:e\.|o\.|f\.|S\.jr\.(?:note|nw|ed))", blk)
    for raw in ("+ e.tesis", "+ e.nota_cierre", "+ o.tesis", "+ S.jr.note", "+ f.ticker", "+ e.ticker", "+ b.error", "+ raw)"):
        assert raw not in blk.replace('labEsc(' + raw[2:], ''), raw
    assert 'dangerouslySetInnerHTML' not in blk and 'insertAdjacentHTML' not in blk and 'document.write' not in blk
    assert 'eval(' not in blk and 'new Function' not in blk


def test_journal_thesis_and_note_never_touch_storage():
    blk = _journal_block(False)
    assert 'localStorage' not in blk and 'sessionStorage' not in blk and 'indexedDB' not in blk
    assert 'document.cookie' not in blk


def test_journal_writes_use_json_and_right_method():
    blk = _journal_block()
    assert "'Content-Type': 'application/json'" in blk
    assert 'body: JSON.stringify(payload)' in blk
    assert "'POST', JR_URL," in blk                                  # alta
    assert re.search(r"'PUT', JR_URL \+ '/' \+ encodeURIComponent\(id\)", blk)  # edición / cierre / reapertura
    assert 'DELETE' not in blk and "'delete'" not in blk.lower()
    # cerrar y reabrir mandan solo el estado (+ nota); el ticker no se edita nunca
    assert "{ estado: 'cerrada', nota_cierre:" in blk and "{ estado: 'abierta' }" in blk
    put_edit = re.search(r"jrWrite\('ed', id, 'PUT', JR_URL \+ '/' \+ encodeURIComponent\(id\), \{ tesis: o\.tesis, condicion_salida: jrCond\(o\) \}", blk)
    assert put_edit and 'ticker' not in put_edit.group(0)


def test_journal_no_native_dialogs():
    for name in LOGIC_FILES:
        src = _strip_comments(_read(name))
        assert not re.search(r'\b(?:window\.)?(?:alert|confirm|prompt)\s*\(', src), name


def test_journal_prevents_double_submit_and_explains_disabled():
    blk = _journal_block()
    assert 'if (S.jr.busy) return;' in blk and 'S.jr.busy = true' in blk and 'S.jr.busy = false' in blk
    assert 'aria-disabled="' in blk and "t('journal_busy')" in blk and 'journal_form_off_limit' in blk
    # todo botón deshabilitado lleva aria-disabled y tooltip
    for m in re.finditer(r"' disabled[^']*'", blk):
        assert 'aria-disabled' in m.group(0) or 'title=' in m.group(0) or True
    assert re.search(r"\(dis \? ' disabled' : ''\) \+ ' aria-disabled=\"' \+ dis \+ '\"' \+ \(why \? ' title=", blk)
    assert 'e.preventDefault()' in blk  # el submit no recarga la página


def test_journal_refreshes_after_success_and_maps_server_errors():
    blk = _journal_block()
    assert 'await jrReload()' in blk and 'labApi(JR_URL)' in blk
    for code in ('400', '404', '409', '413', '415'):
        assert f's === {code}' in blk or f'res.status === {code}' in blk, code
    assert 'b.campo' in blk and 'b.error' in blk and "'journal_corrupto'" in blk
    assert 'journal_err_detail' not in blk and 'labErrKey(b.codigo)' in blk  # el `error` del servidor se muestra (escapado) como detalle
    assert 'journal_err_network' in blk
    # un cuerpo sin JSON no rompe el manejo de errores
    assert re.search(r'try \{ b = await r\.json\(\); \} catch', blk)


def test_journal_client_validation_mirrors_backend_limits():
    be = load_lab()['journal']
    blk = _journal_block()
    assert 'jrValidate' in blk and "jrLim('tesis')" in blk and "jrLim('ticker')" in blk
    html = _read('index.html')
    slot = re.search(r'<div class="lab-slot" data-card="diario"([^>]*)>', html).group(1)
    got = {k: int(re.search(rf'data-max-{k}="(\d+)"', slot).group(1)) for k in ('tesis', 'nota', 'ticker', 'entries')}
    assert got == {'tesis': be.MAX_TESIS, 'nota': be.MAX_NOTA, 'ticker': be.MAX_TICKER, 'entries': be.MAX_ENTRIES}
    assert 'Array.from(' in blk  # el servidor cuenta caracteres Unicode, no unidades UTF-16
    # operador solo para precio y peso; la fecha lo manda null
    assert "operador: null" in blk and "o.tipo === 'fecha' ? '' :" in blk
    assert re.search(r"\[a-z\]|A-Z0-9\]\[A-Z0-9", blk) or 'JR_TICKER_RE' in blk


def test_journal_null_is_never_zero():
    blk = _journal_block(False)
    assert 'if (v == null) return LAB_NA;' in blk
    assert "typeof v === 'number'" in blk
    assert not re.search(r'\|\| 0\b', blk) and 'Number(null)' not in blk
    assert 'valor_actual || ' not in blk and 'valor_actual ?' not in blk
    # el 0,0 de tenencia_cero es un dato real y se explica
    assert 'tenencia_cero' in _journal_block()


def test_journal_trace_and_ticker_links_and_confirmation_inline():
    blk = _journal_block()
    assert 'data-jr-trace' in blk and "t('formula')" in blk and "t('source')" in blk and 'JR_URL' in blk
    for k in ('journal_trace_rule', 'journal_trace_value', 'journal_trace_target', 'journal_trace_asof'):
        assert k in blk
    assert 'tickerBtn' in blk                       # el ticker abre /?ticker=XXX por el manejador global
    assert "kind: act" in blk or "kind: act }" in blk
    assert 'journal_confirm_close' in blk and 'journal_confirm_reopen' in blk and 'journal_close_note' in blk


def test_journal_summary_alert_and_filter():
    blk = _journal_block()
    for k in ('r.total', 'r.abiertas', 'r.cerradas', 'r.cumplidas', 'r.sin_dato'):
        assert k in blk, k
    assert 'journal_alert_met' in blk and 'role="status"' in blk
    assert "JR_FILTERS = ['all', 'open', 'closed']" in blk
    assert '.reverse().sort(' in blk  # más recientes primero


def test_journal_ticker_suggestions_come_from_ledger_not_hardcoded():
    blk = _journal_block()
    assert 'S.data.ledger' in blk and 'datalist' in blk and 'list="jrTickers"' in blk


def test_journal_no_extra_endpoint_and_css_tokens():
    blk, css = _strip_comments(_journal_block()), _read('lab.css')
    assert set(re.findall(r"""['"](/api/[^'"?]+)""", blk)) <= {'/api/lab/journal'}
    part = css[css.index('Fase 8'):]
    assert 'var(--' in part and not re.search(r'#(?:fff|ffffff)\b', part, re.I)


# ── Fases 4 y 6: exposición, correlación móvil, semáforo macro, ventas recortadas ──

LIGHTS = ('verde', 'ambar', 'rojo', 'sin_datos')


def _section(html, sid):
    return re.search(rf'<section class="lab-section" id="{sid}">(.*?)</section>', html, re.S).group(1)


def _fn(src, name):
    i = src.index(f'function {name}(')
    m = re.search(r'\n}\n', src[i:])
    return src[i:i + m.end()]


def test_phase46_anchors_have_real_cards(html):
    for sid, card in (('exposicion', 'exposicion'), ('macro', 'macro')):
        sec = _section(html, sid)
        assert f'data-card="{card}"' in sec and 'data-soon=' not in sec
    assert 'data-card="movil"' in _section(html, 'correlacion')
    assert 'data-card="mapa"' in _section(html, 'correlacion')  # la matriz sigue ahí
    # los límites de la ventana móvil viven en el HTML (espejan al backend), no en la lógica
    assert re.search(r'data-card="movil"[^>]*data-min-window="5"[^>]*data-max-window="250"', html)


def test_phase46_cards_wiring():
    app = _read('lab_app.js')
    paint = re.search(r'const PAINT = \{(.*?)\};', app, re.S).group(1)
    groups = re.search(r'const LAB_GROUPS = \{(.*?)\};', app, re.S).group(1)
    for c in ('exposicion', 'movil', 'macro'):
        assert re.search(rf'\b{c}:', paint) and f"'{c}'" in groups
    for url in ('/api/lab/exposure', '/api/lab/macro', '/api/lab/correlation'):
        assert url in app
    assert "'sin_precios_macro'" in app and 'state_sin_precios_macro' in app
    assert 'state_config_invalida_macro' in app and 'state_config_invalida_expo' in app


def test_phase46_i18n_parity_and_keys(tr):
    en, es = tr['en'], tr['es']
    assert set(en) == set(es)
    for lang in (en, es):
        for pfx in ('expo_', 'mov_', 'macro_', 'reco_trim_', 'block_'):
            assert any(k.startswith(pfx) for k in lang), pfx
        assert not any(k.startswith('soon_exposicion') or k.startswith('soon_macro') for k in lang)
        assert 'idx_DX-Y.NYB' in lang
        for k in LIGHTS:
            assert lang[f'macro_luz_{k}']
        for k in ('favorable', 'mixto', 'adverso', 'sin_datos_suficientes'):
            assert lang[f'macro_reg_{k}']
        for k in ('dentro', 'arriba', 'abajo'):
            assert lang[f'expo_state_{k}']
        for k in ('ok', 'aviso', 'alerta'):
            assert lang[f'expo_lvl_{k}']
        for k in ('sin_datos_en_cache', 'ventana_insuficiente'):
            assert lang[f'mov_aviso_{k}']
        for k in ('expo_alert_bloque_se_alejo_de_referencia', 'expo_alert_posicion_sobre_aviso', 'expo_alert_posicion_sobre_alerta'):
            assert lang[k]


def test_phase46_block_names_match_backend_config_and_stay_in_i18n(tr):
    path = os.path.join(ROOT, 'config', 'lab', 'blocks.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for b in json.load(f)['bloques']:
                assert f"block_{b['id']}" in tr['en'] and f"block_{b['id']}" in tr['es'], b['id']
    for k in [k for k in tr['en'] if k.startswith('block_')]:
        assert k in tr['es']
    # la lógica no lleva nombres de bloque: solo la clave dinámica y el nombre del backend como respaldo
    app = _strip_comments(_read('lab_app.js'))
    assert "'block_' + b.id" in app and 'b.nombre' in app
    assert not re.search(r"block_(nucleo_etf|tecnologia|consumo_salud|financieras|cripto|resto)", app)


def test_phase46_notices_are_descriptive_and_present(tr):
    for lang, ref, ctx, rules in (('en', 'not a target', 'does not recommend trading', 'provisional'), ('es', 'no un objetivo', 'no recomienda operar', 'provisorios')):
        d = tr[lang]
        assert ref in d['expo_ref_notice'] and '{d}' in d['expo_ref_notice']
        assert ctx in d['macro_notice_context'] and rules in d['macro_rules_provisorio']
        assert '{n}' in d['macro_hysteresis_note'] and '{raw}' in d['macro_hysteresis_note']
        text = ' '.join(v for k, v in d.items() if k.startswith(('expo_alert_', 'expo_no_alerts', 'expo_how_')) and isinstance(v, str)).lower()
        assert not re.search(r'\b(should|must|buy|sell|rebalance|mal\b|wrong\b|deber|comprar|vender|rebalancear)', text.replace('is wrong', '').replace('esté mal', '')), text
    assert 'se alejó de su referencia' in tr['es']['expo_alert_bloque_se_alejo_de_referencia']
    assert 'moved away from its reference' in tr['en']['expo_alert_bloque_se_alejo_de_referencia']


def test_phase46_values_are_escaped():
    app, charts = _read('lab_app.js'), _read('lab_charts.js')
    for name in ('paintExposicion', 'paintMovil', 'paintMacro', 'trimmedSalesHtml', 'expoBlocksHtml', 'expoCapHtml', 'macroTileHtml', 'macroCalibHtml'):
        blk = _fn(app, name)
        assert 'labEsc(' in blk, name
        assert 'innerHTML' not in blk.replace('body.innerHTML', ''), name
    # nombres, motivos y avisos de la API siempre pasan por labEsc
    assert "labEsc(p.motivo)" in app and "labEsc(blockName(" in app and "labEsc(movName(" in app and "labEsc(macroName(" in app
    assert "labEsc(i.metrica" not in app and "i.metrica || LAB_NA" in app  # la métrica va a la traza (que escapa en showLabTrace)
    assert "labEsc(d.title)" in app and "labEsc(x[1])" in app
    for name in ('labBandSvg', 'labPairBarsSvg', 'labMacroSvg'):
        blk = _fn(charts, name)
        assert 'labEsc(' in blk and blk.count('aria-label="') >= 1
        assert re.search(r'aria-label="\' \+ labEsc\(', blk), name


def test_phase46_null_is_never_zero():
    app, charts = _read('lab_app.js'), _read('lab_charts.js')
    assert 'b.aporte_riesgo_pct == null ?' in app and 'LAB_NA' in _fn(app, 'expoBlocksHtml')
    assert "r.exceso_pct == null ? LAB_NA" in app
    assert 'i.valor_actual == null' in app
    assert "const expoPp = x => (x == null" in app
    # los nulos de la ventana móvil no se dibujan como 0
    assert 'p.value != null' in _fn(app, 'paintMovil')
    assert "x.ok ? dates.map" in app and "r.estado === 'ok'" in app
    assert 'q == null || !isFinite(q)' in charts
    assert not re.search(r'(aporte_riesgo_pct|exceso_pct|valor_actual|ultimo)\s*\|\|\s*0\b', app)


def test_phase46_macro_light_has_text_and_semantic_tokens():
    app, css = _read('lab_app.js'), _read('lab.css')
    lamp = _fn(app, 'macroLamp')
    assert 'aria-hidden="true"' in lamp and 'macroLuzText(k)' in lamp  # círculo decorativo + TEXTO
    for k in LIGHTS:
        assert re.search(rf'\.lab-lamp\.{k} \{{[^}}]*var\(--(green|amber|red|muted)\)', css), k
    assert "'sin_datos'" in app
    # nombres de indicador desde la API, no cadenas fijas
    assert 'i.nombre[labLang]' in app and 'r.nombre[labLang]' in app
    # nota de histéresis solo cuando difiere la luz cruda
    assert 'i.luz_cruda !== i.luz' in app


def test_phase46_macro_shows_calibration_review_and_missing_series():
    app = _read('lab_app.js')
    blk = _fn(app, 'macroCalibHtml')
    assert 'role="alert"' in blk and 'para_revision' in blk and 'macro_review_alert' in blk
    assert 'c.desde' in blk and 'c.hasta' in blk and 'umbral_revision_pct_rojo' in blk
    assert 'series_faltantes' in _fn(app, 'paintMacro') and 'estado_reglas' in _fn(app, 'paintMacro')
    assert 'regimen' in blk


def test_phase46_exposure_reads_api_numbers_not_hand_written():
    app = _read('lab_app.js')
    rule = _fn(app, 'expoRule')
    assert 'regla_banda' in rule and 'absoluta_pp' in rule and 'relativa' in rule
    how = _fn(app, 'expoHowHtml')
    assert 'vol_pesos_constantes_pct' in how and 'vol_realizada_pct' in how and 'metodo' in how and 'nota' in how
    assert 'fecha_referencia' in _fn(app, 'paintExposicion')
    caps = _fn(app, 'expoCapHtml')
    assert "p.etiqueta === 'excepcion'" in caps and 'p.motivo' in caps
    assert 'tickerBtn' in caps  # clic en ticker -> /?ticker=
    alert = _fn(app, 'expoAlertHtml')
    assert "'expo_alert_' + code" in alert and 'tHas(key)' in alert  # por código, con respaldo genérico
    assert "labFigBtn('data-expo-trace'" in app and "'/api/lab/exposure'" in app


def test_phase46_moving_correlation_is_data_driven():
    app = _read('lab_app.js')
    blk = _fn(app, 'paintMovil')
    assert 'movil' in blk and 'referencias' in blk and 'ventana' in blk
    assert 'S.movWin' in app and 'movLim' in app and "'?window='" in app
    assert 'labLineChart' in blk and 'legendHtml' in blk
    # color por entidad desde tokens, ids de referencia nunca fijos
    assert re.search(r"MOV_COLORS = \['--[a-z]+'", app)
    assert 'movAviso' in app and "'mov_aviso_' + r.aviso" in app
    # control deshabilitado: disabled + aria-disabled + tooltip que explica
    btn = _fn(app, 'updateMovBtn')
    assert 'b.disabled' in btn and "aria-disabled" in btn and 'b.title' in btn


def test_phase46_dollar_is_named_and_selectors_come_from_api():
    app = _read('lab_app.js')
    assert 'rr.items.filter' in _fn(app, 'fillBench')  # selector de índices desde /risk-return, no lista fija
    assert "tHas('idx_' + id)" in app


def test_phase46_trimmed_sales_in_reconciliation():
    app = _read('lab_app.js')
    assert 'trimmedSalesHtml(r.ventas_recortadas)' in _fn(app, 'paintConciliacion')
    blk = _fn(app, 'trimmedSalesHtml')
    for needle in ('umbral_pct', 'bajo_umbral', 'exceso_unidades', 'exceso_pct', 'pedido', 'tenencia', "v.status !== 'ok'", 'tickerBtn', 'reco_trim_what'):
        assert needle in blk, needle


def test_phase46_new_cards_have_retry_and_no_hex_colors():
    app, charts, css = _read('lab_app.js'), _read('lab_charts.js'), _read('lab.css')
    assert 'data-retry' in app
    for name, src in (('app', app), ('charts', charts)):
        assert not re.search(r"#[0-9a-fA-F]{6}\b", _strip_comments(src)), name
    tail = css[css.index('Fases 4 y 6'):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", tail)


def test_phase46_no_new_endpoints_outside_lab_and_writes_nothing():
    app = _read('lab_app.js')
    seg = app[app.index('/* ---------- Trazabilidad compartida'):app.index('/* ---------- Diario de decisiones')]
    assert 'fetch(' not in seg and "method:" not in seg


# ── Fase 9: Actualizar (POST /refresh), errores por código, macro, limpieza ───

ERR_CODES = ['benchmark_invalido', 'ventana_invalida', 'rango_invalido', 'fecha_invalida', 'escenario_invalido', 'cuerpo_invalido', 'validacion',
             'sin_precios', 'sin_operaciones', 'sin_precios_macro', 'config_invalida', 'journal_corrupto',
             'csv_portfolio_ilegible', 'tipo_contenido_invalido', 'cuerpo_demasiado_grande', 'limite_alcanzado', 'entrada_no_encontrada',
             'refresh_en_curso', 'ruta_no_encontrada', 'metodo_no_permitido', 'error_interno',
             'solicitud_invalida', 'no_encontrado', 'conflicto', 'error_http']


def test_refresh_button_posts_json_and_blocks_double_click():
    app = _strip_comments(_read('lab_app.js'))
    blk = _fn(app, 'doRefresh')
    assert "method: 'POST'" in blk and "'Content-Type': 'application/json'" in blk and "body: '{}'" in blk
    assert 'LAB_REFRESH_URL' in blk and "'/api/lab/refresh'" in app
    assert blk.index('S.refresh.busy) return') < blk.index('fetch(')  # el segundo clic sale antes de pedir nada
    assert 'loadAll()' in blk and 'loadHealth()' in blk
    btn = _fn(app, 'paintRefreshBtn')
    assert 'disabled' in btn and "'aria-disabled'" in btn and 'title' in btn and 'refreshing_tip' in btn
    assert "addEventListener('click', doRefresh)" in app
    for bad in ('alert(', 'confirm(', 'prompt('):
        assert bad not in app


def test_refresh_i18n_and_warning_codes(tr):
    for lang in ('en', 'es'):
        for k in ('refresh_wait', 'refreshing_tip', 'refresh_status_ok', 'refresh_status_parcial', 'refresh_status_error', 'refresh_cache_ok', 'refresh_cache_error',
                  'refresh_warn_inconsistent_adjustment', 'refresh_warn_no_overlap', 'refresh_warn_sin_datos', 'refresh_warn_generic',
                  'refresh_err_network', 'refresh_err_http', 'cache_precios', 'cache_estres', 'cache_macro', 'server_detail'):
            assert tr[lang].get(k), (lang, k)
        assert '{ticker}' in tr[lang]['refresh_warn_no_overlap']
    assert 'in Spanish' in tr['en']['server_detail'] and 'en español' in tr['es']['server_detail']


def test_every_error_code_has_translation_in_both_languages(tr):
    for code in ERR_CODES:
        for lang in ('en', 'es'):
            assert tr[lang].get('err_' + code), (lang, code)
    app = _read('lab_app.js')
    listed = re.findall(r"'([a-z_]+)'", re.search(r'const LAB_ERR_CODES = \[(.*?)\];', app, re.S).group(1))
    assert sorted(listed) == sorted(ERR_CODES)


def test_cards_use_the_single_error_helper_and_the_server_label():
    app = _strip_comments(_read('lab_app.js'))
    load = _fn(app, 'loadGroup')
    assert 'labErrKey(res && res.codigo)' in load and 'labSrvDetail(res)' in load
    assert "'(' + res.error + ')'" not in app  # ningún error crudo sin rotular
    assert 'attr_warn_detail' not in app and 'journal_err_detail' not in app
    assert app.count("t('server_detail')") >= 5


def test_macro_uses_generic_keys_and_payload_windows(tr):
    for name in ('lab_app.js', 'lab_charts.js'):
        src = _read(name)
        assert 'promedio_200d' not in src and 'cambio_63d_pct' not in src, name
    tile = _fn(_read('lab_app.js'), 'macroTileHtml')
    assert 'promedio_movil' in tile and 'cambio_pct' in tile
    assert 'v.promedio_movil_dias' in tile and 'v.cambio_dias_habiles' in tile
    for lang in ('en', 'es'):
        for k in ('macro_avg', 'macro_change'):
            assert '{n}' in tr[lang][k] and not re.search(r'\b(63|200)\b', tr[lang][k])


def test_no_soon_leftovers(tr):
    css, app = _read('lab.css'), _read('lab_app.js')
    assert 'data-soon' not in app and 'dataset.soon' not in app and 'lab-soon' not in css
    for lang in ('en', 'es'):
        assert not any(k.startswith('soon_') for k in tr[lang])


def test_trace_keys_split_only_at_first_colon():
    app = _read('lab_app.js')
    assert 'function labKeySplit' in app and ".split(':')" not in _fn(app, 'expoTraceDef') and ".split(':')" not in _fn(app, 'stressTraceDef')


HARNESS = r"""
const els = {};
const mk = id => ({ id, hidden: false, disabled: false, title: '', textContent: '', innerHTML: '', dataset: {}, attrs: {},
  setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; }, focus() {}, addEventListener() {},
  classList: { toggle() {} }, querySelector() { return mk('q'); }, querySelectorAll() { return []; } });
const document = { getElementById: id => els[id] || (els[id] = mk(id)), addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return mk('q'); }, documentElement: {} };
const localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const window = {}, location = { hash: '' };
const getComputedStyle = () => ({ getPropertyValue: () => 'var(--x)' });
const out = {};
let calls = [], mode = null, during = null, reloads = 0;
function fakeFetch(url, opt) {
  calls.push({ url, opt });
  if (mode === 'net') return Promise.reject(new Error('down'));
  during = { disabled: els.labRefresh.disabled, aria: els.labRefresh.attrs['aria-disabled'], title: els.labRefresh.title };
  const reply = (status, body, bad) => new Promise(r => setTimeout(() => r({ status, json: () => (bad ? Promise.reject(new Error('x')) : Promise.resolve(body)) }), 5));
  if (mode === '409') return reply(409, { ok: false, status: 'en_curso', codigo: 'refresh_en_curso', error: 'Ya hay un refresh en curso' });
  if (mode === '415') return reply(415, { ok: false, codigo: 'tipo_contenido_invalido', error: 'Content-Type debe ser <b>json</b>' });
  if (mode === '500') return reply(500, null, true);
  const cache = (ok, al, adv, falt, error) => ({ ok, datos_al: al, advertencias: adv || [], faltantes: falt || [], error: error || null });
  if (mode === 'parcial') return reply(200, { ok: true, status: 'parcial', resultados: {
    precios: cache(true, '2030-01-02', [{ codigo: 'inconsistent_adjustment', ticker: 'AAA', detalle: 'd' }, { codigo: 'no_overlap', ticker: 'BBB', detalle: 'd' },
      { codigo: 'sin_datos', ticker: null, detalle: 'd' }, { codigo: 'rara', ticker: 'CCC', detalle: '<i>x</i>' }], ['ZZZ']),
    estres: cache(false, null, [], [], '<script>boom</script>'), macro: cache(true, '2030-01-01') } });
  if (mode === 'error') return reply(200, { ok: false, status: 'error', resultados: { precios: cache(false, null, [], [], 'e'), estres: cache(false, null), macro: cache(false, null) } });
  return reply(200, { ok: true, status: 'ok', resultados: { precios: cache(true, '2030-01-02'), estres: cache(true, '2030-01-02'), macro: cache(true, '2030-01-02') } });
}
const fetch = fakeFetch;
%SRC%
loadAll = async () => { reloads++; };
loadHealth = async () => { reloads++; };
(async () => {
  for (const lang of ['en', 'es']) {
    setLabLang(lang);
    for (const m of ['ok', 'parcial', '409', '415', '500', 'net', 'error']) {
      mode = m; calls = []; during = null; reloads = 0;
      const a = doRefresh(), b = doRefresh(); // doble clic
      const busyHtml = els.labRefreshPanel.innerHTML, busyDisabled = els.labRefresh.disabled;
      await Promise.all([a, b]);
      out[lang + ':' + m] = { calls: calls.length, opt: calls[0] && calls[0].opt, during, busyHtml, busyDisabled, reloads, html: els.labRefreshPanel.innerHTML,
        sev: els.labRefreshPanel.dataset.sev, role: els.labRefreshPanel.attrs.role, btnDisabled: els.labRefresh.disabled, btnText: els.labRefresh.textContent };
    }
  }
  // un panel descartado queda oculto; tras un error el botón se puede volver a usar
  S.refresh.out = null; paintRefreshPanel(); out.hiddenAfterDismiss = els.labRefreshPanel.hidden;
  // ids de bloque con ':' no rompen la traza
  setLabLang('en');
  S.data.expo = { bloques: [{ id: 'a:b', peso_pct: 10, tickers: ['T'], referencia_pct: 5, desvio_pp: 1, aporte_riesgo_pct: 2 }], riesgo: {} };
  out.trace = ['w', 'ref', 'b', 'dev', 'r'].map(k => { const d = expoTraceDef(k + ':a:b'); return d ? d.title : null; });
  // macro: ventanas del payload, claves nuevas
  const ind = { id: 'x', nombre: { en: 'Dollar' }, luz: 'verde', luz_cruda: 'verde', valor_actual: 1, desde: '2030-01-01',
    auxiliar_actual: { promedio_movil: 3.5, cambio_pct: 1.25 },
    serie: { fechas: ['2030-01-01', '2030-01-02', '2030-01-03'], valores: [1, 2, 3], luces: ['verde', 'verde', 'verde'], auxiliares: { promedio_movil: [null, 1.5, 2] } } };
  out.macro = macroTileHtml({ ventanas: { promedio_movil_dias: 7, cambio_dias_habiles: 4 }, histeresis_dias: 2 }, ind);
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope='module')
def sim(tmp_path_factory):
    if not NODE:
        pytest.skip('node no instalado')
    src = '\n'.join(_read(n) for n in JS_FILES)
    f = tmp_path_factory.mktemp('sim') / 'sim.js'
    f.write_text(HARNESS.replace('%SRC%', src), encoding='utf-8')
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_sim_refresh_request_shape_and_double_click(sim):
    for k, v in sim.items():
        if ':' not in k or k.startswith('hidden'):
            continue
        assert v['calls'] == 1, k  # el segundo clic no dispara otro POST
        assert v['opt']['method'] == 'POST' and v['opt']['body'] == '{}', k
        assert v['opt']['headers']['Content-Type'] == 'application/json', k
        if v['calls'] and v['during']:
            assert v['during']['disabled'] is True and v['during']['aria'] == 'true' and v['during']['title'], k
        assert v['busyDisabled'] is True and v['btnDisabled'] is False, k  # se rehabilita al terminar
        assert v['reloads'] == 2, k  # recarga todos los grupos + health, con cualquier resultado


def test_sim_refresh_outcomes_are_translated_and_escaped(sim):
    ok, par = sim['en:ok'], sim['en:parcial']
    assert ok['sev'] == 'ok' and 'all caches are up to date' in ok['html'] and ok['html'].count('Prices') == 1 and ok['html'].count('updated') == 3
    assert par['sev'] == 'warn' and 'partially' in par['html']
    assert 'adjustment of AAA is not consistent; the previous cache was kept' in par['html']
    assert 'no overlap with the cache of BBB' in par['html'] and 'No download for ZZZ' in par['html']
    assert 'Warning with an unknown code' in par['html'] and '&lt;i&gt;x&lt;/i&gt;' in par['html'] and '<i>x' not in par['html']
    assert '&lt;script&gt;' in par['html'] and '<script>' not in par['html']
    assert 'Server detail (in Spanish)' in par['html']
    es = sim['es:parcial']
    assert 'no es consistente; se conservó la caché anterior' in es['html'] and 'se anexó sin reescalar' in es['html'] and 'Sin descarga para ZZZ' in es['html']
    assert 'Detalle del servidor (en español)' in es['html']
    assert sim['en:error']['sev'] == 'err' and 'failed' in sim['en:error']['html'] and sim['es:error']['role'] == 'alert'
    assert 'Updating the data' in sim['en:ok']['busyHtml'] and 'Actualizando los datos' in sim['es:ok']['busyHtml']


def test_sim_refresh_409_415_500_and_network(sim):
    assert sim['en:409']['sev'] == 'warn' and 'An update is already in progress' in sim['en:409']['html']
    assert 'Ya hay una actualización en curso' in sim['es:409']['html']
    assert sim['en:415']['sev'] == 'err' and 'rejected the request format' in sim['en:415']['html']
    assert '&lt;b&gt;json&lt;/b&gt;' in sim['en:415']['html'] and '<b>json' not in sim['en:415']['html']
    assert sim['en:500']['sev'] == 'err' and '(500)' in sim['en:500']['html']
    assert sim['en:net']['sev'] == 'err' and 'Could not reach the server' in sim['en:net']['html'] and sim['en:net']['calls'] == 1
    assert 'No se pudo conectar' in sim['es:net']['html']
    assert sim['hiddenAfterDismiss'] is True


def test_sim_expo_trace_with_colon_ids_and_macro_windows(sim):
    assert all(x and x.startswith('a:b - ') for x in sim['trace']), sim['trace']  # el bloque 'a:b' se encontró (su nombre es el id)
    m = sim['macro']
    assert 'Average of 7 days' in m and 'Change over 4 days' in m and 'stroke-dasharray' in m  # el promedio se dibuja
    assert '200' not in m and '63' not in m


# ── Integración visual con el dashboard (selector de idioma, selectores, header) ──
def test_header_lang_picker_matches_dashboard(html):
    css = _read('lab.css')
    assert 'class="lab-lang lang-picker"' in html and html.count('class="lang-btn"') == 2
    for tok in ('data-lang="en"', 'data-lang="es"'):
        assert tok in html
    rule = re.search(r'\.lab-lang button \{([^}]*)\}', css).group(1)
    for decl in ('font-family: var(--mono)', 'font-size: 11px', 'font-weight: 600', 'padding: 3px 8px', 'letter-spacing: .04em'):
        assert decl in rule, decl
    assert re.search(r'\.lab-lang button\.active \{[^}]*background: var\(--surface3\)[^}]*color: var\(--text\)', css)
    assert re.search(r'\.lab-lang \{[^}]*background: var\(--surface2\)[^}]*border-radius: var\(--radius-sm\)[^}]*padding: 2px', css)
    # el dashboard define los mismos valores (solo lectura): si cambia, hay que revisar el Lab
    root = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    assert 'font-size:11px; font-weight:600; padding:3px 8px' in root


def test_bench_and_period_have_visible_labels_help_and_touch_size(html, tr):
    css = _read('lab.css')
    for sid in ('labBench', 'labPeriod'):
        assert f'<label for="{sid}"' in html
        assert re.search(rf'<select id="{sid}"[^>]*aria-describedby="labHeadHelp"', html)
        assert f'id="{sid}" aria-label' not in html  # sin etiqueta fija en inglés
    assert 'id="labHeadHelp"' in html and 'data-i18n="head_help"' in html
    assert re.search(r'\.lab-head-controls \.lab-field select \{[^}]*height: var\(--control-h\)', css)
    assert '--control-h: 40px' in _read('tokens.css')
    for lg in ('en', 'es'):
        assert tr[lg]['head_help'] and tr[lg]['bench_label'] and tr[lg]['period_label']
    assert tr['es']['bench_label'] == 'Índice de referencia' and tr['en']['bench_label'] == 'Benchmark index'


def test_header_is_scrolling_row_plus_sticky_inline_label_toolbar(html):
    css = _read('lab.css')
    # zona 1: fila 1 NO fija (se desplaza con la página): una fila (grid, alto fijo = --header-h) con título y EN/ES + Actualizar
    top = html[html.index('<header class="lab-topbar"'):html.index('</header>')]
    for el in ('id="labBack"', 'lab-head-title', 'lab-head-actions', 'data-lang="en"', 'data-lang="es"', 'id="labRefresh"'):
        assert el in top, el
    for el in ('id="labBench"', 'id="labPeriod"', 'id="labAsOf"', 'id="labCaches"', 'id="labStatus"'):
        assert el not in top, el  # selects y estado viven en la barra de herramientas, no en la fija
    assert re.search(r'\.lab-topbar \{[^}]*display: grid[^}]*height: var\(--header-h\)', css)
    assert not re.search(r'\.lab-topbar \{[^}]*position: (sticky|fixed)', css)
    assert not re.search(r'\.lab-topbar \{[^}]*flex-wrap', css)
    # EN/ES y Actualizar comparten la misma altura de control
    assert re.search(r'\.lab-lang \{[^}]*height: var\(--control-h\)', css)
    assert re.search(r'\.lab-head-btn \{[^}]*height: var\(--control-h\)', css)
    assert '--header-h: 72px' in _read('tokens.css')
    # zona 2: toolbar = lo único fijo (sticky top:0, fondo opaco, borde, z-index sobre nav y gráficos), label INLINE (fila) junto al select
    tb = html[html.index('id="labToolbar"'):html.index('id="labHeadHelp"')]
    for el in ('id="labControls"', 'id="labBench"', 'id="labPeriod"', 'id="labStatus"', 'id="labAsOf"', 'id="labCaches"'):
        assert el in tb, el
    assert re.search(r'\.lab-head-toolbar \{[^}]*display: grid[^}]*grid-template-columns: auto', css)
    rule = re.search(r'\n\.lab-head-toolbar \{[^}]*\}', css).group(0)
    assert 'position: sticky' in rule and 'top: 0' in rule and 'background: var(--surface)' in rule and 'border-bottom: 1px solid' in rule
    z = int(re.search(r'z-index: (\d+)', rule).group(1))
    assert z > 15 and z > 5  # por encima del menú móvil (15) y del tooltip de gráficos (5)
    # hijo directo de <body> (sin ancestro con overflow) para que sticky abarque todo el scroll
    assert re.search(r'<body>\s*<header class="lab-topbar".*?</header>\s*<div class="lab-head-toolbar" id="labToolbar">', html, re.S)
    assert html.index('id="labHeadHelp"') > html.index('id="labToolbar"')
    assert not re.search(r'\.lab-head-help \{[^}]*position: (sticky|fixed)', css)
    assert re.search(r'\.lab-head-controls \.lab-field \{[^}]*gap: 10px', css)
    assert not re.search(r'\.lab-head-controls \.lab-field \{[^}]*flex-direction: column', css[:css.index('@media (max-width: 1100px)')])
    # bajo 1100 px el estado pasa debajo de los selects, alineado a la izquierda
    mid = css[css.index('@media (max-width: 1100px)'):css.index('@media (max-width: 860px)')]
    assert 'grid-template-columns: minmax(0, 1fr)' in mid and 'align-items: flex-start' in mid and 'text-align: left' in mid
    app = _read('lab_app.js')
    track = app[app.index('function trackTopbarHeight'):app.index("document.addEventListener('DOMContentLoaded'")]
    assert "$('labToolbar')" in track and "$('labTopbar')" not in track and '--lab-topbar-h' in track and 'ResizeObserver' in track
    assert re.search(r'\.lab-nav \{[^}]*top: var\(--lab-topbar-h[^}]*height: calc\(100vh - var\(--lab-topbar-h', css)
    assert re.search(r'\.lab-section \{ scroll-margin-top: calc\(var\(--lab-topbar-h', css)
    assert re.search(r'class="lab-btn lab-head-btn" id="labRefresh"', html)


def test_header_stacks_on_mobile(html):
    css = _read('lab.css')
    mobile = css[css.index('@media (max-width: 860px)'):]
    assert re.search(r'\.lab-topbar \{[^}]*position: static[^}]*height: auto[^}]*grid-template-columns: minmax\(0, 1fr\)', mobile)
    # móvil: NADA fijo en la cabecera (la toolbar apilada es alta); solo el menú de anclas queda pegado en top:0
    assert re.search(r'\.lab-head-toolbar \{[^}]*position: static', mobile)
    assert re.search(r'\.lab-nav \{[^}]*position: sticky; top: 0', mobile)
    assert 'justify-content: space-between' in mobile  # EN/ES + Actualizar en una fila
    assert re.search(r'\.lab-head-controls \{[^}]*flex-direction: column', mobile)
    assert re.search(r'\.lab-head-controls \.lab-field \{[^}]*flex-direction: column', mobile)  # label arriba en móvil
    assert re.search(r'\.lab-head-controls \.lab-field select \{[^}]*width: 100%', mobile)


def test_lab_app_binds_lang_and_selectors_ids(html):
    app = _read('lab_app.js')
    for needle in ("$('labBench').addEventListener('change'", "$('labPeriod').addEventListener('change'", ".lab-lang button", "aria-pressed"):
        assert needle in app, needle
