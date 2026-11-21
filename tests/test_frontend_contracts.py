"""
Contratos entre index.html y el backend, y salud del JS embebido.

Atrapa roturas silenciosas típicas de un archivo HTML de 300 KB editado por agentes:
claves de traducción faltantes, rutas de API que ya no existen, JS con errores de
sintaxis, tabs sin panel, o datos financieros escritos a mano.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

NODE = shutil.which('node')


def _script_body(html):
    start = html.index('<script>', html.index('lightweight-charts')) + len('<script>')
    return html[start:html.index('</script>', start)]


def _translations(html, tmp_path):
    start = html.index('const TRANSLATIONS = {')
    end = html.index('\n};', start) + 3
    src = html[start:end] + '\nprocess.stdout.write(JSON.stringify(TRANSLATIONS));'
    f = tmp_path / 'tr.js'
    f.write_text(src, encoding='utf-8')
    out = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── JavaScript ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_inline_js_has_no_syntax_errors(index_html, tmp_path):
    f = tmp_path / 'app.js'
    f.write_text(_script_body(index_html), encoding='utf-8')
    out = subprocess.run([NODE, '--check', str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f'Error de sintaxis en el JS de index.html:\n{out.stderr[:2000]}'


def test_external_scripts_are_pinned(index_html):
    for src in re.findall(r'<script src="([^"]+)"', index_html):
        assert re.search(r'@\d+\.\d+\.\d+', src), f'Script sin versión fija: {src}'


# ── i18n ────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_translation_languages_have_same_keys(index_html, tmp_path):
    tr = _translations(index_html, tmp_path)
    en, es = set(tr['en']), set(tr['es'])
    assert not (en - es), f'Claves solo en EN: {sorted(en - es)}'
    assert not (es - en), f'Claves solo en ES: {sorted(es - en)}'


@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_every_used_key_exists(index_html, tmp_path):
    tr = _translations(index_html, tmp_path)
    used = set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', index_html))
    used |= set(re.findall(r"\bt\('([a-z0-9_]+)'\)", index_html))
    missing = sorted(k for k in used if k not in tr['en'])
    assert not missing, f'Claves usadas pero no definidas: {missing}'
    # t() devuelve la clave cruda si no existe: el fallback `t(k) || '...'` nunca se usa.
    # (El caso 'compare_min_error' desapareció con el modal Comparar.)


@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_dynamic_keys_of_new_panels_exist(index_html, tmp_path):
    """Claves armadas en JS (prefijo + código del servidor) para el registro y el chat."""
    import valuations
    tr = _translations(index_html, tmp_path)
    keys = [f'val_group_{g}' for g in valuations.GROUPS]
    keys += [f'val_reason_{r}' for r in ('etf', 'crypto', 'no_data', 'insufficient_valuation',
                                         'insufficient_coverage', 'not_applicable')]
    keys += [f'metric_{k}' for k in valuations.VALUATION_KEYS] + [f'mtip_{k}' for k in valuations.VALUATION_KEYS]
    keys += [f'cat_{c}' for c in ('portfolio', 'watching', 'archived')] + [f'kind_{k}' for k in ('stock', 'etf', 'crypto')]
    keys += [f'chat_sug_{i}' for i in (1, 2, 3)]
    for lang in ('en', 'es'):
        missing = [k for k in keys if k not in tr[lang]]
        assert not missing, f'{lang}: {missing}'


@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_no_empty_translations(index_html, tmp_path):
    tr = _translations(index_html, tmp_path)
    for lang, d in tr.items():
        empty = [k for k, v in d.items() if isinstance(v, str) and not v.strip()]
        assert not empty, f'{lang}: traducciones vacías {empty}'


# ── Contrato con el backend ─────────────────────────────────────────────────

def _route_regexes(server_module):
    out = []
    for rule in server_module.app.url_map.iter_rules():
        pat = re.sub(r'<[^>]+>', '[^/]+', rule.rule)
        out.append(re.compile('^' + pat + '$'))
    return out


def test_every_api_call_has_a_route(index_html, server_module):
    calls = set(re.findall(r"""['"`](/api/[^'"`?\s]*)""", index_html))
    routes = _route_regexes(server_module)
    broken = []
    for c in calls:
        probe = re.sub(r'\$\{[^}]+\}', 'X', c)
        if probe.endswith('/'):
            probe += 'X'
        if not any(r.match(probe) for r in routes):
            broken.append(c)
    assert not broken, f'El frontend llama rutas que el servidor no tiene: {sorted(broken)}'


# ── Estructura de la UI ─────────────────────────────────────────────────────

def test_every_tab_button_has_a_panel(index_html):
    tabs = set(re.findall(r'class="tab-btn[^"]*" data-tab="([^"]+)"', index_html))
    assert tabs, 'No se encontraron tabs'
    for tab in tabs:
        assert f'id="tab-{tab}"' in index_html, f'La tab "{tab}" no tiene panel'


def test_ids_used_by_status_pills_exist(index_html):
    for pill in ('h-status', 'h2-status', 'tech-status', 'fin-status'):
        assert pill in index_html


def test_html_ids_unique(index_html):
    static_html = index_html[:index_html.index('<script>', index_html.index('lightweight-charts'))]
    ids = re.findall(r'\sid="([^"$]+)"', static_html)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f'IDs duplicados en el HTML: {dupes}'


# ── Tab financiera (Fase 3): sin datos fijos, todo desde la API ─────────────

def test_no_hardcoded_financial_data(index_html):
    m = re.search(r'const FD\s*=\s*\{(.*?)\n\};', index_html, re.S)
    body = m.group(1) if m else ''
    assert not re.search(r'^\s*[A-Z]{1,5}\s*:\s*\{', body, re.M), 'FD contiene tickers fijos'


def test_no_hardcoded_fiscal_years(index_html):
    assert "const YEARS  = ['FY21'" not in index_html and 'const YEARS = [' not in index_html


def _fn_body(html, signature):
    start = html.index(signature)
    nxt = html.find('\nfunction ', start + 10)
    nxt_async = html.find('\nasync function ', start + 10)
    ends = [e for e in (nxt, nxt_async) if e > 0]
    return html[start:min(ends) if ends else len(html)]


def test_no_hardcoded_years_or_periods_in_fin_code(index_html):
    for frag in ("'FY2025'", 'FY21–FY25', "FY21-FY25", "· FY2025"):
        assert frag not in index_html, f'Período fijo en el frontend: {frag}'


def test_financial_tab_has_no_ai_report_fallback(index_html):
    body = _fn_body(index_html, 'async function renderFinancialTab(')
    assert 'DYNAMIC_REPORTS' not in body, 'La tab financiera no debe usar los reportes de IA como datos'
    assert "fetch('/api/financials/'" in body


def test_single_refresh_goes_through_server_gate(index_html):
    start = index_html.index('window.refreshFinancialSingle')
    body = index_html[start:index_html.index('window.refreshFinancialData', start)]
    assert "/refresh`, { method: 'POST' }" in body
    assert 'res.status === 409' in body
    assert '?force=true' not in body


def test_refresh_button_disabled_unless_new_data(index_html):
    body = _fn_body(index_html, 'function renderFinFreshness(')
    assert "f.state === 'available'" in body
    assert "' disabled aria-disabled=\"true\"'" in body
    assert 'btnRefreshSingleFin' in body and 'fin-status' in index_html


def test_menu_item_starts_disabled_and_counts_new_quarters(index_html):
    assert 'id="btnRefreshFinancialAll"' in index_html
    tag = index_html[index_html.index('id="btnRefreshFinancialAll"') - 80:index_html.index('id="btnRefreshFinancialAll"') + 200]
    assert 'disabled aria-disabled="true"' in tag
    body = _fn_body(index_html, 'async function updateFinancialMenu(')
    assert "fetch('/api/financials/status')" in body and "=== 'available'" in body


def test_iso_dates_parsed_as_local(index_html):
    """new Date('2027-01-28') es UTC: en Buenos Aires se mostraría el 27."""
    body = _fn_body(index_html, 'function finDate(')
    assert 'new Date(y, m - 1, d)' in body


def test_server_strings_are_escaped(index_html):
    body = _fn_body(index_html, 'function renderFinancialDash(')
    assert '${finEsc(d.name)}' in body and '${d.name}' not in body


@pytest.mark.skipif(not NODE, reason='node no instalado')
@pytest.mark.parametrize('dpr', [1, 2, 3])
def test_canvas_size_stable_across_redraws(index_html, tmp_path, dpr):
    """Cada hover redibuja el gráfico. En pantallas Retina el canvas crecía x2 por redibujo
    hasta quedar en blanco (superaba el tamaño máximo de canvas)."""
    body = _fn_body(index_html, 'function setupCanvas(')
    src = f"""
const window = {{ devicePixelRatio: {dpr} }};
{body}
const attrs = {{ height: '180' }};
const canvas = {{ height: 180, width: 300, dataset: {{}}, style: {{}},
  parentElement: {{ clientWidth: 500 }},
  getAttribute: k => attrs[k], getContext: () => ({{ scale() {{}} }}) }};
const out = [];
for (let i = 0; i < 6; i++) {{ setupCanvas(canvas); out.push([canvas.style.height, canvas.height]); }}
process.stdout.write(JSON.stringify(out));
"""
    f = tmp_path / 'canvas.js'
    f.write_text(src, encoding='utf-8')
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    sizes = json.loads(res.stdout)
    assert all(s == ['180px', 180 * dpr] for s in sizes), sizes


def test_risk_band_widths_match_backend_levels(index_html):
    """La escala visual usa los mismos cortes que risk.LEVELS (30 / 45 / 60)."""
    import re
    import risk
    m = re.search(r"const RISK_BANDS = (\[.*?\]);", index_html)
    bands = json.loads(m.group(1).replace("'", '"'))
    edges, acc = [], 0
    for _, w in bands[:-1]:
        acc += w
        edges.append(acc)
    assert edges == [lim for lim, _ in risk.LEVELS[:-1]]
    assert sum(w for _, w in bands) == 100


def test_risk_gauge_gradient_spans_full_scale(index_html):
    """El color del arco depende del valor (0 a 100), no del largo del arco."""
    assert 'id="garc-risk" gradientUnits="userSpaceOnUse" x1="30"' in index_html


# ── Portfolio Agent y registro de valuaciones ───────────────────────────────

def _chat_render_src(html):
    parts = [_fn_body(html, 'function finEsc('), _fn_body(html, 'function chatInline('),
             _fn_body(html, 'function renderChatMarkdown(')]
    return '\n'.join(parts)


@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_chat_markdown_escapes_model_text(index_html, tmp_path):
    """El texto del modelo pasa por finEsc antes de cualquier etiqueta: nunca se inyecta HTML."""
    evil = '**hola** <img src=x onerror=alert(1)>\n- item <script>x</script>\n| A | B |'
    src = _chat_render_src(index_html) + f"\nprocess.stdout.write(renderChatMarkdown({json.dumps(evil)}));"
    f = tmp_path / 'md.js'
    f.write_text(src, encoding='utf-8')
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert '<strong>hola</strong>' in out and '<li>item' in out
    assert '<img' not in out and '<script>' not in out and '&lt;img' in out


def test_chat_reply_never_assigned_as_raw_html(index_html):
    assert not re.search(r'innerHTML\s*[+]?=\s*[^;]*\bdata\.reply', index_html)
    body = _fn_body(index_html, 'function appendChatMsg(')
    assert 'renderChatMarkdown(entry.content)' in body and 'renderChatTable(tbl)' in body
    assert 'box.textContent = entry.content' in body, 'los mensajes del usuario van como texto'


def test_chat_tables_come_from_server_data(index_html):
    body = _fn_body(index_html, 'async function sendChatMessage(')
    assert 'data.tables' in body and 'tickers: chatTickers()' in body
    cells = _fn_body(index_html, 'function chatFmtCell(')
    assert 'FIN_NA' in cells, 'celda sin dato: guion, nunca 0'


def test_chat_panel_is_large(index_html):
    css = re.search(r'\.chat-panel \{([^}]*)\}', index_html).group(1)
    w = int(re.search(r'width:min\((\d+)px', css).group(1))
    assert w >= 600 and 'top:0' in css and 'bottom:0' in css
    exp = re.search(r'\.chat-panel\.expanded \{([^}]*)\}', index_html).group(1)
    assert int(re.search(r'width:min\((\d+)px', exp).group(1)) > w


def test_valuation_refresh_button_states(index_html):
    body = _fn_body(index_html, 'function setValBusy(')
    assert 'btn.disabled = busy' in body and "setAttribute('aria-disabled'" in body and 'btn.title' in body
    poll = _fn_body(index_html, 'function pollValuations(')
    assert '/api/valuations/refresh/status' in poll and 'loadRiskSummary' in poll, \
        'al terminar se recargan registro, tab de Riesgo y sidebar (misma caché)'


def test_valuation_rows_open_risk_report(index_html):
    body = _fn_body(index_html, 'function renderValuations(')
    assert "selectTicker(sym); switchTab('risk')" in body
    assert 'finEsc(' in _fn_body(index_html, 'function valGroupTable(')


def test_compare_removed_from_server(server_module):
    rules = {r.rule for r in server_module.app.url_map.iter_rules()}
    assert '/api/compare' not in rules
    assert {'/api/valuations', '/api/valuations/refresh', '/api/valuations/refresh/status'} <= rules


# ── Modo Explorar ───────────────────────────────────────────────────────────

@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_explore_dynamic_keys_exist(index_html, tmp_path):
    """Motivos (códigos de explore.py), intenciones y sectores se traducen con claves armadas en JS."""
    import explore
    src = open(explore.__file__, encoding='utf-8').read()
    codes = set(re.findall(r"'code': '(\w+)'", src))
    assert codes >= {'new_sector', 'risk_below_avg', 'valuation', 'dividend'}
    tr = _translations(index_html, tmp_path)
    keys = [f'exp_r_{c}' for c in codes] + [f'exp_intent_{i}' for i in explore.INTENTS]
    keys += ['sec_' + s.lower().replace(' ', '_') for s in explore.SECTORS] + [f'level_{lv}' for lv in explore.LEVELS]
    for lang in ('en', 'es'):
        missing = [k for k in keys if k not in tr[lang]]
        assert not missing, f'{lang}: {missing}'


def test_explore_block_is_escaped_and_has_no_analyst_rating(index_html):
    body = _fn_body(index_html, 'function renderExploreBlock(')
    assert 'finEsc(c.ticker)' in body and 'finEsc(c.name' in body and 'map(finEsc)' in body
    assert 'averageAnalystRating' not in index_html and 'analyst' not in body.lower()
    assert "t('exp_disclaimer')" in body, 'aclara que son candidatos para observar, no recomendaciones'


def test_add_to_watching_uses_existing_lists(index_html):
    body = _fn_body(index_html, 'function addToWatching(')
    assert "category: 'watching'" in body and 'state.extraTickers.push' in body
    assert "setTickerCategory(sym, 'watching')" in body and 'renderSidebar()' in body
    send = _fn_body(index_html, 'async function sendChatMessage(')
    assert 'owned: Object.keys(getAllMeta())' in send and 'data.explore' in send


# ── Regla 4 en la variación del día ─────────────────────────────────────────

@pytest.mark.skipif(not NODE, reason='node no instalado')
def test_day_change_missing_shows_dash_not_zero(index_html, tmp_path):
    src = '\n'.join([re.search(r"const FIN_NA = [^;]+;", index_html).group(0),
                     _fn_body(index_html, 'function quoteNum('), _fn_body(index_html, 'function fmtDayChange(')])
    src += """
const out = [fmtDayChange(null, null), fmtDayChange(1.5, 0.75), fmtDayChange(null, -2), fmtDayChange(0, 0)];
process.stdout.write(JSON.stringify(out));"""
    f = tmp_path / 'chg.js'
    f.write_text(src, encoding='utf-8')
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    a, b, c, d = json.loads(res.stdout)
    assert a['text'] == '–' and 'pos' not in a['cls'] and 'neg' not in a['cls']
    assert b == {'text': '+1.50 (+0.75%)', 'cls': 'chg pos'}
    assert c == {'text': '– (-2.00%)', 'cls': 'chg neg'}
    assert d['text'] == '+0.00 (+0.00%)', 'un 0 real se muestra como 0'


def test_quote_ingestion_does_not_turn_null_into_zero(index_html):
    for pattern in (r'\bchg\s*\|\|\s*0', r'chgPct\s*\|\|\s*0', r'chg\s*\?\?\s*0', r'chg_pct\s*\?\?\s*0',
                    r'chgPct\s*\?\?\s*0', r'd\.price\s*\|\|\s*0'):
        assert not re.search(pattern, index_html), pattern
