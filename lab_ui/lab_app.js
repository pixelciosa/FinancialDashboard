/* Portfolio Lab: estado, carga por tarjeta, render y navegación. Todo dato viene de /api/lab/*. */
'use strict';

const LAB_KEY_BENCH = 'lab_bench';
const LAB_KEY_PERIOD = 'lab_period';
const LAB_PERIOD_MONTHS = { '1m': 1, '3m': 3, '6m': 6, '1y': 12 };
const LAB_PERIODS = ['all', '1m', '3m', '6m', 'ytd', '1y'];
const LAB_LEDGER_LIMIT = 60;
const LAB_ATTR_PRESETS = ['all', 'ytd', '1y', '6m', '3m'];
const LAB_ATTR_STEP = 50;

/* Cada tarjeta pertenece a un grupo de datos; un grupo que falla solo afecta a sus tarjetas. */
const LAB_GROUPS = {
  perf: ['resumen', 'evolucion', 'periodos', 'drawdown'],
  corr: ['mapa'],
  rr: ['dispersion'],
  ledger: ['operaciones'],
  reco: ['conciliacion'],
  attr: ['atribucion'],
  stress: ['estres'],
  journal: ['diario'],
  expo: ['exposicion'],
  mov: ['movil'],
  macro: ['macro'],
};
const LAB_CARD_GROUP = {};
Object.keys(LAB_GROUPS).forEach(g => LAB_GROUPS[g].forEach(c => { LAB_CARD_GROUP[c] = g; }));

const S = {
  bench: null, period: 'all', data: {}, status: {}, seq: {}, charts: {},
  showPositions: false, barMode: 'm', ledgerAll: false, ledgerFilter: '', recoAll: false, trace: null,
  attrPreset: 'all', attrFrom: null, attrTo: null, attrBounds: null, attrTk: '', attrSt: '', attrLimit: LAB_ATTR_STEP, attrTrace: null,
  stressSel: null, stressTrace: null,
  jrLim: null,
  expoTrace: null, macroTrace: null, movWin: null, movLim: null,
  jr: { filter: 'all', nw: { ticker: '', tesis: '', tipo: 'precio', op: '>=', valor: '' }, nwErr: {}, busy: false, editId: null, ed: null,
    confirm: null, note: '', rowErr: { id: null, e: {} }, trace: null, flash: null },
};
try { S.bench = localStorage.getItem(LAB_KEY_BENCH) || null; } catch (e) { /* solo en memoria */ }
try { const p = localStorage.getItem(LAB_KEY_PERIOD); if (LAB_PERIODS.includes(p)) S.period = p; } catch (e) { /* solo en memoria */ }

const $ = id => document.getElementById(id);
const cardEl = id => document.querySelector('.lab-card[data-card="' + id + '"]');
const bodyEl = id => cardEl(id).querySelector('.lab-body');

/* ---------- Nombres ---------- */
function entName(id) {
  if (id === LAB_PORT_ID) return t('portfolio');
  return tHas('idx_' + id) ? t('idx_' + id) + ' (' + id + ')' : id;
}
function entShort(id) { return id === LAB_PORT_ID ? t('portfolio_short') : id; }

/* ---------- API ---------- */
async function labApi(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  let body = null;
  try { body = await r.json(); } catch (e) { throw new Error('HTTP ' + r.status); }
  return body;
}

/* ---------- Errores del servidor: el codigo estable se traduce; el texto `error` (en espanol) va como detalle ---------- */
const LAB_ERR_CODES = ['benchmark_invalido', 'ventana_invalida', 'rango_invalido', 'fecha_invalida', 'escenario_invalido', 'cuerpo_invalido', 'validacion',
  'sin_precios', 'sin_operaciones', 'sin_precios_macro', 'config_invalida', 'journal_corrupto',
  'csv_portfolio_ilegible', 'tipo_contenido_invalido', 'cuerpo_demasiado_grande', 'limite_alcanzado', 'entrada_no_encontrada', 'refresh_en_curso',
  'ruta_no_encontrada', 'metodo_no_permitido', 'error_interno', 'solicitud_invalida', 'no_encontrado', 'conflicto', 'error_http'];
function labErrKey(codigo) { return typeof codigo === 'string' && LAB_ERR_CODES.indexOf(codigo) >= 0 && tHas('err_' + codigo) ? 'err_' + codigo : null; }
function labSrvDetail(res) { const raw = res && typeof res.error === 'string' ? res.error : ''; return raw ? { srv: raw } : ''; }
function labDetailText(d) { return d && typeof d === 'object' ? '(' + t('server_detail') + ': ' + d.srv + ')' : String(d == null ? '' : d); }
/* Mensaje de error ya escapado (HTML): texto traducido por `codigo` (o `fallbackKey`) + detalle del servidor rotulado. */
function labErrHtml(res, fallbackKey) {
  const key = labErrKey(res && res.codigo) || fallbackKey || 'state_error', d = labSrvDetail(res);
  return labEsc(t(key)) + (d ? ' <span class="lab-hint">' + labEsc(labDetailText(d)) + '</span>' : '');
}

/* Claves de trazabilidad 'tipo:id': se corta solo en el primer ':' (el id puede contener ':'). */
function labKeySplit(key) { const k = String(key), c = k.indexOf(':'); return c < 0 ? [k, ''] : [k.slice(0, c), k.slice(c + 1)]; }

function groupUrl(g) {
  if (g === 'perf') return '/api/lab/performance' + (S.bench ? '?benchmark=' + encodeURIComponent(S.bench) : '');
  return { corr: '/api/lab/correlation', rr: '/api/lab/risk-return', ledger: '/api/lab/ledger', reco: '/api/lab/reconcile', attr: '/api/lab/attribution' + attrQuery(), stress: '/api/lab/stress', journal: '/api/lab/journal', expo: '/api/lab/exposure', macro: '/api/lab/macro',
    mov: '/api/lab/correlation' + (S.movWin ? '?window=' + encodeURIComponent(S.movWin) : '') }[g];
}

/* ---------- Tarjetas: shell y estados ---------- */
function buildCards() {
  document.querySelectorAll('.lab-slot').forEach(slot => {
    const id = slot.dataset.card;
    if (id === 'movil') S.movLim = { min: +slot.dataset.minWindow, max: +slot.dataset.maxWindow };
    if (id === 'diario') S.jrLim = { tesis: +slot.dataset.maxTesis, nota: +slot.dataset.maxNota, ticker: +slot.dataset.maxTicker, entries: +slot.dataset.maxEntries };
    const a = document.createElement('article');
    a.className = 'lab-card';
    a.dataset.card = id;
    a.dataset.status = 'loading';
    a.setAttribute('aria-busy', 'true');
    a.innerHTML =
      '<h3 data-i18n="' + slot.dataset.title + '"></h3>'
      + (slot.dataset.sub ? '<p class="lab-sub" data-i18n="' + slot.dataset.sub + '"></p>' : '')
      + '<div class="lab-state" data-state="loading" role="status"><span class="lab-spin"></span><span data-i18n="state_loading"></span></div>'
      + '<div class="lab-state" data-state="empty"><span class="lab-msg"></span></div>'
      + '<div class="lab-state" data-state="error" role="alert"><span class="lab-msg"></span>'
      + '<button type="button" class="lab-btn" data-retry data-i18n="state_retry"></button></div>'
      + '<div class="lab-body"></div>';
    slot.replaceWith(a);
    S.status[id] = { st: 'loading' };
  });
}

function setStatus(id, st, msgKey, detail) { S.status[id] = { st, msgKey, detail }; }

function paintStatus(id) {
  const card = cardEl(id), s = S.status[id];
  card.dataset.status = s.st;
  card.setAttribute('aria-busy', s.st === 'loading' ? 'true' : 'false');
  ['empty', 'error'].forEach(k => {
    const m = card.querySelector('.lab-state[data-state="' + k + '"] .lab-msg');
    if (m) m.textContent = k === s.st && s.msgKey ? t(s.msgKey) + (s.detail ? ' ' + labDetailText(s.detail) : '') : '';
  });
}

function paintCard(id) {
  paintStatus(id);
  const s = S.status[id];
  if (s.st !== 'ok') { destroyChart(id); return; }
  try { PAINT[id](bodyEl(id)); } catch (e) {
    console.error('lab card', id, e);
    destroyChart(id);
    setStatus(id, 'error', 'state_error', e && e.message ? '(' + e.message + ')' : '');
    paintStatus(id);
  }
}

function destroyChart(id) {
  if (S.charts[id]) { try { S.charts[id].remove(); } catch (e) { /* ya destruido */ } S.charts[id] = null; }
}

/* ---------- Carga ---------- */
async function loadGroup(g, retried) {
  const token = (S.seq[g] || 0) + 1;
  S.seq[g] = token;
  LAB_GROUPS[g].forEach(c => { setStatus(c, 'loading'); paintStatus(c); });
  let res = null, err = null;
  try { res = await labApi(groupUrl(g)); } catch (e) { err = e; }
  if (token !== S.seq[g]) return; // llegó una respuesta más nueva
  if (g === 'perf' && S.bench && res && res.ok === false && !res.status && !retried) {
    S.bench = null; // índice guardado ya no válido: reintenta con el predeterminado del servidor
    try { localStorage.removeItem(LAB_KEY_BENCH); } catch (e) { /* solo en memoria */ }
    return loadGroup(g, true);
  }
  let st, key, detail = '';
  if (err) { st = 'error'; key = 'state_error'; detail = '(' + err.message + ')'; }
  else if (g === 'journal' && res && (res.status === 'sin_precios' || res.status === 'journal_corrupto')) { st = 'ok'; } // la tarjeta lo explica y sigue usable (o bloqueada) por dentro
  else if (res && res.status === 'sin_precios') { st = 'empty'; key = 'state_sin_precios'; }
  else if (res && res.status === 'sin_operaciones') { st = 'empty'; key = 'state_sin_operaciones'; }
  else if (res && res.status === 'sin_precios_macro') { st = 'empty'; key = 'state_sin_precios_macro'; }
  else if (res && res.status === 'sin_precios_estres') { st = 'empty'; key = 'state_sin_precios_estres'; }
  else if (res && res.status === 'config_invalida') { st = 'error'; key = g === 'macro' ? 'state_config_invalida_macro' : (g === 'expo' ? 'state_config_invalida_expo' : 'state_config_invalida'); detail = labSrvDetail(res); }
  else if (!res || res.ok === false) { st = 'error'; key = labErrKey(res && res.codigo) || 'state_error'; detail = labSrvDetail(res); }
  else { st = 'ok'; }
  if (st === 'ok') S.data[g] = res; else S.data[g] = null;
  if (g === 'perf' && st === 'ok' && !S.bench) S.bench = res.benchmark;
  LAB_GROUPS[g].forEach(c => setStatus(c, st, key, detail));
  if (g === 'attr') attrLoaded(res, st);
  if (g === 'mov' && st === 'ok' && res.movil) S.movWin = res.movil.ventana;
  if (g === 'ledger' && st === 'ok' && $('jrTickers')) $('jrTickers').innerHTML = jrTickers().map(tk => '<option value="' + labEsc(tk) + '"></option>').join('');
  if (g === 'journal' && st === 'ok') { S.jr.trace = S.jr.trace && jrEntry(S.jr.trace) ? S.jr.trace : null; }
  if (g === 'perf' || g === 'rr') { fillBench(); paintAsOf(); }
  if (g === 'perf' && st === 'ok') LAB_GROUPS.rr.forEach(c => { if (S.status[c].st === 'ok') paintCard(c); });
  LAB_GROUPS[g].forEach(paintCard);
  if (g === 'corr' || g === 'perf') LAB_GROUPS.corr.forEach(c => { if (S.status[c].st === 'ok') paintCard(c); });
}

async function loadAll() {
  await Promise.allSettled(Object.keys(LAB_GROUPS).map(g => loadGroup(g)));
}

/* ---------- Barra superior ---------- */
function fillBench() {
  const sel = $('labBench');
  const rr = S.data.rr, perf = S.data.perf;
  let ids = rr ? rr.items.filter(i => i.tipo === 'indice').map(i => i.id) : [];
  if (!ids.length && perf) ids = [perf.benchmark];
  sel.innerHTML = '';
  ids.forEach(id => { const o = document.createElement('option'); o.value = id; o.textContent = entName(id); sel.appendChild(o); });
  const off = ids.length === 0;
  sel.disabled = off;
  sel.setAttribute('aria-disabled', off ? 'true' : 'false');
  sel.title = off ? t('state_empty') : '';
  if (!off) sel.value = ids.includes(S.bench) ? S.bench : ids[0];
}
function fillPeriod() {
  const sel = $('labPeriod');
  sel.innerHTML = '';
  LAB_PERIODS.forEach(p => { const o = document.createElement('option'); o.value = p; o.textContent = t('per_' + p); sel.appendChild(o); });
  sel.value = S.period;
}
function paintAsOf() {
  const d = (S.data.perf && S.data.perf.hasta) || (S.data.rr && S.data.rr.hasta) || null;
  $('labAsOf').innerHTML = labEsc(t('data_as_of')) + ' <b class="lab-mono">' + labEsc(d ? labDate(d) : t('no_date')) + '</b>';
}

function applyI18n() {
  document.documentElement.lang = labLang;
  document.title = t('page_title');
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.querySelectorAll('.lab-lang button').forEach(b => b.classList.toggle('active', b.dataset.lang === labLang));
  fillPeriod(); fillBench(); paintAsOf(); fillAttrRange();
}
function repaintAll() {
  applyI18n();
  paintRefreshBtn(); paintRefreshPanel(); paintCaches();
  Object.keys(S.status).forEach(paintCard);
}

/* ---------- Períodos ---------- */
function periodStart(dates) {
  if (S.period === 'all' || !dates.length) return null;
  const last = dates[dates.length - 1];
  if (S.period === 'ytd') return last.slice(0, 4) + '-01-01';
  const d = new Date(last + 'T00:00:00Z');
  d.setUTCMonth(d.getUTCMonth() - LAB_PERIOD_MONTHS[S.period]);
  return d.toISOString().slice(0, 10);
}
function periodIndex(dates) {
  const start = periodStart(dates);
  if (!start) return 0;
  let i0 = 0;
  dates.forEach((d, i) => { if (d <= start) i0 = i; });
  return i0;
}

/* ---------- Resumen: 6 tarjetas ---------- */
function tileDefs(p) {
  const P = p.stats.portfolio, B = p.stats.benchmark, C = p.cash, M = p.mismos_aportes;
  const idx = entName(p.benchmark), ddNow = p.series.portfolio.drawdown_pct.slice(-1)[0];
  const src = '/api/lab/performance?benchmark=' + p.benchmark;
  const vs = v => t('tile_vs', { idx: '<span class="lab-num">' + labEsc(idx) + '</span>', v: '<span class="lab-num">' + v + '</span>' });
  const diff = M.diferencia_vs_cartera;
  return [
    { k: 'tile_cum', tip: 'tip_cum', f: 'f_cum', v: labPct(P.cum_pct), sub: vs(labPct(B.cum_pct)),
      inputs: [['inp_cum', labPct(P.cum_pct, 2)], ['inp_dias', P.dias]], src },
    { k: 'tile_ann', tip: 'tip_ann', f: 'f_ann', v: labPct(P.ann_pct), sub: vs(labPct(B.ann_pct)),
      inputs: [['inp_cum', labPct(P.cum_pct, 2)], ['inp_dias', P.dias], ['inp_ann', labPct(P.ann_pct, 2)]], src },
    { k: 'tile_vol', tip: 'tip_vol_card', f: 'f_vol', v: labPct(P.vol_pct, 1, false), sub: vs(labPct(B.vol_pct, 1, false)),
      inputs: [['inp_vol', labPct(P.vol_pct, 2, false)], ['inp_dias', P.dias]], src },
    { k: 'tile_dd', tip: 'tip_dd', f: 'f_dd', v: labPct(P.max_dd_pct, 1, false),
      sub: labEsc(t('tile_dd_now', { v: labPct(ddNow, 1, false) })) + ' ' + vs(labPct(B.max_dd_pct, 1, false)),
      inputs: [['inp_dd', labPct(P.max_dd_pct, 2, false)], ['inp_dias', P.dias]], src },
    { k: 'tile_usd', tip: 'tip_usd', f: 'f_usd', v: labUsd(C.resultado_usd), plain: true,
      sub: labEsc(t('tile_usd_sub', { inv: labUsd(C.invertido), end: labUsd(C.valor_final) })),
      inputs: [['inp_valor_inicial', labUsd(C.valor_inicial)], ['inp_aportes_netos', labUsd(C.aportes_netos)],
        ['inp_invertido', labUsd(C.invertido)], ['inp_valor_final', labUsd(C.valor_final)]], src },
    { k: 'tile_same', kv: { idx }, tip: 'tip_same', f: 'f_same', v: labUsd(M.valor_final), plain: true,
      sub: (diff == null ? '' : labEsc(t(diff >= 0 ? 'tile_same_more' : 'tile_same_less', { d: labUsd(Math.abs(diff)) })) + ' ') + labEsc(t('tile_same_note')),
      inputs: [['inp_same_final', labUsd(M.valor_final)], ['inp_valor_final', labUsd(C.valor_final)], ['inp_diff', labUsd(diff)]], src },
  ];
}
function paintResumen(body) {
  const p = S.data.perf, defs = tileDefs(p);
  body.innerHTML = '<div class="lab-tiles">' + defs.map((d, i) =>
    '<button type="button" class="lab-tile" data-tile="' + i + '" aria-expanded="' + (S.trace === i) + '" title="' + labEsc(t(d.tip)) + '">'
    + '<div class="k">' + labEsc(t(d.k, d.kv)) + '<span class="lab-info" aria-hidden="true">?</span></div>'
    + '<div class="v">' + labEsc(d.v) + '</div><div class="c' + (d.plain ? ' plain' : '') + '">' + d.sub + '</div></button>').join('')
    + '</div><div class="lab-trace" id="labTrace" hidden></div>'
    + (p.warnings && p.warnings.some(w => w.tipo === 'ventas_recortadas') ? '<p class="lab-hint">' + labEsc(t('warn_trimmed')) + '</p>' : '')
    + '<p class="lab-hint">' + labEsc(t('trace_hint')) + '</p>';
  if (S.trace != null) showTrace(S.trace);
}
function showTrace(i) {
  const box = $('labTrace'), d = tileDefs(S.data.perf)[i];
  if (!box || !d) return;
  S.trace = i;
  box.hidden = false;
  box.innerHTML = '<h4>' + labEsc(t(d.k, d.kv)) + '</h4>'
    + '<div>' + labEsc(t('formula')) + ': <code>' + labEsc(t(d.f)) + '</code></div>'
    + '<div>' + labEsc(t('source')) + ': <code>' + labEsc(d.src) + '</code></div>'
    + '<dl>' + d.inputs.map(x => '<dt>' + labEsc(t(x[0])) + '</dt><dd>' + labEsc(x[1]) + '</dd>').join('') + '</dl>';
  document.querySelectorAll('.lab-tile').forEach((b, j) => b.setAttribute('aria-expanded', String(j === i)));
}

/* ---------- Desempeño ---------- */
function legendHtml(items) {
  return '<div class="lab-legend">' + items.map(i => '<span><i class="' + (i.dot ? 'dot' : '') + '" style="background:' + i.color + '"></i>' + labEsc(i.label) + '</span>').join('') + '</div>';
}
function legendPair(p) {
  return legendHtml([{ label: entName(LAB_PORT_ID), color: labColor(LAB_PORT_ID, p.benchmark) }, { label: entName(p.benchmark), color: labColor(p.benchmark, p.benchmark) }]);
}
function paintEvolucion(body) {
  destroyChart('evolucion');
  const p = S.data.perf, i0 = periodIndex(p.dates), mk = key => {
    const v = p.series[key].index, base = v[i0];
    return p.dates.slice(i0).map((d, k) => ({ time: d, value: v[i0 + k] / base * 100 }));
  };
  body.innerHTML = legendPair(p) + '<div class="lab-plot" id="labPlotEvol"></div>';
  S.charts.evolucion = labLineChart($('labPlotEvol'), [
    { id: 'b', label: entName(p.benchmark), color: labColor(p.benchmark, p.benchmark), width: 2, data: mk('benchmark') },
    { id: LAB_PORT_ID, label: entName(LAB_PORT_ID), color: labColor(LAB_PORT_ID, p.benchmark), width: 3, data: mk('portfolio') },
  ], { fmt: v => v == null ? LAB_NA : labNum(v, 1) + ' (' + labPct(v - 100) + ')' });
}
function paintDrawdown(body) {
  destroyChart('drawdown');
  const p = S.data.perf, i0 = periodIndex(p.dates);
  const mk = key => {
    if (i0 === 0) return p.dates.map((d, k) => ({ time: d, value: p.series[key].drawdown_pct[k] }));
    const v = p.series[key].index, base = v[i0]; let peak = 1;
    return p.dates.slice(i0).map((d, k) => { const r = v[i0 + k] / base; peak = Math.max(peak, r); return { time: d, value: (r / peak - 1) * 100 }; });
  };
  body.innerHTML = legendPair(p) + '<div class="lab-plot" id="labPlotDD"></div>';
  S.charts.drawdown = labLineChart($('labPlotDD'), [
    { id: 'b', label: entName(p.benchmark), color: labColor(p.benchmark, p.benchmark), width: 2, data: mk('benchmark') },
    { id: LAB_PORT_ID, label: entName(LAB_PORT_ID), color: labColor(LAB_PORT_ID, p.benchmark), width: 2, area: true, data: mk('portfolio') },
  ], { fmt: v => v == null ? LAB_NA : labNum(v, 1) + '%' });
}
function paintPeriodos(body) {
  const p = S.data.perf, key = S.barMode === 'm' ? 'monthly' : 'yearly', start = periodStart(p.dates);
  const bench = new Map(p[key].benchmark.map(r => [r.periodo, r.retorno_pct]));
  let cats = p[key].portfolio.map(r => ({ id: r.periodo, label: S.barMode === 'm' ? labMonth(r.periodo) : r.periodo, port: r.retorno_pct, bench: bench.has(r.periodo) ? bench.get(r.periodo) : null }));
  if (start) cats = cats.filter(c => c.id >= start.slice(0, c.id.length));
  body.innerHTML = '<div class="lab-seg" role="group"><button type="button" data-mode="m" aria-pressed="' + (S.barMode === 'm') + '">' + labEsc(t('monthly')) + '</button>'
    + '<button type="button" data-mode="y" aria-pressed="' + (S.barMode === 'y') + '">' + labEsc(t('yearly')) + '</button></div>'
    + legendPair(p)
    + (cats.length ? '<div class="lab-svgwrap">' + labBarsSvg(cats, p.benchmark, entName) + '</div>' : '<div class="lab-state" style="display:flex">' + labEsc(t('state_empty')) + '</div>');
}

/* ---------- Correlación y riesgo ---------- */
function paintMapa(body) {
  const c = S.data.corr, sel = S.data.perf ? S.data.perf.benchmark : S.bench;
  body.innerHTML = '<div class="lab-svgwrap">' + labHeatmapSvg(c.names, c.matrix, sel, entName, entShort) + '</div>'
    + '<div class="lab-scale"><span>-1</span><div style="background:' + labHeatScaleCss() + '"></div><span>+1</span></div>'
    + '<p class="lab-hint">' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/correlation</code></p>';
}
function paintDispersion(body) {
  const rr = S.data.rr, sel = S.data.perf ? S.data.perf.benchmark : S.bench;
  const usable = rr.items.filter(i => i.vol_pct != null && i.ann_pct != null);
  const pts = usable.filter(i => S.showPositions || i.tipo !== 'posicion').map(i => ({
    id: i.id, x: i.vol_pct, y: i.ann_pct, kind: i.tipo, label: i.tipo === 'cartera' ? entShort(i.id) : i.id,
    ticker: i.tipo === 'posicion' ? i.id : null,
    title: entName(i.id) + (i.tipo === 'posicion' && i.peso_pct != null ? ' (' + labNum(i.peso_pct, 1) + '% ' + t('weight_pct') + ')' : '')
      + '\n' + t('tip_ann_ret') + ': ' + labPct(i.ann_pct) + '\n' + t('tip_vol') + ': ' + labPct(i.vol_pct, 1, false),
  }));
  if (!pts.length) { body.innerHTML = '<div class="lab-state" style="display:flex">' + labEsc(t('state_empty')) + '</div>'; return; }
  const rows = usable.filter(i => i.tipo !== 'posicion').map(i => '<tr class="' + (i.id === LAB_PORT_ID ? 'me' : i.id === sel ? 'sel' : '') + '"><td class="txt">' + labEsc(entName(i.id)) + '</td>'
    + '<td>' + labPct(i.cum_pct) + '</td><td>' + labPct(i.ann_pct) + '</td><td>' + labPct(i.vol_pct, 1, false) + '</td></tr>').join('');
  body.innerHTML = legendHtml([{ label: entName(LAB_PORT_ID), color: labColor(LAB_PORT_ID, sel), dot: 1 }, { label: entName(sel), color: labColor(sel, sel), dot: 1 }, { label: t('other_indices'), color: labColor('', sel), dot: 1 }])
    + '<label class="lab-chk"><input type="checkbox" id="labShowPos"' + (S.showPositions ? ' checked' : '') + '> ' + labEsc(t('positions_toggle')) + '</label>'
    + '<div class="lab-svgwrap">' + labScatterSvg(pts, sel, { x: t('axis_vol'), y: t('axis_ret') }) + '</div>'
    + '<details class="lab-details"><summary>' + labEsc(t('data_table')) + '</summary><div class="lab-table-wrap"><table class="lab-table"><thead><tr><th class="txt"></th>'
    + '<th title="' + labEsc(t('tip_cum')) + '">' + labEsc(t('tile_cum')) + '</th><th title="' + labEsc(t('tip_ann')) + '">' + labEsc(t('tile_ann')) + '</th>'
    + '<th title="' + labEsc(t('tip_vol_card')) + '">' + labEsc(t('tile_vol')) + '</th></tr></thead><tbody>' + rows + '</tbody></table></div></details>'
    + '<p class="lab-hint">' + labEsc(t('formula')) + ': <code class="lab-mono">' + labEsc(t('f_vol')) + '</code> &middot; ' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/risk-return</code></p>';
}

/* ---------- Libro ---------- */
function labQty(x) { return x == null ? LAB_NA : x.toLocaleString(labLocale(), { maximumFractionDigits: 6 }); }
function tickerBtn(tk) {
  return '<button type="button" class="lab-link" data-ticker="' + labEsc(tk) + '" title="' + labEsc(t('open_in_dashboard', { t: tk })) + '">' + labEsc(tk) + '</button>';
}
function opsRowsHtml() {
  const led = S.data.ledger, f = S.ledgerFilter.trim().toUpperCase();
  const all = led.ops.slice().sort((a, b) => (a.fecha < b.fecha ? 1 : a.fecha > b.fecha ? -1 : 0));
  const rows = f ? all.filter(o => (o.ticker || '').toUpperCase().includes(f)) : all;
  const shown = S.ledgerAll || f ? rows : rows.slice(0, LAB_LEDGER_LIMIT);
  return {
    count: t('ledger_count', { n: shown.length, total: rows.length }),
    html: shown.map(o => '<tr><td class="txt lab-mono">' + labEsc(labDate(o.fecha)) + '</td><td class="txt">' + labEsc(tHas('tipo_' + o.tipo) ? t('tipo_' + o.tipo) : o.tipo) + '</td>'
      + '<td class="txt">' + tickerBtn(o.ticker) + '</td><td>' + labQty(o.cantidad) + '</td><td>' + labNum(o.precio_usd, 2) + '</td><td>' + labNum(o.monto_usd, 2) + '</td>'
      + '<td class="txt">' + labEsc(o.plataforma || LAB_NA) + '</td><td class="txt">' + labEsc(o.origen || LAB_NA) + '</td></tr>').join(''),
    more: !S.ledgerAll && !f && rows.length > LAB_LEDGER_LIMIT,
  };
}
function paintOperaciones(body) {
  const led = S.data.ledger;
  if (!led.ops.length) { body.innerHTML = '<div class="lab-state" style="display:flex">' + labEsc(t('state_empty')) + '</div>'; return; }
  const exc = led.counts && led.counts.excluidas ? Object.values(led.counts.excluidas).reduce((a, b) => a + b, 0) : 0;
  const th = (k, tip, txt) => '<th' + (txt ? ' class="txt"' : '') + (tip ? ' title="' + labEsc(t(tip)) + '"' : '') + '>' + labEsc(t(k)) + '</th>';
  body.innerHTML = '<div class="lab-toolbar"><input type="text" id="labLedgerFilter" placeholder="' + labEsc(t('ledger_filter')) + '" aria-label="' + labEsc(t('ledger_filter')) + '" value="' + labEsc(S.ledgerFilter) + '">'
    + '<span id="labLedgerCount" class="lab-mono"></span><button type="button" class="lab-btn" id="labLedgerAll"></button>'
    + (exc ? '<span>' + labEsc(t('ledger_excluded', { n: exc })) + '</span>' : '')
    + (led.errors && led.errors.length ? '<span class="neg">' + labEsc(t('ledger_errors', { n: led.errors.length })) + '</span>' : '') + '</div>'
    + '<div class="lab-table-wrap"><table class="lab-table"><thead><tr>' + th('ledger_col_date', '', 1) + th('ledger_col_type', '', 1) + th('ledger_col_ticker', '', 1)
    + th('ledger_col_qty', 'ledger_tip_qty') + th('ledger_col_price') + th('ledger_col_amount', 'ledger_tip_amount') + th('ledger_col_platform', '', 1) + th('ledger_col_origin', '', 1)
    + '</tr></thead><tbody id="labLedgerRows"></tbody></table></div>'
    + '<p class="lab-hint">' + labEsc(t('formula')) + ': <code class="lab-mono">' + labEsc(t('ledger_formula')) + '</code> &middot; ' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/ledger</code></p>';
  updateOps();
}
function updateOps() {
  const r = opsRowsHtml();
  $('labLedgerRows').innerHTML = r.html;
  $('labLedgerCount').textContent = r.count;
  const b = $('labLedgerAll');
  b.textContent = t(S.ledgerAll ? 'ledger_less' : 'ledger_all');
  b.hidden = !r.more && !S.ledgerAll;
}

const RECO_STATES = { ok: 'ok', esperada_excluida: 'esperada_excluida', sin_historial: 'sin_historial', diferencias_reales: 'diff' };
const RECO_KNOWN = ['ok', 'esperada_excluida', 'sin_historial', 'diff', 'sin_portfolio'];
function recoKey(state) { return state === 'diferencia' || state === 'diferencias_reales' ? 'diff' : (RECO_KNOWN.indexOf(state) >= 0 ? state : 'diff'); }
function recoPill(state) { const k = recoKey(state); return '<span class="lab-pill ' + k + '" title="' + labEsc(t('reco_tip_' + k)) + '">' + labEsc(t('reco_state_' + k)) + '</span>'; }
function recoReason(x) {
  const k = recoKey(x.estado), main = t('reco_reason_' + k);
  return labEsc(main) + (x.motivo ? '<div class="lab-hint" style="margin:2px 0 0">' + labEsc(t('reco_backend_detail')) + ': ' + labEsc(x.motivo) + '</div>' : '');
}
function paintConciliacion(body) {
  const r = S.data.reco, sum = r.resumen || {};
  const rows = S.recoAll ? r.rows : r.fuera_de_tolerancia;
  body.innerHTML = '<div class="lab-reco-sum">' + Object.keys(RECO_STATES).filter(k => sum[k]).map(k =>
    '<span class="lab-pill ' + RECO_STATES[k] + '" title="' + labEsc(t('reco_tip_' + RECO_STATES[k])) + '"><b>' + labEsc(Number(sum[k].conteo) || 0) + '</b>' + labEsc(t('reco_state_' + RECO_STATES[k])) + '</span>').join('')
    + '<span class="lab-hint" style="align-self:center;margin:0">' + labEsc(t('reco_tolerance', { v: labPct(r.tolerancia * 100, 0, false) })) + '</span></div>'
    + '<div class="lab-toolbar"><button type="button" class="lab-btn" id="labRecoAll">' + labEsc(t(S.recoAll ? 'reco_only_gaps' : 'reco_show_all')) + '</button></div>'
    + (rows.length ? '<div class="lab-table-wrap"><table class="lab-table"><thead><tr><th class="txt">' + labEsc(t('reco_col_ticker')) + '</th><th>' + labEsc(t('reco_col_ledger')) + '</th>'
      + '<th>' + labEsc(t('reco_col_portfolio')) + '</th><th>' + labEsc(t('reco_col_diff')) + '</th><th class="txt">' + labEsc(t('reco_col_state')) + '</th><th class="txt">' + labEsc(t('reco_col_reason')) + '</th></tr></thead><tbody>'
      + rows.map(x => '<tr><td class="txt">' + tickerBtn(x.ticker) + '</td><td>' + labQty(x.libro) + '</td><td>' + labQty(x.portfolio) + '</td><td>' + labQty(x.diferencia) + '</td>'
        + '<td class="txt">' + recoPill(x.estado) + '</td><td class="txt">' + recoReason(x) + '</td></tr>').join('')
      + '</tbody></table></div>' : '<div class="lab-state" style="display:flex">' + labEsc(t('reco_all_ok')) + '</div>')
    + trimmedSalesHtml(r.ventas_recortadas)
    + '<p class="lab-hint">' + labEsc(t('formula')) + ': <code class="lab-mono">' + labEsc(t('reco_formula')) + '</code> &middot; ' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/reconcile</code></p>';
}

/* ---------- Atribución ---------- */
function attrUsd2(x) { return x == null || !isFinite(x) ? LAB_NA : (x < 0 ? '-' : '') + 'US$ ' + labNum(Math.abs(x), 2); }
function attrPp(x) { return x == null || !isFinite(x) ? LAB_NA : (x > 0 ? '+' : '') + labNum(x, 2) + ' pp'; }
function attrSign(x) { return x > 0 ? 'pos' : x < 0 ? 'neg' : ''; }
function attrQuery() {
  const q = [];
  if (S.attrFrom) q.push('from=' + encodeURIComponent(S.attrFrom));
  if (S.attrTo) q.push('to=' + encodeURIComponent(S.attrTo));
  return q.length ? '?' + q.join('&') : '';
}
/* Los límites salen de las fechas que devuelve la API (primer día y último precio), nunca de constantes. */
function attrPresetFrom(p) {
  const b = S.attrBounds;
  if (!b || p === 'all') return null;
  let from;
  if (p === 'ytd') from = b.hasta.slice(0, 4) + '-01-01';
  else {
    const d = new Date(b.hasta + 'T00:00:00Z');
    d.setUTCMonth(d.getUTCMonth() - LAB_PERIOD_MONTHS[p]);
    from = d.toISOString().slice(0, 10);
  }
  return from < b.desde ? b.desde : from;
}
function fillAttrRange() {
  const host = $('labAttrRange');
  if (!host) return;
  host.setAttribute('aria-label', t('attr_range'));
  const b = S.attrBounds, off = !b, tip = off ? t('attr_range_wait') : t('attr_range_tip');
  const dis = off ? ' disabled aria-disabled="true"' : ' aria-disabled="false"';
  host.innerHTML = '<span>' + labEsc(t('attr_range')) + '</span><span class="lab-seg" title="' + labEsc(tip) + '">'
    + LAB_ATTR_PRESETS.map(p => '<button type="button" data-attr-preset="' + p + '" aria-pressed="' + (S.attrPreset === p) + '"' + dis + ' title="' + labEsc(tip) + '">' + labEsc(t('per_' + p)) + '</button>').join('') + '</span>'
    + '<label class="lab-field">' + labEsc(t('attr_from')) + ' <input type="date" id="labAttrFrom" value="' + labEsc(S.attrFrom || (b ? b.desde : '')) + '"'
    + (b ? ' min="' + labEsc(b.desde) + '" max="' + labEsc(b.hasta) + '"' : '') + dis + ' title="' + labEsc(tip) + '"></label>'
    + '<label class="lab-field">' + labEsc(t('attr_to')) + ' <input type="date" id="labAttrTo" value="' + labEsc(S.attrTo || (b ? b.hasta : '')) + '"'
    + (b ? ' min="' + labEsc(b.desde) + '" max="' + labEsc(b.hasta) + '"' : '') + dis + ' title="' + labEsc(tip) + '"></label>';
}
function attrLoaded(res, st) {
  if (st === 'ok' && !S.attrFrom && !S.attrTo && res.desde && res.hasta) S.attrBounds = { desde: res.desde, hasta: res.hasta };
  fillAttrRange();
}

function attrFig(key, text, cls) {
  return '<button type="button" class="lab-fig ' + (cls || '') + '" data-attr-trace="' + labEsc(key) + '" aria-expanded="' + (S.attrTrace === key) + '">' + labEsc(text) + '</button>';
}
function attrTraceDef(key) {
  const a = S.data.attr, r = a.resumen || {}, rc = a.reconciliacion || {}, p = key.split(':');
  const resInputs = (o) => [['inp_valor_inicial', attrUsd2(o.valor_inicial)], ['attr_inp_flow', attrUsd2(o.flujo_neto != null ? o.flujo_neto : o.aportes_netos)],
    ['inp_valor_final', attrUsd2(o.valor_final)], ['attr_inp_result', attrUsd2(o.resultado_usd)]];
  if (key === 'reco') {
    return { title: t('attr_reco_title'), f: 'attr_f_reco', inputs: [['attr_inp_sum_pos', attrUsd2(rc.suma_posiciones)], ['attr_inp_sum_ops', attrUsd2(rc.suma_operaciones)],
      ['attr_inp_total', attrUsd2(rc.resultado_usd_cartera)], ['attr_reco_diff', attrUsd2(rc.diferencia)], ['attr_inp_tol', attrUsd2(rc.tolerancia_usd)]] };
  }
  if (p[0] === 'sum') {
    if (p[1] === 'dietz' || p[1] === 'base') {
      return { title: t(p[1] === 'dietz' ? 'attr_sum_dietz' : 'attr_sum_base'), f: 'attr_f_dietz',
        inputs: [['attr_inp_result', attrUsd2(r.resultado_usd)], ['attr_inp_base', attrUsd2(r.base_dietz)], ['attr_inp_dietz', labPct(r.rentabilidad_dietz_pct, 2)]] };
    }
    return { title: t('attr_sum_' + p[1]), f: 'attr_f_result', inputs: resInputs(r) };
  }
  const row = (a.posiciones || [])[+p[1]];
  if (!row) return null;
  const title = row.ticker + ' - ' + t('attr_col_' + p[2]);
  if (p[2] === 'pct') return { title, f: 'attr_f_pct', inputs: [['attr_inp_result', attrUsd2(row.resultado_usd)], ['attr_inp_total', attrUsd2(r.resultado_usd)], ['attr_col_pct', labPct(row.pct_del_resultado, 2, false)]] };
  if (p[2] === 'pp') return { title, f: 'attr_f_pp', inputs: [['attr_inp_result', attrUsd2(row.resultado_usd)], ['attr_inp_base', attrUsd2(r.base_dietz)], ['attr_col_pp', attrPp(row.aporte_pp)]] };
  return { title, f: 'attr_f_result', inputs: resInputs(row) };
}
function showAttrTrace(key) {
  const box = $('labAttrTrace'), d = box && attrTraceDef(key);
  if (!box || !d) return;
  S.attrTrace = key;
  box.hidden = false;
  box.innerHTML = '<h4>' + labEsc(d.title) + '</h4>'
    + '<div>' + labEsc(t('formula')) + ': <code>' + labEsc(t(d.f)) + '</code></div>'
    + '<div>' + labEsc(t('source')) + ': <code>' + labEsc('/api/lab/attribution' + attrQuery()) + '</code></div>'
    + '<dl>' + d.inputs.map(x => '<dt>' + labEsc(t(x[0])) + '</dt><dd>' + labEsc(x[1]) + '</dd>').join('') + '</dl>';
  document.querySelectorAll('[data-attr-trace]').forEach(b => b.setAttribute('aria-expanded', String(b.dataset.attrTrace === key)));
}
function toggleAttrTrace(key) {
  if (S.attrTrace === key) {
    S.attrTrace = null; $('labAttrTrace').hidden = true;
    document.querySelectorAll('[data-attr-trace]').forEach(b => b.setAttribute('aria-expanded', 'false'));
  } else showAttrTrace(key);
}

function attrWarnHtml(w) {
  const code = w && typeof w === 'object' ? w.tipo : null, key = 'attr_warn_' + code;
  let text, detail = '';
  if (code && tHas(key)) text = t(key, { tickers: Array.isArray(w.tickers) ? w.tickers.join(', ') : '' });
  else {
    text = t('attr_warn_generic');
    const raw = w && typeof w === 'object' ? (w.mensaje || w.message || (typeof w.detalle === 'string' ? w.detalle : '') || code || JSON.stringify(w)) : w;
    detail = String(raw == null ? '' : raw);
  }
  return '<li>' + labEsc(text) + (detail ? ' <span class="lab-hint">' + labEsc(t('server_detail')) + ': ' + labEsc(detail) + '</span>' : '') + '</li>';
}
function attrRecoHtml(a) {
  const rc = a.reconciliacion;
  if (!rc) return '';
  const ok = rc.ok === true, dt = Array.isArray(rc.diferencias_por_ticker) ? rc.diferencias_por_ticker : [];
  return '<div class="lab-attr-reco"><span class="lab-pill ' + (ok ? 'ok' : 'diff') + '" title="' + labEsc(t(ok ? 'attr_reco_tip_ok' : 'attr_reco_tip_alert')) + '">' + labEsc(t(ok ? 'attr_reco_ok' : 'attr_reco_alert')) + '</span>'
    + attrFig('reco', t('attr_reco_title'))
    + '<span class="lab-mono ' + (ok ? '' : 'neg') + '">' + labEsc(t('attr_reco_diff', { d: attrUsd2(rc.diferencia) })) + '</span>'
    + '<span class="lab-hint" style="margin:0">' + labEsc(t('attr_reco_tol', { v: attrUsd2(rc.tolerancia_usd) })) + '</span></div>'
    + (dt.length ? '<div class="lab-hint">' + labEsc(t('attr_reco_by_ticker')) + '</div><ul class="lab-attr-list">' + dt.map(x =>
      '<li>' + tickerBtn(x.ticker) + ' ' + labEsc(t('attr_reco_row', { a: attrUsd2(x.resultado_posicion), b: attrUsd2(x.resultado_operaciones), d: attrUsd2(x.diferencia) })) + '</li>').join('') + '</ul>' : '');
}
function attrSummaryHtml(a) {
  const r = a.resumen || {};
  const items = [['result', attrUsd2(r.resultado_usd), attrSign(r.resultado_usd)], ['dietz', labPct(r.rentabilidad_dietz_pct, 2), attrSign(r.rentabilidad_dietz_pct)],
    ['vi', attrUsd2(r.valor_inicial), ''], ['flow', attrUsd2(r.aportes_netos), ''], ['vf', attrUsd2(r.valor_final), ''], ['base', attrUsd2(r.base_dietz), '']];
  return '<div class="lab-attr-sum">' + items.map(i => '<div><div class="k">' + labEsc(t('attr_sum_' + i[0])) + '</div>' + attrFig('sum:' + i[0], i[1], i[2]) + '</div>').join('') + '</div>';
}
function attrChartHtml(a) {
  const rows = (a.posiciones || []).filter(p => p.resultado_usd != null && isFinite(p.resultado_usd))
    .sort((x, y) => y.resultado_usd - x.resultado_usd)
    .map(p => ({ ticker: p.ticker, value: p.resultado_usd, text: attrUsd2(p.resultado_usd),
      tip: t('attr_bar_tip', { t: p.ticker, v: attrUsd2(p.resultado_usd) }) + '. ' + t('open_in_dashboard', { t: p.ticker }) }));
  if (!rows.length) return '';
  return '<h4 class="lab-h4">' + labEsc(t('attr_chart_title')) + '</h4><div class="lab-svgwrap lab-attr-chart">' + labAttrBarsSvg(rows, t('attr_chart_aria')) + '</div>';
}
function attrPositionsHtml(a) {
  const th = (k, tip, txt) => '<th' + (txt ? ' class="txt"' : '') + (tip ? ' title="' + labEsc(t(tip)) + '"' : '') + '>' + labEsc(t(k)) + '</th>';
  const rows = (a.posiciones || []).map((p, i) => '<tr><td class="txt">' + tickerBtn(p.ticker) + '</td><td>' + attrUsd2(p.valor_inicial) + '</td><td>' + attrUsd2(p.flujo_neto) + '</td><td>' + attrUsd2(p.valor_final) + '</td>'
    + '<td>' + attrFig('pos:' + i + ':result', attrUsd2(p.resultado_usd), attrSign(p.resultado_usd)) + '</td>'
    + '<td>' + attrFig('pos:' + i + ':pct', labPct(p.pct_del_resultado, 1, false)) + '</td>'
    + '<td>' + attrFig('pos:' + i + ':pp', attrPp(p.aporte_pp), attrSign(p.aporte_pp)) + '</td></tr>').join('');
  return '<h4 class="lab-h4">' + labEsc(t('attr_positions')) + '</h4><div class="lab-table-wrap"><table class="lab-table"><thead><tr>'
    + th('attr_col_ticker', '', 1) + th('attr_col_vi', 'attr_tip_vi') + th('attr_col_flow', 'attr_tip_flow') + th('attr_col_vf', 'attr_tip_vf')
    + th('attr_col_result', 'attr_tip_result') + th('attr_col_pct', 'attr_tip_pct') + th('attr_col_pp', 'attr_tip_pp') + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function attrHowHtml(a) {
  return '<details class="lab-details"><summary>' + labEsc(t('attr_how_title')) + '</summary>'
    + ['attr_how_result', 'attr_how_dietz', 'attr_how_twr', 'attr_how_ops'].map(k => '<p class="lab-note">' + labEsc(t(k)) + '</p>').join('')
    + '<p class="lab-hint"><b>' + labEsc(t('attr_api_note')) + '</b> (' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/attribution</code>)</p>'
    + '<p class="lab-hint">' + labEsc(t('attr_api_method')) + ': ' + labEsc(a.metodo) + '. ' + labEsc(a.nota) + '</p>'
    + '<p class="lab-hint">' + labEsc(t('attr_api_method')) + ': ' + labEsc(a.metodo_operaciones) + '. ' + labEsc(a.nota_operaciones) + '</p></details>';
}
function attrOpRow(o) {
  const start = o.valuado_al_inicio === true, zero = o.costo_lote_usd === 0;
  const tipo = tHas('tipo_' + o.tipo) ? t('tipo_' + o.tipo) : o.tipo;
  const startTip = o.precio_ejecucion != null ? t('attr_tip_start', { p: labNum(o.precio_ejecucion, 2) }) : t('attr_tip_start_nop');
  const st = o.estado === 'realizado' || o.estado === 'no_realizado' ? '<span class="lab-pill ' + o.estado + '">' + labEsc(t('attr_state_' + o.estado)) + '</span>' : labEsc(o.estado);
  return '<tr><td class="txt lab-mono">' + labEsc(labDate(o.fecha)) + '</td><td class="txt">' + tickerBtn(o.ticker) + '</td>'
    + '<td class="txt"' + (o.nota ? ' title="' + labEsc(o.nota) + '"' : '') + '>' + labEsc(tipo)
    + (start ? ' <span class="lab-pill start" title="' + labEsc(startTip) + '">' + labEsc(t('attr_badge_start')) + '</span>' : '')
    + (zero ? ' <span class="lab-pill zero" title="' + labEsc(t('attr_tip_zero')) + '">' + labEsc(t('attr_badge_zero')) + '</span>' : '') + '</td>'
    + '<td>' + labQty(o.cantidad) + '</td><td>' + attrUsd2(o.costo_lote_usd) + '</td><td>' + labNum(o.precio_referencia, 2) + '</td>'
    + '<td class="' + attrSign(o.resultado_usd) + '">' + attrUsd2(o.resultado_usd) + '</td><td>' + (o.dias_en_cartera == null ? LAB_NA : labEsc(o.dias_en_cartera)) + '</td><td class="txt">' + st + '</td></tr>';
}
function attrOpsHtml(a) {
  const ops = a.operaciones || [];
  if (!ops.length) return '';
  const tickers = [...new Set(ops.map(o => o.ticker))].sort();
  if (!tickers.includes(S.attrTk)) S.attrTk = '';
  const th = (k, tip, txt) => '<th' + (txt ? ' class="txt"' : '') + (tip ? ' title="' + labEsc(t(tip)) + '"' : '') + '>' + labEsc(t(k)) + '</th>';
  return '<h4 class="lab-h4">' + labEsc(t('attr_ops')) + '</h4><div class="lab-toolbar">'
    + '<label class="lab-field">' + labEsc(t('attr_filter_ticker')) + ' <select id="labAttrTk"><option value="">' + labEsc(t('attr_all_tickers')) + '</option>'
    + tickers.map(x => '<option value="' + labEsc(x) + '"' + (x === S.attrTk ? ' selected' : '') + '>' + labEsc(x) + '</option>').join('') + '</select></label>'
    + '<label class="lab-field">' + labEsc(t('attr_filter_state')) + ' <select id="labAttrSt">' + ['', 'realizado', 'no_realizado'].map(v =>
      '<option value="' + v + '"' + (v === S.attrSt ? ' selected' : '') + '>' + labEsc(v ? t('attr_state_' + v) : t('attr_all_states')) + '</option>').join('') + '</select></label>'
    + '<span id="labAttrCount" class="lab-mono"></span><button type="button" class="lab-btn" id="labAttrMore">' + labEsc(t('attr_more')) + '</button></div>'
    + '<div class="lab-table-wrap"><table class="lab-table"><thead><tr>' + th('attr_col_date', '', 1) + th('attr_col_ticker', '', 1) + th('attr_col_type', '', 1) + th('attr_col_qty')
    + th('attr_col_cost', 'attr_tip_cost') + th('attr_col_ref', 'attr_tip_ref') + th('attr_col_res', 'attr_tip_res') + th('attr_col_days', 'attr_tip_days') + th('attr_col_state', '', 1)
    + '</tr></thead><tbody id="labAttrOps"></tbody></table></div>'
    + '<p class="lab-hint">' + labEsc(t('formula')) + ': <code class="lab-mono">' + labEsc(t('attr_f_ops')) + '</code></p>';
}
function updateAttrOps() {
  const a = S.data.attr, host = $('labAttrOps');
  if (!a || !host) return;
  const rows = (a.operaciones || []).filter(o => (!S.attrTk || o.ticker === S.attrTk) && (!S.attrSt || o.estado === S.attrSt))
    .sort((x, y) => (x.fecha < y.fecha ? 1 : x.fecha > y.fecha ? -1 : 0));
  const shown = rows.slice(0, S.attrLimit);
  host.innerHTML = shown.length ? shown.map(attrOpRow).join('') : '<tr><td class="txt" colspan="9">' + labEsc(t('attr_ops_empty')) + '</td></tr>';
  $('labAttrCount').textContent = t('attr_count', { n: shown.length, total: rows.length });
  $('labAttrMore').hidden = shown.length >= rows.length;
}
function paintAtribucion(body) {
  const a = S.data.attr;
  if (!(a.posiciones || []).length && !(a.operaciones || []).length) {
    body.innerHTML = '<div class="lab-state" style="display:flex">' + labEsc(t('attr_empty_range')) + '</div>';
    return;
  }
  const warns = Array.isArray(a.warnings) ? a.warnings : [];
  body.innerHTML = attrRecoHtml(a)
    + (warns.length ? '<ul class="lab-attr-list warn">' + warns.map(attrWarnHtml).join('') + '</ul>' : '')
    + attrSummaryHtml(a) + '<div class="lab-trace" id="labAttrTrace" hidden></div><p class="lab-hint">' + labEsc(t('attr_trace_hint')) + '</p>'
    + attrChartHtml(a) + attrPositionsHtml(a) + attrHowHtml(a) + attrOpsHtml(a);
  updateAttrOps();
  if (S.attrTrace && attrTraceDef(S.attrTrace)) showAttrTrace(S.attrTrace); else S.attrTrace = null;
}

/* ---------- Pruebas de estrés ---------- */
const stressSign = x => (x == null || !isFinite(x) ? '' : attrSign(x));
function stressScn(id) { const d = S.data.stress; return d && (d.escenarios || []).find(x => x.id === id) || null; }
function stressName(e) {
  if (tHas(e.nombre)) return t(e.nombre);
  return e.descripcion || e.nombre || e.id; // la descripción del backend solo si falta la clave i18n
}
function stressWindow(e) {
  const v = e.ventana || {}, a = v.cierre_desde || v.desde, b = v.cierre_hasta || v.hasta;
  return a && b ? labDate(a) + ' → ' + labDate(b) : LAB_NA;
}
function stressFig(key, text, cls) {
  return '<button type="button" class="lab-fig ' + (cls || '') + '" data-stress-trace="' + labEsc(key) + '" aria-expanded="' + (S.stressTrace === key) + '" title="' + labEsc(t('stress_trace_hint')) + '">' + labEsc(text) + '</button>';
}
function stressTraceDef(key) {
  const d = S.data.stress, p = labKeySplit(key);
  if (p[0] === 'pos') { const c = p[1].lastIndexOf(':'); p.splice(1, 1, p[1].slice(0, c), p[1].slice(c + 1)); } // 'pos:<id>:<fila>': el id puede llevar ':'
  const e = stressScn(p[1]);
  if (!d || !e) return null;
  const total = attrUsd2(d.valor_total_usd), win = stressWindow(e);
  if (p[0] === 'imp') return { title: stressName(e) + ' - ' + t('stress_impact'), f: 'stress_f_impact',
    inputs: [['stress_inp_window', win], ['stress_inp_n', String((e.posiciones || []).length)], ['stress_cov', labPct(e.cobertura_pct, 1, false)], ['stress_impact', labPct(e.impacto_pct, 2)]] };
  if (p[0] === 'usd') return { title: stressName(e) + ' - ' + t('stress_impact_usd'), f: 'stress_f_usd',
    inputs: [['stress_impact', labPct(e.impacto_pct, 2)], ['stress_inp_value', total], ['stress_impact_usd', attrUsd2(e.impacto_usd)]] };
  const row = (e.posiciones || [])[+p[2]];
  if (!row) return null;
  return { title: row.ticker + ' - ' + t('stress_col_contrib'), f: 'stress_f_contrib',
    inputs: [['stress_col_weight', labPct(row.peso_pct, 2, false)], ['stress_col_ret', labPct(row.retorno_pct, 2)], ['stress_col_contrib', labPct(row.aporte_pct, 2)], ['stress_inp_window', win]] };
}
function showStressTrace(key) {
  const box = $('labStressTrace'), d = box && stressTraceDef(key);
  if (!box || !d) return;
  S.stressTrace = key;
  box.hidden = false;
  box.innerHTML = '<h4>' + labEsc(d.title) + '</h4>'
    + '<div>' + labEsc(t('formula')) + ': <code>' + labEsc(t(d.f)) + '</code></div>'
    + '<div>' + labEsc(t('source')) + ': <code>' + labEsc('/api/lab/stress') + '</code></div>'
    + '<dl>' + d.inputs.map(x => '<dt>' + labEsc(t(x[0])) + '</dt><dd>' + labEsc(x[1]) + '</dd>').join('') + '</dl>';
  document.querySelectorAll('[data-stress-trace]').forEach(b => b.setAttribute('aria-expanded', String(b.dataset.stressTrace === key)));
}
function toggleStressTrace(key) {
  if (S.stressTrace === key) {
    S.stressTrace = null; $('labStressTrace').hidden = true;
    document.querySelectorAll('[data-stress-trace]').forEach(b => b.setAttribute('aria-expanded', 'false'));
  } else showStressTrace(key);
}
function stressWarnHtml(w) {
  const code = w && typeof w === 'object' ? w.tipo : null, key = 'stress_warn_' + code;
  const list = w && Array.isArray(w.tickers) ? w.tickers : (w && Array.isArray(w.detalle) ? w.detalle.map(x => (x && x.ticker) || '').filter(Boolean) : []);
  if (code && tHas(key)) return '<li>' + labEsc(t(key, { tickers: list.join(', ') })) + '</li>';
  const raw = w && typeof w === 'object' ? (w.mensaje || code || '') : w;
  return '<li>' + labEsc(t('stress_warn_generic')) + (raw ? ' <span class="lab-hint">' + labEsc(t('server_detail')) + ': ' + labEsc(raw) + '</span>' : '') + '</li>';
}
function stressTilesHtml(list) {
  return '<div class="lab-stress-tiles" role="group" aria-label="' + labEsc(t('stress_pick')) + '">' + list.map(e => {
    const ok = e.status === 'ok' && e.impacto_pct != null, sel = e.id === S.stressSel;
    const cov = e.cobertura_pct, partial = ok && cov != null && cov < 100;
    return '<div class="lab-stress-tile' + (sel ? ' sel' : '') + '">'
      + '<button type="button" class="lab-stress-pick" data-stress-pick="' + labEsc(e.id) + '" aria-pressed="' + sel + '">'
      + '<span class="nm">' + labEsc(stressName(e)) + '</span><span class="win lab-mono">' + labEsc(stressWindow(e)) + '</span></button>'
      + (ok
        ? '<div class="big">' + stressFig('imp:' + e.id, labPct(e.impacto_pct, 1), stressSign(e.impacto_pct)) + '</div>'
          + '<div class="usd">' + stressFig('usd:' + e.id, attrUsd2(e.impacto_usd), stressSign(e.impacto_usd)) + '</div>'
        : '<div class="big lab-mono">' + LAB_NA + '</div><div class="usd lab-hint">' + labEsc(t('stress_no_prices')) + '</div>')
      + (partial ? '<span class="lab-pill start" title="' + labEsc(t('stress_cov_tip')) + '">' + labEsc(t('stress_cov_partial', { c: labNum(cov, 1) })) + '</span>'
        : (ok && cov != null ? '<span class="lab-hint">' + labEsc(t('stress_cov_full')) + '</span>' : ''))
      + '</div>';
  }).join('') + '</div>';
}
function stressBarsHtml(e) {
  const rows = (e.posiciones || []).filter(p => p.aporte_pct != null && isFinite(p.aporte_pct)).sort((x, y) => x.aporte_pct - y.aporte_pct)
    .map(p => ({ ticker: p.ticker, value: p.aporte_pct, text: labPct(p.aporte_pct, 2),
      tip: t('stress_bar_tip', { t: p.ticker, v: labPct(p.aporte_pct, 2) }) + '. ' + t('open_in_dashboard', { t: p.ticker }) }));
  if (!rows.length) return '';
  return '<h4 class="lab-h4">' + labEsc(t('stress_chart_title')) + '</h4><div class="lab-svgwrap lab-attr-chart">' + labAttrBarsSvg(rows, t('stress_chart_aria')) + '</div>';
}
function stressPositionsHtml(e) {
  const th = (k, tip, txt) => '<th' + (txt ? ' class="txt"' : '') + (tip ? ' title="' + labEsc(t(tip)) + '"' : '') + '>' + labEsc(t(k)) + '</th>';
  const rows = (e.posiciones || []).map((p, i) => {
    const proxy = p.fuente_retorno === 'proxy'
      ? ' <span class="lab-pill start" title="' + labEsc(t('stress_proxy_tip')) + '">' + labEsc(t('stress_proxy', { i: p.indice_proxy ? entName(p.indice_proxy) : LAB_NA })) + '</span>' : '';
    return '<tr><td class="txt">' + tickerBtn(p.ticker) + proxy + '</td><td>' + labPct(p.peso_pct, 2, false) + '</td>'
      + '<td class="' + stressSign(p.retorno_pct) + '">' + labPct(p.retorno_pct, 1) + '</td>'
      + '<td>' + (p.aporte_pct == null ? LAB_NA : stressFig('pos:' + e.id + ':' + i, labPct(p.aporte_pct, 2), stressSign(p.aporte_pct))) + '</td>'
      + '<td class="' + stressSign(p.impacto_usd) + '">' + attrUsd2(p.impacto_usd) + '</td></tr>';
  }).join('');
  return '<h4 class="lab-h4">' + labEsc(t('stress_positions')) + '</h4><div class="lab-table-wrap"><table class="lab-table"><thead><tr>'
    + th('attr_col_ticker', '', 1) + th('stress_col_weight', 'stress_tip_weight') + th('stress_col_ret', 'stress_tip_ret') + th('stress_col_contrib', 'stress_tip_contrib') + th('stress_impact_usd', 'stress_tip_usd')
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function stressBlocksHtml(e) {
  const bl = e.bloques || [];
  if (!bl.length) return '';
  const th = (k, tip, txt) => '<th' + (txt ? ' class="txt"' : '') + (tip ? ' title="' + labEsc(t(tip)) + '"' : '') + '>' + labEsc(t(k)) + '</th>';
  const rows = bl.map(b => '<tr><td class="txt">' + labEsc(b.nombre || b.id) + '</td><td>' + labPct(b.peso_pct, 1, false) + '</td><td class="' + stressSign(b.retorno_pct) + '">' + labPct(b.retorno_pct, 1) + '</td>'
    + '<td class="' + stressSign(b.aporte_pct) + '">' + labPct(b.aporte_pct, 2) + '</td><td class="' + stressSign(b.impacto_usd) + '">' + attrUsd2(b.impacto_usd) + '</td>'
    + '<td>' + (b.cobertura_pct != null && b.cobertura_pct < 100 ? '<span class="lab-pill start" title="' + labEsc(t('stress_cov_tip')) + '">' + labEsc(labPct(b.cobertura_pct, 0, false)) + '</span>' : LAB_NA) + '</td></tr>').join('');
  return '<h4 class="lab-h4">' + labEsc(t('stress_blocks')) + '</h4><div class="lab-table-wrap"><table class="lab-table"><thead><tr>'
    + th('stress_col_block', '', 1) + th('stress_col_weight', 'stress_tip_weight') + th('stress_col_ret', 'stress_tip_ret') + th('stress_col_contrib', 'stress_tip_contrib') + th('stress_impact_usd', 'stress_tip_usd') + th('stress_cov', 'stress_cov_tip')
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function stressDetailHtml(e) {
  if (e.status !== 'ok') return '<div class="lab-state" style="display:flex">' + labEsc(t('stress_no_prices')) + (e.mensaje ? ' <span class="lab-hint">' + labEsc(e.mensaje) + '</span>' : '') + '</div>';
  const warns = Array.isArray(e.advertencias) ? e.advertencias : [], nd = Array.isArray(e.sin_dato) ? e.sin_dato : [];
  return '<h4 class="lab-h4">' + labEsc(stressName(e)) + ' <span class="lab-hint lab-mono">' + labEsc(stressWindow(e)) + '</span></h4>'
    + (e.nota ? '<p class="lab-hint">' + labEsc(t('stress_note')) + ': ' + labEsc(e.nota) + '</p>' : '')
    + (warns.length ? '<ul class="lab-attr-list warn">' + warns.map(stressWarnHtml).join('') + '</ul>' : '')
    + (nd.length ? '<p class="lab-hint"><b>' + labEsc(t('stress_nodata')) + ':</b> ' + nd.map(tickerBtn).join(', ') + '</p>' : '')
    + stressBarsHtml(e) + stressPositionsHtml(e) + stressBlocksHtml(e);
}
function paintEstres(body) {
  const d = S.data.stress, list = d.escenarios || [];
  if (!list.length) { body.innerHTML = '<div class="lab-state" style="display:flex">' + labEsc(t('stress_empty')) + '</div>'; return; }
  if (!stressScn(S.stressSel)) S.stressSel = list[0].id;
  const warns = Array.isArray(d.advertencias) ? d.advertencias : [];
  body.innerHTML = '<p class="lab-note lab-stress-disclaimer" role="note">' + labEsc(t('stress_disclaimer')) + '</p>'
    + stressTilesHtml(list) + '<div class="lab-trace" id="labStressTrace" hidden></div><p class="lab-hint">' + labEsc(t('stress_trace_hint')) + '</p>'
    + (warns.length ? '<ul class="lab-attr-list warn">' + warns.map(stressWarnHtml).join('') + '</ul>' : '')
    + '<div class="lab-stress-detail">' + stressDetailHtml(stressScn(S.stressSel)) + '</div>'
    + '<details class="lab-details"><summary>' + labEsc(t('stress_how_title')) + '</summary>'
    + ['stress_how_what', 'stress_how_calc', 'stress_how_proxy', 'stress_how_cov'].map(k => '<p class="lab-note">' + labEsc(t(k)) + '</p>').join('')
    + '<p class="lab-hint">' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/stress</code> · ' + labEsc(t('data_as_of')) + ' <b class="lab-mono">' + labEsc(labDate(d.datos_al)) + '</b></p></details>';
  if (S.stressTrace && stressTraceDef(S.stressTrace)) showStressTrace(S.stressTrace); else S.stressTrace = null;
}

const PAINT = {
  resumen: paintResumen, evolucion: paintEvolucion, periodos: paintPeriodos, drawdown: paintDrawdown,
  mapa: paintMapa, dispersion: paintDispersion, operaciones: paintOperaciones, conciliacion: paintConciliacion, exposicion: paintExposicion, movil: paintMovil, macro: paintMacro, atribucion: paintAtribucion, estres: paintEstres, diario: paintDiario,
};

/* ---------- Trazabilidad compartida (exposición y macro) ---------- */
function showLabTrace(stateKey, boxId, attr, key, defFn) {
  const box = $(boxId), d = box && defFn(key);
  if (!box || !d) return false;
  S[stateKey] = key;
  box.hidden = false;
  box.innerHTML = '<h4>' + labEsc(d.title) + '</h4>'
    + '<div>' + labEsc(t('formula')) + ': <code>' + labEsc(d.f) + '</code></div>'
    + '<div>' + labEsc(t('source')) + ': <code>' + labEsc(d.src) + '</code></div>'
    + '<dl>' + d.inputs.map(x => '<dt>' + labEsc(x[0]) + '</dt><dd>' + labEsc(x[1]) + '</dd>').join('') + '</dl>';
  document.querySelectorAll('[' + attr + ']').forEach(b => b.setAttribute('aria-expanded', String(b.getAttribute(attr) === key)));
  return true;
}
function toggleLabTrace(stateKey, boxId, attr, key, defFn) {
  if (S[stateKey] === key) {
    S[stateKey] = null; $(boxId).hidden = true;
    document.querySelectorAll('[' + attr + ']').forEach(b => b.setAttribute('aria-expanded', 'false'));
  } else showLabTrace(stateKey, boxId, attr, key, defFn);
}
function labFigBtn(attr, key, text, active, hint) {
  return '<button type="button" class="lab-fig" ' + attr + '="' + labEsc(key) + '" aria-expanded="' + (active === key) + '" title="' + labEsc(hint) + '">' + labEsc(text) + '</button>';
}

/* ---------- Exposición por bloque ---------- */
const EXPO_SRC = '/api/lab/exposure';
const EXPO_LEVEL_CLS = { ok: 'ok', aviso: 'start', alerta: 'diff' };
const expoBlock = (d, id) => (d.bloques || []).find(b => b.id === id) || null;
function blockName(b) { return b ? (tHas('block_' + b.id) ? t('block_' + b.id) : (b.nombre || b.id)) : LAB_NA; }
const expoPp = x => (x == null || !isFinite(x) ? LAB_NA : (x > 0 ? '+' : '') + labNum(x, 2) + ' pp');
const expoFig = (key, text) => labFigBtn('data-expo-trace', key, text, S.expoTrace, t('expo_trace_hint'));
function expoRule(d) { const r = d.regla_banda || {}; return { abs: r.absoluta_pp == null ? LAB_NA : String(r.absoluta_pp), rel: r.relativa == null ? LAB_NA : labPct(r.relativa * 100, 0, false), name: r.nombre || LAB_NA }; }
function expoBandText(b) { return b.banda ? t('expo_band_range', { a: labNum(b.banda.min_pct, 1) + '%', b: labNum(b.banda.max_pct, 1) + '%' }) : LAB_NA; }
function expoTraceDef(key) {
  const d = S.data.expo, p = labKeySplit(key), b = d && expoBlock(d, p[1]);
  if (!d || !b) return null;
  const rule = expoRule(d), rk = d.riesgo || {}, v = rk.ventana || {};
  const base = { src: EXPO_SRC };
  if (p[0] === 'w') return Object.assign({ title: blockName(b) + ' - ' + t('expo_col_weight'), f: t('expo_f_weight'),
    inputs: [[t('expo_inp_tickers'), (b.tickers || []).join(', ') || LAB_NA], [t('expo_inp_total'), attrUsd2(d.valor_total_usd)], [t('expo_col_weight'), labPct(b.peso_pct, 2, false)]] }, base);
  if (p[0] === 'ref') return Object.assign({ title: blockName(b) + ' - ' + t('expo_col_ref'), f: t('expo_ref_notice', { d: labDate(d.fecha_referencia) }),
    inputs: [[t('expo_inp_refdate'), labDate(d.fecha_referencia)], [t('expo_col_ref'), labPct(b.referencia_pct, 2, false)]] }, base);
  if (p[0] === 'b') return Object.assign({ title: blockName(b) + ' - ' + t('expo_trace_band'), f: t('expo_f_band', { abs: rule.abs, rel: rule.rel }),
    inputs: [[t('expo_col_ref'), labPct(b.referencia_pct, 2, false)], [t('expo_inp_band'), expoBandText(b)], [t('expo_trace_band'), b.banda ? '±' + labNum(b.banda.ancho_pp, 2) + ' pp (' + b.banda.manda + ')' : LAB_NA]] }, base);
  if (p[0] === 'dev') return Object.assign({ title: blockName(b) + ' - ' + t('expo_trace_dev'), f: t('expo_f_dev'),
    inputs: [[t('expo_col_weight'), labPct(b.peso_pct, 2, false)], [t('expo_col_ref'), labPct(b.referencia_pct, 2, false)], [t('expo_trace_dev'), expoPp(b.desvio_pp)]] }, base);
  if (p[0] === 'r') return Object.assign({ title: blockName(b) + ' - ' + t('expo_col_risk'), f: t('expo_f_risk'),
    inputs: [[t('expo_col_weight'), labPct(b.peso_pct, 2, false)], [t('expo_inp_window'), v.desde && v.hasta ? labDate(v.desde) + ' → ' + labDate(v.hasta) + ' (' + labNum(v.dias, 0) + ')' : LAB_NA],
      [t('expo_inp_cov'), labPct(b.cobertura_riesgo_pct, 1, false)], [t('expo_col_risk'), labPct(b.aporte_riesgo_pct, 2, false)]] }, base);
  return null;
}
function expoWarnHtml(w) {
  const code = w && typeof w === 'object' ? w.tipo : null;
  if (code && tHas('expo_warn_' + code)) return '<li>' + labEsc(t('expo_warn_' + code)) + '</li>';
  const raw = w && typeof w === 'object' ? (w.mensaje || code || '') : w;
  return '<li>' + labEsc(t('expo_warn_generic')) + (raw ? ' <span class="lab-hint">' + labEsc(t('server_detail')) + ': ' + labEsc(raw) + '</span>' : '') + '</li>';
}
function expoAlertHtml(d, a) {
  const code = a && a.codigo, key = 'expo_alert_' + code;
  if (!code || !tHas(key)) return '<li>' + labEsc(t('expo_alert_generic')) + (code ? ' <span class="lab-hint">' + labEsc(code) + '</span>' : '') + '</li>';
  const dir = tHas('expo_dir_' + a.direccion) ? t('expo_dir_' + a.direccion) : (a.direccion || LAB_NA);
  return '<li>' + labEsc(t(key, { b: blockName(expoBlock(d, a.bloque)), w: labPct(a.peso_pct, 1, false), r: labPct(a.referencia_pct, 1, false), dev: expoPp(a.desvio_pp), dir: dir,
    tk: a.ticker || LAB_NA, u: labPct(a.umbral_pct, 0, false) })) + '</li>';
}
function expoBlocksHtml(d) {
  const bl = d.bloques || [];
  const hi = Math.max(1, ...bl.map(b => Math.max(b.peso_pct || 0, b.referencia_pct || 0, b.banda ? b.banda.max_pct || 0 : 0))) * 1.08;
  const th = (k, tip, txt) => '<th' + (txt ? ' class="txt"' : '') + (tip ? ' title="' + labEsc(t(tip)) + '"' : '') + '>' + labEsc(t(k)) + '</th>';
  const rows = bl.map(b => {
    const nm = blockName(b), okSt = b.estado === 'dentro', known = tHas('expo_state_' + b.estado);
    const aria = t('expo_band_aria', { n: nm, w: labPct(b.peso_pct, 1, false), r: labPct(b.referencia_pct, 1, false), a: expoBandText(b) });
    const cov = b.cobertura_riesgo_pct != null && b.cobertura_riesgo_pct < 100;
    return '<tr><td class="txt"><b>' + labEsc(nm) + '</b><div class="lab-expo-tk">' + (b.tickers || []).map(tickerBtn).join(' ') + '</div></td>'
      + '<td class="txt"><div class="lab-svgwrap">' + labBandSvg({ min: b.banda && b.banda.min_pct, max: b.banda && b.banda.max_pct, ref: b.referencia_pct, weight: b.peso_pct, state: b.estado, aria: aria }, hi) + '</div>'
      + '<div class="lab-hint" style="margin:2px 0 0">' + labEsc(b.banda ? t('expo_band_rule_' + (b.banda.manda === 'relativa' ? 'relativa' : 'absoluta'), { v: labNum(b.banda.ancho_pp, 2), p: expoRule(d).rel }) + ' · ' + expoBandText(b) : LAB_NA) + '</div></td>'
      + '<td>' + (b.peso_pct == null ? LAB_NA : expoFig('w:' + b.id, labPct(b.peso_pct, 1, false))) + '</td>'
      + '<td>' + (b.referencia_pct == null ? LAB_NA : expoFig('ref:' + b.id, labPct(b.referencia_pct, 1, false))) + '</td>'
      + '<td>' + (b.desvio_pp == null ? LAB_NA : expoFig('dev:' + b.id, expoPp(b.desvio_pp))) + '</td>'
      + '<td class="txt"><span class="lab-pill ' + (okSt ? 'ok' : 'start') + '" title="' + labEsc(t('expo_state_tip')) + '">' + labEsc(known ? t('expo_state_' + b.estado) : (b.estado || LAB_NA)) + '</span></td>'
      + '<td>' + (b.aporte_riesgo_pct == null ? '<span title="' + labEsc(t('expo_risk_none')) + '">' + LAB_NA + '</span>' : expoFig('r:' + b.id, labPct(b.aporte_riesgo_pct, 1, false)))
      + (cov ? ' <span class="lab-pill start" title="' + labEsc(t('expo_risk_cov_tip')) + '">' + labEsc(t('expo_risk_cov', { c: labPct(b.cobertura_riesgo_pct, 0, false) })) + '</span>' : '') + '</td></tr>';
  }).join('');
  return '<div class="lab-table-wrap"><table class="lab-table"><thead><tr>' + th('expo_col_block', '', 1) + th('expo_col_band', 'expo_state_tip', 1)
    + th('expo_col_weight', 'expo_tip_weight') + th('expo_col_ref', 'expo_tip_ref') + th('expo_col_dev', 'expo_tip_dev') + th('expo_col_state', '', 1) + th('expo_col_risk', 'expo_tip_risk')
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
    + legendHtml([{ label: t('expo_legend_band'), color: labCss('--accent') }, { label: t('expo_legend_ref'), color: labCss('--text') }, { label: t('expo_legend_weight'), color: labCss('--green'), dot: 1 }]);
}
function expoRiskHtml(d) {
  const bl = d.bloques || [];
  if (!bl.length) return '';
  const rk = d.riesgo || {}, v = rk.ventana || {};
  const rows = bl.map(b => ({ name: blockName(b), w: b.peso_pct, r: b.aporte_riesgo_pct, wText: labPct(b.peso_pct, 1, false), rText: b.aporte_riesgo_pct == null ? LAB_NA : labPct(b.aporte_riesgo_pct, 1, false) }));
  return '<h4 class="lab-h4">' + labEsc(t('expo_risk_title')) + '</h4>'
    + legendHtml([{ label: t('expo_risk_weight'), color: labCss('--accent') }, { label: t('expo_risk_share'), color: labCss('--orange') }])
    + '<div class="lab-svgwrap">' + labPairBarsSvg(rows, t('expo_risk_aria')) + '</div>'
    + (v.desde && v.hasta ? '<p class="lab-hint">' + labEsc(t('expo_risk_total', { a: labDate(v.desde), b: labDate(v.hasta), n: labNum(v.dias, 0), c: labPct(rk.cobertura_pct, 0, false) })) + '</p>' : '');
}
function expoCapHtml(d) {
  const cap = d.tope_posicion || {}, ps = d.posiciones || [];
  const th = (k, txt) => '<th' + (txt ? ' class="txt"' : '') + '>' + labEsc(t(k)) + '</th>';
  const rows = ps.map(p => '<tr><td class="txt">' + tickerBtn(p.ticker) + '</td><td class="txt">' + labEsc(blockName(expoBlock(d, p.bloque) || { id: p.bloque })) + '</td><td>' + labPct(p.peso_pct, 1, false) + '</td>'
    + '<td class="txt"><span class="lab-pill ' + (EXPO_LEVEL_CLS[p.nivel] || 'sin_historial') + '">' + labEsc(tHas('expo_lvl_' + p.nivel) ? t('expo_lvl_' + p.nivel) : (p.nivel || LAB_NA)) + '</span>'
    + (p.etiqueta === 'excepcion' ? ' <span class="lab-pill zero" title="' + labEsc(t('expo_exception_tip')) + '">' + labEsc(t('expo_exception')) + '</span>' : '') + '</td>'
    + '<td class="txt">' + (p.motivo ? '<span class="lab-hint">' + labEsc(t('expo_motivo')) + ': ' + labEsc(p.motivo) + '</span>' : '') + '</td></tr>').join('');
  return '<h4 class="lab-h4">' + labEsc(t('expo_cap_title')) + '</h4><p class="lab-hint">' + labEsc(t('expo_cap_intro', { a: labPct(cap.aviso_pct, 0, false), b: labPct(cap.alerta_pct, 0, false) })) + '</p>'
    + '<div class="lab-table-wrap"><table class="lab-table"><thead><tr>' + th('expo_cap_col_ticker', 1) + th('expo_cap_col_block', 1) + th('expo_cap_col_weight') + th('expo_cap_col_level', 1) + th('expo_cap_col_note', 1)
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function expoHowHtml(d) {
  const rule = expoRule(d), rk = d.riesgo || {};
  return '<details class="lab-details"><summary>' + labEsc(t('expo_how_title')) + '</summary>'
    + '<p class="lab-note">' + labEsc(t('expo_how_band', rule)) + '</p><p class="lab-note">' + labEsc(t('expo_how_ref')) + '</p><p class="lab-note">' + labEsc(t('expo_how_risk')) + '</p>'
    + '<p class="lab-note">' + labEsc(t('expo_how_vol', { a: labPct(rk.vol_pesos_constantes_pct, 1, false), b: labPct(rk.vol_realizada_pct, 1, false) })) + '</p>'
    + (rk.metodo || rk.nota ? '<p class="lab-hint">' + labEsc(t('expo_how_detail')) + ': ' + labEsc([rk.metodo, rk.nota].filter(Boolean).join(' ')) + '</p>' : '')
    + '<p class="lab-hint">' + labEsc(t('source')) + ': <code class="lab-mono">' + EXPO_SRC + '</code> · ' + labEsc(t('data_as_of')) + ' <b class="lab-mono">' + labEsc(labDate(d.datos_al)) + '</b></p></details>';
}
function paintExposicion(body) {
  const d = S.data.expo, warns = Array.isArray(d.advertencias) ? d.advertencias : [], alerts = Array.isArray(d.alertas) ? d.alertas : [], nop = Array.isArray(d.sin_precio) ? d.sin_precio : [];
  if (!(d.bloques || []).length) { body.innerHTML = '<div class="lab-state" style="display:flex">' + labEsc(t('state_empty')) + '</div>'; return; }
  body.innerHTML = '<p class="lab-note lab-stress-disclaimer" role="note">' + labEsc(t('expo_ref_notice', { d: labDate(d.fecha_referencia) })) + '</p>'
    + '<p class="lab-hint">' + labEsc(t('expo_total')) + ': <b class="lab-mono">' + labEsc(attrUsd2(d.valor_total_usd)) + '</b></p>'
    + (nop.length ? '<ul class="lab-attr-list warn"><li>' + labEsc(t('expo_no_price', { tickers: nop.join(', ') })) + '</li></ul>' : '')
    + (warns.length ? '<ul class="lab-attr-list warn">' + warns.map(expoWarnHtml).join('') + '</ul>' : '')
    + expoBlocksHtml(d) + '<div class="lab-trace" id="labExpoTrace" hidden></div><p class="lab-hint">' + labEsc(t('expo_trace_hint')) + '</p>'
    + '<h4 class="lab-h4">' + labEsc(t('expo_alerts_title')) + '</h4>'
    + (alerts.length ? '<ul class="lab-attr-list warn">' + alerts.map(a => expoAlertHtml(d, a)).join('') + '</ul>' : '<p class="lab-hint">' + labEsc(t('expo_no_alerts')) + '</p>')
    + expoRiskHtml(d) + expoCapHtml(d) + expoHowHtml(d);
  if (S.expoTrace && expoTraceDef(S.expoTrace)) showLabTrace('expoTrace', 'labExpoTrace', 'data-expo-trace', S.expoTrace, expoTraceDef); else S.expoTrace = null;
}

/* ---------- Correlación móvil ---------- */
const MOV_COLORS = ['--orange', '--amber', '--accent', '--green']; // por posición en la lista de la API; la leyenda nombra cada línea
const movName = r => (r.nombre && (r.nombre[labLang] || r.nombre.es || r.nombre.en)) || r.id;
const movColor = i => labCss(MOV_COLORS[i % MOV_COLORS.length]);
const movLim = () => S.movLim || { min: NaN, max: NaN };
function movInputValue() { const el = $('labMovWin'), s = el ? el.value.trim() : ''; const n = Number(s); return s && Number.isInteger(n) ? n : null; }
function movValid(n) { const l = movLim(); return n != null && n >= l.min && n <= l.max; }
function updateMovBtn() {
  const b = $('labMovApply'); if (!b) return;
  const n = movInputValue(), same = n === S.movWin, bad = !movValid(n), off = bad || same;
  b.disabled = off; b.setAttribute('aria-disabled', String(off));
  b.title = bad ? t('mov_window_tip', { min: movLim().min, max: movLim().max }) : (same ? t('mov_shown', { w: S.movWin }) : '');
  $('labMovWin').setAttribute('aria-invalid', String(bad));
}
function applyMovWindow() {
  const n = movInputValue();
  if (!movValid(n) || n === S.movWin) return;
  S.movWin = n; loadGroup('mov');
}
function movAviso(r, w) {
  const k = 'mov_aviso_' + r.aviso;
  return tHas(k) ? t(k, { w: w }) : t('mov_aviso_generic') + (r.aviso ? ' (' + r.aviso + ')' : '');
}
function paintMovil(body) {
  destroyChart('movil');
  const c = S.data.mov, m = c.movil || {}, refs = m.referencias || [], dates = c.dates || [], w = m.ventana, l = movLim();
  const lines = refs.map((r, i) => ({ r: r, i: i, ok: r.estado === 'ok' && Array.isArray(r.valores) }))
    .map(x => Object.assign(x, { data: x.ok ? dates.map((d, k) => ({ time: d, value: x.r.valores[k] })).filter(p => p.value != null && isFinite(p.value)) : [] }));
  const plotted = lines.filter(x => x.data.length);
  const tiles = lines.map(x => '<div class="lab-mov-tile' + (x.data.length ? '' : ' off') + '">'
    + '<div class="k"><i class="dot" style="background:' + labEsc(movColor(x.i)) + '"></i>' + labEsc(movName(x.r)) + '</div>'
    + (x.data.length ? '<div class="v lab-mono" title="' + labEsc(t('mov_what', { w: w })) + '">' + labEsc(labNum(x.r.ultimo, 2)) + '</div><div class="lab-hint">' + labEsc(t('mov_current')) + '</div>'
      : '<div class="v lab-mono">' + LAB_NA + '</div><div class="lab-hint" role="status"><b>' + labEsc(t('mov_state_sin_datos')) + '.</b> ' + labEsc(movAviso(x.r, w)) + '</div>') + '</div>').join('');
  body.innerHTML = '<div class="lab-toolbar"><label class="lab-field">' + labEsc(t('mov_window')) + ' <input type="number" id="labMovWin" min="' + labEsc(l.min) + '" max="' + labEsc(l.max) + '" step="1" value="' + labEsc(w) + '" title="' + labEsc(t('mov_window_tip', { min: l.min, max: l.max })) + '"></label>'
    + '<button type="button" class="lab-btn" id="labMovApply">' + labEsc(t('mov_apply')) + '</button>'
    + '<span class="lab-mono">' + labEsc(t('mov_shown', { w: w })) + '</span>'
    + '<span class="lab-info" tabindex="0" role="note" title="' + labEsc(t('mov_what', { w: w })) + '">?</span></div>'
    + '<div class="lab-mov-tiles">' + tiles + '</div>'
    + (plotted.length
      ? legendHtml(plotted.map(x => ({ label: movName(x.r), color: movColor(x.i) }))) + '<div class="lab-plot" id="labPlotMov" role="img" aria-label="' + labEsc(t('mov_aria')) + '"></div>'
      : '<div class="lab-state" style="display:flex">' + labEsc(t('mov_none')) + '</div>')
    + '<p class="lab-note">' + labEsc(t('mov_what', { w: w })) + '</p>'
    + '<p class="lab-hint">' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/correlation</code></p>';
  updateMovBtn();
  if (plotted.length) S.charts.movil = labLineChart($('labPlotMov'), plotted.map(x => ({ id: x.r.id, label: movName(x.r), color: movColor(x.i), width: 2, data: x.data })), { fmt: v => (v == null ? LAB_NA : labNum(v, 2)) });
}

/* ---------- Semáforo macro ---------- */
const MACRO_LIGHTS = ['verde', 'ambar', 'rojo', 'sin_datos'];
const MACRO_REG_CLS = { favorable: 'ok', mixto: 'start', adverso: 'diff', sin_datos_suficientes: 'sin_historial' };
const macroLuz = k => (MACRO_LIGHTS.indexOf(k) >= 0 ? k : 'sin_datos');
const macroName = i => (i.nombre && (i.nombre[labLang] || i.nombre.es || i.nombre.en)) || i.id;
const macroUnit = u => (u == null ? '' : (tHas('macro_unit_' + u) ? t('macro_unit_' + u) : u));
const macroLuzText = k => t('macro_luz_' + macroLuz(k));
function macroLamp(k, n) {
  return '<span class="lab-lampwrap" title="' + labEsc(t('macro_luz_tip', { n: n == null ? LAB_NA : n })) + '"><i class="lab-lamp ' + macroLuz(k) + '" aria-hidden="true"></i><b>' + labEsc(macroLuzText(k)) + '</b></span>';
}
const macroInd = (d, id) => (d.indicadores || []).find(i => i.id === id) || null;
function macroValue(i) { return i.valor_actual == null || !isFinite(i.valor_actual) ? LAB_NA : labNum(i.valor_actual, 2) + (i.unidad ? ' ' + macroUnit(i.unidad) : ''); }
function macroTraceDef(key) {
  const d = S.data.macro, i = d && macroInd(d, key);
  if (!i) return null;
  return { title: macroName(i), f: t('macro_f_light', { n: d.histeresis_dias == null ? LAB_NA : d.histeresis_dias }), src: '/api/lab/macro',
    inputs: [[t('macro_inp_metric'), i.metrica || LAB_NA], [t('macro_inp_series'), (i.series_origen || []).join(', ') || LAB_NA], [t('macro_value'), macroValue(i)],
      [t('macro_inp_raw'), macroLuzText(i.luz_cruda)], [t('macro_inp_light'), macroLuzText(i.luz)], [t('macro_inp_since'), i.desde ? labDate(i.desde) : LAB_NA]] };
}
function macroTileHtml(d, i) {
  const v = d.ventanas || {}, aux = i.auxiliar_actual || {}, s = i.serie || {}, f = s.fechas || [];
  const hasAvg = s.auxiliares && Array.isArray(s.auxiliares.promedio_movil) && s.auxiliares.promedio_movil.some(q => q != null);
  const diff = i.luz_cruda && i.luz_cruda !== i.luz;
  const auxLines = [];
  if (aux.promedio_movil != null) auxLines.push(t('macro_avg', { n: v.promedio_movil_dias == null ? LAB_NA : v.promedio_movil_dias }) + ': ' + labNum(aux.promedio_movil, 2));
  if (aux.cambio_pct != null) auxLines.push(t('macro_change', { n: v.cambio_dias_habiles == null ? LAB_NA : v.cambio_dias_habiles }) + ': ' + labPct(aux.cambio_pct, 2));
  const svg = labMacroSvg(i, t('macro_chart_aria', { n: macroName(i) }), 'promedio_movil');
  return '<div class="lab-macro-tile"><h4 class="lab-h4" style="margin-top:0">' + labEsc(macroName(i)) + '</h4>'
    + '<div class="lab-macro-head">' + macroLamp(i.luz, d.histeresis_dias) + '<span class="lab-macro-val">' + labFigBtn('data-macro-trace', i.id, macroValue(i), S.macroTrace, t('macro_trace_hint')) + '</span></div>'
    + '<div class="lab-hint">' + labEsc(i.desde ? t('macro_since', { d: labDate(i.desde) }) : LAB_NA) + '</div>'
    + (diff ? '<div class="lab-macro-note" role="note">' + labEsc(t('macro_hysteresis_note', { raw: macroLuzText(i.luz_cruda), n: d.histeresis_dias == null ? LAB_NA : d.histeresis_dias })) + '</div>' : '')
    + (auxLines.length ? '<div class="lab-hint">' + auxLines.map(labEsc).join(' · ') + '</div>' : '')
    + (svg ? '<div class="lab-svgwrap">' + svg + '</div><div class="lab-hint lab-mono">' + (f.length ? labEsc(t('macro_chart_range', { a: labDate(f[0]), b: labDate(f[f.length - 1]) })) : '')
      + (hasAvg ? ' · <span class="lab-dash"></span>' + labEsc(t('macro_avg', { n: v.promedio_movil_dias == null ? LAB_NA : v.promedio_movil_dias })) : '') + '</div>' : '')
    + '</div>';
}
function macroCalibHtml(d) {
  const c = d.calibracion;
  if (!c) return '';
  const th = (k, txt) => '<th' + (txt ? ' class="txt"' : '') + '>' + labEsc(t(k)) + '</th>';
  const pct = (o, k) => labPct(o && o.pct ? o.pct[k] : null, 1, false);
  const rows = (d.indicadores || []).filter(i => c.indicadores && c.indicadores[i.id]).map(i => { const o = c.indicadores[i.id];
    return '<tr><td class="txt">' + labEsc(macroName(i)) + '</td>' + MACRO_LIGHTS.map(k => '<td>' + pct(o, k) + '</td>').join('') + '</tr>'; }).join('');
  const regs = Object.keys(MACRO_REG_CLS);
  const rev = Array.isArray(d.para_revision) ? d.para_revision : [];
  const names = rev.map(x => { const i = macroInd(d, x && x.id); return (i ? macroName(i) : (x && x.id) || LAB_NA) + (x && x.pct_rojo != null ? ' (' + labPct(x.pct_rojo, 1, false) + ')' : ''); }).join(', ');
  const u = labPct(c.umbral_revision_pct_rojo, 0, false);
  return '<h4 class="lab-h4">' + labEsc(t('macro_calib_title')) + '</h4><p class="lab-hint">' + labEsc(t('macro_calib_intro', { a: labDate(c.desde), b: labDate(c.hasta), n: labNum(c.dias, 0) })) + '</p>'
    + (rev.length ? '<div class="lab-jr-alert" role="alert">' + labEsc(t('macro_review_alert', { list: names, u: u })) + '</div>' : '<p class="lab-hint">' + labEsc(t('macro_review_none', { u: u })) + '</p>')
    + '<div class="lab-table-wrap"><table class="lab-table"><thead><tr>' + th('macro_col_ind', 1) + th('macro_col_verde') + th('macro_col_ambar') + th('macro_col_rojo') + th('macro_col_sin') + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
    + '<div class="lab-table-wrap"><table class="lab-table"><thead><tr>' + th('macro_calib_regime', 1) + regs.map(k => '<th>' + labEsc(t('macro_reg_' + k)) + '</th>').join('') + '</tr></thead><tbody><tr><td class="txt">' + labEsc(t('macro_regime_title')) + '</td>'
    + regs.map(k => '<td>' + pct(c.regimen, k) + '</td>').join('') + '</tr></tbody></table></div>';
}
function paintMacro(body) {
  const d = S.data.macro, g = d.regimen || {}, list = d.indicadores || [], miss = Array.isArray(d.series_faltantes) ? d.series_faltantes : [];
  if (!list.length) { body.innerHTML = '<div class="lab-state" style="display:flex">' + labEsc(t('state_empty')) + '</div>'; return; }
  const rk = 'macro_reg_' + g.etiqueta, rules = 'macro_rules_' + d.estado_reglas;
  body.innerHTML = '<p class="lab-note lab-stress-disclaimer" role="note">' + labEsc(t('macro_notice_context')) + ' ' + labEsc(tHas(rules) ? t(rules) : t('macro_rules_generic', { s: d.estado_reglas == null ? LAB_NA : d.estado_reglas })) + '</p>'
    + '<div class="lab-macro-regime"><span class="k">' + labEsc(t('macro_regime_title')) + '</span> <span class="lab-pill ' + (MACRO_REG_CLS[g.etiqueta] || 'sin_historial') + '">' + labEsc(tHas(rk) ? t(rk) : (g.etiqueta || LAB_NA)) + '</span> '
    + '<span class="lab-mono">' + labEsc(t('macro_reg_detail', { r: g.n_rojas == null ? LAB_NA : g.n_rojas, v: g.n_validas == null ? LAB_NA : g.n_validas })) + '</span> '
    + '<span class="lab-hint">' + labEsc(g.desde ? t('macro_reg_since', { d: labDate(g.desde) }) : '') + '</span>'
    + (g.minimo_luces_validas != null ? '<div class="lab-hint">' + labEsc(t('macro_reg_min', { m: g.minimo_luces_validas })) + '</div>' : '') + '</div>'
    + (miss.length ? '<ul class="lab-attr-list warn"><li>' + labEsc(t('macro_missing', { s: miss.join(', ') })) + '</li></ul>' : '')
    + '<div class="lab-macro-grid">' + list.map(i => macroTileHtml(d, i)).join('') + '</div>'
    + '<div class="lab-trace" id="labMacroTrace" hidden></div><p class="lab-hint">' + labEsc(t('macro_trace_hint')) + '</p>'
    + macroCalibHtml(d)
    + '<p class="lab-hint">' + labEsc(t('source')) + ': <code class="lab-mono">/api/lab/macro</code> · ' + labEsc(t('data_as_of')) + ' <b class="lab-mono">' + labEsc(labDate(d.datos_al)) + '</b></p>';
  if (S.macroTrace && macroTraceDef(S.macroTrace)) showLabTrace('macroTrace', 'labMacroTrace', 'data-macro-trace', S.macroTrace, macroTraceDef); else S.macroTrace = null;
}

/* ---------- Ventas recortadas (dentro de la conciliación) ---------- */
function trimmedSalesHtml(v) {
  if (!v) return '';
  const head = '<h4 class="lab-h4">' + labEsc(t('reco_trim_title')) + ' <span class="lab-info" tabindex="0" role="note" title="' + labEsc(t('reco_trim_what')) + '">?</span></h4>';
  if (v.status !== 'ok' || !Array.isArray(v.lista)) return head + '<p class="lab-hint">' + labEsc(t('reco_trim_no_prices')) + '</p>';
  const x = v.umbral_pct == null ? LAB_NA : v.umbral_pct.toLocaleString(labLocale(), { maximumFractionDigits: 2 }) + '%';
  const th = (k, txt, tip) => '<th' + (txt ? ' class="txt"' : '') + (tip ? ' title="' + labEsc(t(tip)) + '"' : '') + '>' + labEsc(t(k)) + '</th>';
  const rows = v.lista.map(r => '<tr><td class="txt">' + tickerBtn(r.ticker) + '</td><td class="txt lab-mono">' + labEsc(labDate(r.fecha)) + '</td><td>' + labQty(r.exceso_unidades) + '</td>'
    + '<td' + (r.exceso_pct == null ? ' title="' + labEsc(t('reco_trim_pct_tip')) + '"' : '') + '>' + (r.exceso_pct == null ? LAB_NA : labNum(r.exceso_pct, 2) + '%') + '</td><td>' + labQty(r.pedido) + '</td><td>' + labQty(r.tenencia) + '</td></tr>').join('');
  return head + '<p class="lab-hint">' + labEsc(t('reco_trim_threshold', { x: x })) + '</p>'
    + (v.lista.length ? '<div class="lab-table-wrap"><table class="lab-table"><thead><tr>' + th('reco_col_ticker', 1) + th('reco_trim_cols_date', 1) + th('reco_trim_cols_units') + th('reco_trim_cols_pct', 0, 'reco_trim_pct_tip')
      + th('reco_trim_cols_req') + th('reco_trim_cols_held') + '</tr></thead><tbody>' + rows + '</tbody></table></div>' : '<p class="lab-hint">' + labEsc(t('reco_trim_none', { x: x })) + '</p>')
    + (v.bajo_umbral > 0 ? '<p class="lab-hint">' + labEsc(t('reco_trim_below', { n: v.bajo_umbral, x: x })) + '</p>' : '');
}

/* ---------- Diario de decisiones ---------- */
/* Solo muestra el estado de las reglas que escribió la usuaria. La tesis y la nota son texto libre: siempre labEsc, nunca crudo,
   y viven solo en memoria (no se guardan en localStorage). Los límites vienen del HTML (data-max-*) y espejan los del servidor. */
const JR_URL = '/api/lab/journal';
const JR_TIPOS = ['precio', 'peso', 'fecha'];
const JR_OPS = ['>=', '<='];
const JR_FILTERS = ['all', 'open', 'closed'];
const JR_EVAL_KEY = { cumplida: 'met', no_cumplida: 'notmet', sin_dato: 'nodata' };
const JR_DETAIL_KEYS = ['precio_cumple', 'precio_no_cumple', 'peso_cumple', 'peso_no_cumple', 'tenencia_cero', 'fecha_alcanzada', 'fecha_pendiente', 'sin_precio_ticker', 'sin_precios'];
const JR_TICKER_RE = /^[A-Z0-9][A-Z0-9.\-]*$/;
const jrLim = k => (S.jrLim ? S.jrLim[k] : NaN); // sin límite conocido: NaN (las comparaciones dan falso)
const jrLen = s => Array.from(String(s == null ? '' : s)).length;
const jrBlank = () => ({ ticker: '', tesis: '', tipo: 'precio', op: '>=', valor: '' });
const jrStatus = () => (S.data.journal ? S.data.journal.status : null);
const jrCorrupt = () => jrStatus() === 'journal_corrupto';
const jrFull = () => { const r = S.data.journal && S.data.journal.resumen; return !!(r && jrLim('entries') > 0 && r.total >= jrLim('entries')); };
const jrOff = () => (jrCorrupt() ? 'journal_form_off_corrupt' : (jrFull() ? 'journal_form_off_limit' : null));
const jrErrText = e => t(e.k, e.v) + (e.raw ? ' ' + t('server_detail') + ': ' + e.raw : '');
const jrErrHtml = e => (e ? '<span class="lab-jr-err" role="alert">' + labEsc(jrErrText(e)) + '</span>' : '');
const jrErrs = sc => (sc === 'ed' ? S.jr.rowErr.e : S.jr.nwErr);
const jrEntries = () => (S.data.journal && S.data.journal.entries) || [];
const jrEntry = id => jrEntries().find(e => e.id === id) || null;

function jrNum(s) { const x = String(s == null ? '' : s).trim(); if (!x) return null; const n = Number(x); return isFinite(n) ? n : null; }
function jrValidDate(s) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(s))) return false;
  const d = new Date(s + 'T00:00:00Z');
  return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}
function jrTickers() {
  const led = S.data.ledger, seen = {};
  ((led && led.ops) || []).forEach(o => { if (o && o.ticker) seen[String(o.ticker).toUpperCase()] = 1; });
  return Object.keys(seen).sort();
}
function jrCond(f) {
  if (f.tipo === 'fecha') return { tipo: 'fecha', operador: null, valor: f.valor };
  return { tipo: f.tipo, operador: f.op, valor: jrNum(f.valor) };
}
/* Espeja las reglas del servidor (lab/journal.py); el servidor sigue siendo la validación real. */
function jrValidate(f, withTicker) {
  const e = {};
  if (withTicker) {
    const tk = f.ticker.trim().toUpperCase();
    if (!tk || jrLen(tk) > jrLim('ticker') || !JR_TICKER_RE.test(tk)) e.ticker = { k: 'journal_v_ticker' };
  }
  if (!f.tesis.trim()) e.tesis = { k: 'journal_v_tesis_empty' };
  else if (jrLen(f.tesis) > jrLim('tesis')) e.tesis = { k: 'journal_v_tesis_long', v: { max: jrLim('tesis') } };
  if (f.tipo === 'fecha') {
    if (!jrValidDate(f.valor)) e.cond = { k: 'journal_v_valor_fecha' };
  } else if (JR_OPS.indexOf(f.op) < 0) e.cond = { k: 'journal_v_op' };
  else {
    const v = jrNum(f.valor);
    if (f.tipo === 'precio' && (v == null || v <= 0)) e.cond = { k: 'journal_v_valor_precio' };
    if (f.tipo === 'peso' && (v == null || v < 0 || v > 100)) e.cond = { k: 'journal_v_valor_peso' };
  }
  return e;
}

/* Formato legible de una condición y de un valor según su tipo (null -> guion, nunca 0). */
function jrOpSym(op) { return op === '>=' ? '≥' : (op === '<=' ? '≤' : null); }
function jrFmtVal(tipo, v) {
  if (v == null) return LAB_NA;
  if (tipo === 'precio') return typeof v === 'number' ? attrUsd2(v) : LAB_NA;
  if (tipo === 'peso') return typeof v === 'number' ? labPct(v, 2, false) : LAB_NA;
  if (tipo === 'fecha') return labDate(v);
  return LAB_NA;
}
function jrTargetText(c) {
  if (!c || JR_TIPOS.indexOf(c.tipo) < 0 || c.valor == null) return LAB_NA;
  if (c.tipo === 'fecha') return jrFmtVal('fecha', c.valor);
  const op = jrOpSym(c.operador);
  return op ? op + ' ' + jrFmtVal(c.tipo, c.valor) : LAB_NA;
}
function jrCondText(c) {
  if (!c || JR_TIPOS.indexOf(c.tipo) < 0 || c.valor == null) return t('journal_cond_unknown');
  if (c.tipo === 'fecha') return t('journal_cond_fecha', { v: jrFmtVal('fecha', c.valor) });
  const op = jrOpSym(c.operador);
  return op ? t('journal_cond_' + c.tipo, { op, v: jrFmtVal(c.tipo, c.valor) }) : t('journal_cond_unknown');
}
function jrPill(k) {
  return '<span class="lab-pill jr-' + k + '" title="' + labEsc(t('journal_st_' + k + '_tip')) + '">' + labEsc(t('journal_st_' + k)) + '</span>';
}

/* ---- formulario (alta y edición comparten los campos) ---- */
function jrDis() { return !!jrOff() || S.jr.busy; }
function jrCountText(n, max) { return t('journal_count', { n: n, max: max }); }
function jrCountHtml(sc, f, val, max) {
  return '<span class="lab-hint lab-jr-count' + (jrLen(val) > max ? ' over' : '') + '" id="jr-' + sc + '-' + f + '-count">' + labEsc(jrCountText(jrLen(val), max)) + '</span>';
}
function jrCondValHtml(sc, o) {
  const id = 'jr-' + sc + '-';
  const op = o.tipo === 'fecha' ? '' : '<label class="lab-jr-lbl" for="' + id + 'op">' + labEsc(t('journal_op')) + '</label>'
    + '<select class="lab-jr-in" id="' + id + 'op" data-jr="' + sc + '" data-jr-f="op">'
    + JR_OPS.map(x => '<option value="' + labEsc(x) + '"' + (o.op === x ? ' selected' : '') + '>' + labEsc(t(x === '>=' ? 'journal_op_ge' : 'journal_op_le')) + '</option>').join('') + '</select>';
  const inp = o.tipo === 'fecha'
    ? '<input class="lab-jr-in" id="' + id + 'valor" type="date" data-jr="' + sc + '" data-jr-f="valor" value="' + labEsc(o.valor) + '">'
    : '<input class="lab-jr-in" id="' + id + 'valor" type="number" step="any" inputmode="decimal" data-jr="' + sc + '" data-jr-f="valor" value="' + labEsc(o.valor) + '">';
  return op + '<label class="lab-jr-lbl" for="' + id + 'valor">' + labEsc(t('journal_valor_' + o.tipo)) + '</label>' + inp + jrErrHtml(jrErrs(sc).cond);
}
function jrCondHtml(sc, o) {
  return '<div class="lab-jr-cond"><label class="lab-jr-lbl" for="jr-' + sc + '-tipo">' + labEsc(t('journal_tipo')) + '</label>'
    + '<select class="lab-jr-in" id="jr-' + sc + '-tipo" data-jr="' + sc + '" data-jr-f="tipo">'
    + JR_TIPOS.map(k => '<option value="' + k + '"' + (o.tipo === k ? ' selected' : '') + '>' + labEsc(t('journal_tipo_' + k)) + '</option>').join('') + '</select>'
    + '<span class="lab-jr-condv" id="jr-condv-' + sc + '">' + jrCondValHtml(sc, o) + '</span></div>';
}
function jrTesisHtml(sc, o) {
  return '<label class="lab-jr-lbl" for="jr-' + sc + '-tesis">' + labEsc(t('journal_tesis')) + '</label>'
    + '<textarea class="lab-jr-in lab-jr-ta" id="jr-' + sc + '-tesis" rows="4" data-jr="' + sc + '" data-jr-f="tesis" placeholder="' + labEsc(t('journal_tesis_ph')) + '">' + labEsc(o.tesis) + '</textarea>'
    + jrCountHtml(sc, 'tesis', o.tesis, jrLim('tesis')) + jrErrHtml(jrErrs(sc).tesis);
}
function jrSubmitBtn(labelKey) {
  const off = jrOff(), dis = jrDis(), why = off ? t(off) : (S.jr.busy ? t('journal_busy') : '');
  return '<button type="submit" class="lab-btn lab-jr-save"' + (dis ? ' disabled' : '') + ' aria-disabled="' + dis + '"' + (why ? ' title="' + labEsc(why) + '"' : '') + '>'
    + labEsc(S.jr.busy ? t('journal_saving') : t(labelKey)) + '</button>';
}
function jrFormHtml() {
  const f = S.jr.nw, off = jrOff(), dis = jrDis();
  return '<form class="lab-jr-form" data-jr-form="nw" novalidate><h4 class="lab-h4">' + labEsc(t('journal_form_title')) + '</h4>'
    + (off ? '<p class="lab-jr-off" role="note">' + labEsc(t(off)) + '</p>' : '')
    + '<fieldset class="lab-jr-fs"' + (dis ? ' disabled' : '') + '>'
    + '<label class="lab-jr-lbl" for="jr-nw-ticker" title="' + labEsc(t('journal_ticker_tip')) + '">' + labEsc(t('journal_ticker')) + '</label>'
    + '<input class="lab-jr-in" id="jr-nw-ticker" type="text" list="jrTickers" autocomplete="off" spellcheck="false" data-jr="nw" data-jr-f="ticker" value="' + labEsc(f.ticker) + '" placeholder="' + labEsc(t('journal_ticker_ph')) + '" title="' + labEsc(t('journal_ticker_tip')) + '">'
    + '<datalist id="jrTickers">' + jrTickers().map(tk => '<option value="' + labEsc(tk) + '"></option>').join('') + '</datalist>'
    + jrErrHtml(S.jr.nwErr.ticker)
    + jrTesisHtml('nw', f)
    + '<h5 class="lab-jr-h5">' + labEsc(t('journal_cond_title')) + '</h5>' + jrCondHtml('nw', f)
    + (S.jr.nwErr.gen ? jrErrHtml(S.jr.nwErr.gen) : '')
    + '<div class="lab-jr-actions">' + jrSubmitBtn('journal_save') + '</div></fieldset></form>';
}

/* ---- lista ---- */
function jrBtn(act, id, key) {
  const dis = S.jr.busy;
  return '<button type="button" class="lab-btn" data-jr-act="' + act + '" data-jr-id="' + labEsc(id) + '"' + (dis ? ' disabled aria-disabled="true" title="' + labEsc(t('journal_busy')) + '"' : ' aria-disabled="false"') + '>' + labEsc(t(key)) + '</button>';
}
function jrTraceHtml(e) {
  const c = e.condicion_salida || {}, ev = e.evaluacion || {};
  return '<div class="lab-trace"><h4>' + labEsc(e.ticker + ' - ' + t('journal_trace_rule')) + '</h4>'
    + (JR_TIPOS.indexOf(c.tipo) >= 0 ? '<div>' + labEsc(t('formula')) + ': <code>' + labEsc(t('journal_f_' + c.tipo)) + '</code></div>' : '')
    + '<div>' + labEsc(t('source')) + ': <code>' + labEsc(JR_URL) + '</code></div>'
    + '<dl><dt>' + labEsc(t('journal_trace_rule')) + '</dt><dd>' + labEsc(jrCondText(c)) + '</dd>'
    + '<dt>' + labEsc(t('journal_trace_value')) + '</dt><dd>' + labEsc(jrFmtVal(c.tipo, ev.valor_actual)) + '</dd>'
    + '<dt>' + labEsc(t('journal_trace_target')) + '</dt><dd>' + labEsc(jrTargetText(c)) + '</dd>'
    + '<dt>' + labEsc(t('journal_trace_asof')) + '</dt><dd>' + labEsc(ev.datos_al ? labDate(ev.datos_al) : LAB_NA) + '</dd></dl></div>';
}
function jrEvalHtml(e) {
  const c = e.condicion_salida || {}, ev = e.evaluacion;
  if (!ev) return '';
  const cur = jrFmtVal(c.tipo, ev.valor_actual), key = JR_DETAIL_KEYS.indexOf(ev.detalle) >= 0 ? 'journal_det_' + ev.detalle : 'journal_det_generic';
  const open = S.jr.trace === e.id;
  return '<div class="lab-jr-eval"><span>' + labEsc(t(c.tipo === 'fecha' ? 'journal_today' : 'journal_current')) + ': '
    + '<button type="button" class="lab-fig" data-jr-trace="' + labEsc(e.id) + '" aria-expanded="' + open + '" title="' + labEsc(t('journal_trace_hint')) + '">' + labEsc(cur) + '</button></span>'
    + '<span>' + labEsc(t('journal_target')) + ': <b class="lab-mono">' + labEsc(jrTargetText(c)) + '</b></span>'
    + '<span class="lab-hint">' + labEsc(t('journal_asof')) + ' <span class="lab-mono">' + labEsc(ev.datos_al ? labDate(ev.datos_al) : LAB_NA) + '</span></span></div>'
    + '<p class="lab-hint">' + labEsc(t(key)) + '</p>' + (open ? jrTraceHtml(e) : '');
}
function jrEditHtml(e) {
  const o = S.jr.ed;
  return '<form class="lab-jr-form lab-jr-inline" data-jr-form="ed" data-jr-id="' + labEsc(e.id) + '" novalidate><fieldset class="lab-jr-fs"' + (S.jr.busy ? ' disabled' : '') + '>'
    + jrTesisHtml('ed', o) + '<h5 class="lab-jr-h5">' + labEsc(t('journal_cond_title')) + '</h5>' + jrCondHtml('ed', o)
    + jrErrHtml(S.jr.rowErr.e.gen)
    + '<div class="lab-jr-actions">' + jrSubmitBtn('journal_save_changes') + jrBtn('cancel', e.id, 'journal_cancel') + '</div></fieldset></form>';
}
function jrConfirmHtml(e) {
  const k = S.jr.confirm.kind, close = k === 'close';
  return '<div class="lab-jr-confirm" role="group" aria-label="' + labEsc(t(close ? 'journal_close_q' : 'journal_reopen_q')) + '"><p>' + labEsc(t(close ? 'journal_close_q' : 'journal_reopen_q')) + '</p>'
    + (close ? '<label class="lab-jr-lbl" for="jr-nt-nota">' + labEsc(t('journal_close_note')) + '</label>'
      + '<textarea class="lab-jr-in lab-jr-ta" id="jr-nt-nota" rows="2" data-jr="nt" data-jr-f="nota">' + labEsc(S.jr.note) + '</textarea>'
      + jrCountHtml('nt', 'nota', S.jr.note, jrLim('nota')) + jrErrHtml(S.jr.rowErr.e.nota) : '')
    + jrErrHtml(S.jr.rowErr.e.gen)
    + '<div class="lab-jr-actions"><button type="button" class="lab-btn" data-jr-act="confirm" data-jr-id="' + labEsc(e.id) + '"' + (S.jr.busy ? ' disabled aria-disabled="true" title="' + labEsc(t('journal_busy')) + '"' : ' aria-disabled="false"') + '>'
    + labEsc(S.jr.busy ? t('journal_saving') : t(close ? 'journal_confirm_close' : 'journal_confirm_reopen')) + '</button>' + jrBtn('cancel', e.id, 'journal_cancel') + '</div></div>';
}
function jrItemHtml(e) {
  const closed = e.estado === 'cerrada', ev = e.evaluacion, ek = ev && JR_EVAL_KEY[ev.estado];
  const editing = S.jr.editId === e.id, conf = S.jr.confirm && S.jr.confirm.id === e.id;
  return '<li class="lab-jr-item" data-jr-item="' + labEsc(e.id) + '">'
    + '<div class="lab-jr-head">' + tickerBtn(e.ticker) + (closed ? jrPill('closed') : jrPill('open') + (ek ? jrPill(ek) : ''))
    + '<span class="lab-hint">' + labEsc(t('journal_created')) + ' ' + labEsc(e.fecha_alta ? labDate(String(e.fecha_alta).slice(0, 10)) : LAB_NA) + '</span></div>'
    + '<p class="lab-jr-tesis">' + labEsc(e.tesis) + '</p>'
    + '<p class="lab-jr-cond-text">' + labEsc(jrCondText(e.condicion_salida)) + '</p>'
    + (closed ? '' : jrEvalHtml(e))
    + (closed && e.nota_cierre ? '<p class="lab-jr-nota"><b>' + labEsc(t('journal_note_label')) + ':</b> <span class="lab-jr-tesis">' + labEsc(e.nota_cierre) + '</span></p>' : '')
    + (editing ? jrEditHtml(e) : (conf ? jrConfirmHtml(e)
      : '<div class="lab-jr-actions">' + jrBtn('edit', e.id, 'journal_edit') + (closed ? jrBtn('reopen', e.id, 'journal_reopen') : jrBtn('close', e.id, 'journal_close')) + '</div>'
        + (S.jr.rowErr.id === e.id ? jrErrHtml(S.jr.rowErr.e.gen) : '')))
    + '</li>';
}
function jrSummaryHtml(d) {
  const r = d.resumen;
  if (!r) return '';
  const items = [['journal_sum_total', r.total], ['journal_sum_open', r.abiertas], ['journal_sum_closed', r.cerradas], ['journal_sum_met', r.cumplidas], ['journal_sum_nodata', r.sin_dato]];
  const met = jrEntries().filter(e => e.estado === 'abierta' && e.evaluacion && e.evaluacion.estado === 'cumplida');
  return '<div class="lab-jr-sum" role="group">' + items.map(x => '<div class="lab-jr-stat"><span class="lab-hint">' + labEsc(t(x[0])) + '</span><b class="lab-mono">' + labEsc(x[1] == null ? LAB_NA : x[1]) + '</b></div>').join('') + '</div>'
    + (r.cumplidas > 0 ? '<div class="lab-jr-alert" role="status">' + labEsc(t('journal_alert_met', { n: r.cumplidas })) + ' ' + met.map(e => e.ticker).filter((x, i, arr) => arr.indexOf(x) === i).map(tickerBtn).join(', ') + '</div>' : '');
}
function jrListHtml(d) {
  const all = jrEntries().slice().reverse().sort((a, b) => (a.fecha_alta < b.fecha_alta ? 1 : a.fecha_alta > b.fecha_alta ? -1 : 0));
  const f = S.jr.filter, rows = all.filter(e => f === 'all' || (f === 'open' ? e.estado === 'abierta' : e.estado === 'cerrada'));
  const filt = '<div class="lab-jr-filter" role="group" aria-label="' + labEsc(t('journal_filter_label')) + '">' + JR_FILTERS.map(k =>
    '<button type="button" class="lab-btn" data-jr-filter="' + k + '" aria-pressed="' + (f === k) + '">' + labEsc(t('journal_filter_' + k)) + '</button>').join('') + '</div>';
  if (!all.length) return '<div class="lab-state" style="display:flex">' + labEsc(t('journal_empty')) + '</div>';
  return filt + (rows.length ? '<ul class="lab-jr-list">' + rows.map(jrItemHtml).join('') + '</ul>'
    : '<div class="lab-state" style="display:flex">' + labEsc(t('journal_empty_filter')) + '</div>');
}
function jrMainHtml() {
  const d = S.data.journal, fl = S.jr.flash;
  const flash = '<p class="lab-jr-flash ' + (fl && fl.kind === 'err' ? 'err' : '') + '" role="status">' + (fl ? labEsc(jrErrText(fl)) : '') + '</p>';
  if (jrCorrupt()) return flash + '<div class="lab-jr-corrupt" role="alert">' + labEsc(t('journal_corrupt')) + (d.error ? ' <span class="lab-hint">' + labEsc(t('server_detail')) + ': ' + labEsc(d.error) + '</span>' : '') + '</div>';
  return flash + (jrStatus() === 'sin_precios' ? '<p class="lab-note" role="note">' + labEsc(t('journal_sin_precios')) + '</p>' : '')
    + jrSummaryHtml(d) + jrListHtml(d);
}
function jrPaintAll() {
  if (!$('jrMain')) return;
  $('jrFormWrap').innerHTML = jrFormHtml();
  $('jrMain').innerHTML = jrMainHtml();
}
function paintDiario(body) {
  body.innerHTML = '<p class="lab-note lab-stress-disclaimer" role="note">' + labEsc(t('journal_disclaimer')) + '</p><div id="jrFormWrap"></div><div id="jrMain"></div>';
  jrPaintAll();
}

/* ---- escritura (POST/PUT) ---- */
async function jrSend(method, url, payload) {
  try {
    const r = await fetch(url, { method: method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(payload) });
    let b = null;
    try { b = await r.json(); } catch (e) { b = null; }
    return { status: r.status, body: b, ok: r.ok && !!b && b.ok !== false };
  } catch (e) { return { status: 0, body: null, ok: false }; }
}
function jrErrKey(campo) {
  const c = String(campo == null ? '' : campo);
  if (c === 'tesis' || c === 'ticker') return c;
  if (c.indexOf('condicion_salida') === 0) return 'cond';
  if (c === 'nota_cierre') return 'nota';
  return 'gen';
}
function jrApiErr(res) {
  const b = res.body || {}, raw = typeof b.error === 'string' ? b.error : '', s = res.status;
  if (s === 0) return { key: 'gen', err: { k: 'journal_err_network' } };
  if (s === 400) return { key: jrErrKey(b.campo), err: { k: 'journal_err_400', raw: raw } };
  if (s === 404) return { key: 'gen', err: { k: 'journal_err_404', raw: raw } };
  if (s === 409) return { key: 'gen', err: { k: b.status === 'journal_corrupto' ? 'journal_err_409_corrupt' : 'journal_err_409', raw: raw } };
  if (s === 413) return { key: 'gen', err: { k: 'journal_err_413' } };
  if (s === 415) return { key: 'gen', err: { k: 'journal_err_415' } };
  const ck = labErrKey(b.codigo);
  if (ck) return { key: 'gen', err: { k: ck, raw: raw } }; // codigo estable conocido: texto traducido
  return { key: 'gen', err: { k: 'journal_err_http', v: { code: s }, raw: raw } };
}
async function jrReload() {
  S.seq.journal = (S.seq.journal | 0) + 1; // contador de cargas, no un dato
  const token = S.seq.journal;
  let res = null;
  try { res = await labApi(JR_URL); } catch (e) { res = null; }
  if (token !== S.seq.journal) return;
  if (!res || (res.ok === false && res.status !== 'journal_corrupto')) { loadGroup('journal'); return; }
  S.data.journal = res;
  jrPaintAll();
}
function jrFocusFirst(sc, errs) {
  const order = [['ticker', 'jr-' + sc + '-ticker'], ['tesis', 'jr-' + sc + '-tesis'], ['cond', 'jr-' + sc + '-valor'], ['nota', 'jr-nt-nota']];
  const hit = order.find(x => errs[x[0]] && $(x[1]));
  if (hit) $(hit[1]).focus();
}
async function jrWrite(sc, rowId, method, url, payload, okKey, after) {
  if (S.jr.busy) return;
  S.jr.busy = true; S.jr.flash = null; jrPaintAll();
  const res = await jrSend(method, url, payload);
  S.jr.busy = false;
  if (res.ok) {
    S.jr.flash = { k: okKey, kind: 'ok' };
    after();
    await jrReload();
    return;
  }
  const m = jrApiErr(res);
  if (sc === 'nw') S.jr.nwErr = { [m.key]: m.err };
  else S.jr.rowErr = { id: rowId, e: { [m.key]: m.err } };
  if (res.status === 404 || res.status === 409) { S.jr.flash = Object.assign({ kind: 'err' }, m.err); await jrReload(); } else jrPaintAll();
  jrFocusFirst(sc === 'nw' ? 'nw' : 'ed', sc === 'nw' ? S.jr.nwErr : S.jr.rowErr.e);
}
function jrSubmitNew() {
  if (S.jr.busy || jrOff()) return;
  const f = S.jr.nw, errs = jrValidate(f, true);
  S.jr.nwErr = errs;
  if (Object.keys(errs).length) { jrPaintAll(); jrFocusFirst('nw', errs); return; }
  jrWrite('nw', null, 'POST', JR_URL, { ticker: f.ticker.trim().toUpperCase(), tesis: f.tesis, condicion_salida: jrCond(f) }, 'journal_saved',
    () => { S.jr.nw = jrBlank(); S.jr.nwErr = {}; });
}
function jrSubmitEdit(id) {
  if (S.jr.busy) return;
  const o = S.jr.ed, errs = jrValidate(o, false);
  S.jr.rowErr = { id: id, e: errs };
  if (Object.keys(errs).length) { jrPaintAll(); jrFocusFirst('ed', errs); return; }
  jrWrite('ed', id, 'PUT', JR_URL + '/' + encodeURIComponent(id), { tesis: o.tesis, condicion_salida: jrCond(o) }, 'journal_updated',
    () => { S.jr.editId = null; S.jr.ed = null; S.jr.rowErr = { id: null, e: {} }; });
}
function jrConfirmOk(id) {
  if (S.jr.busy || !S.jr.confirm || S.jr.confirm.id !== id) return;
  const close = S.jr.confirm.kind === 'close', note = S.jr.note;
  if (close && jrLen(note) > jrLim('nota')) { S.jr.rowErr = { id: id, e: { nota: { k: 'journal_v_nota_long', v: { max: jrLim('nota') } } } }; jrPaintAll(); jrFocusFirst('nt', S.jr.rowErr.e); return; }
  S.jr.rowErr = { id: id, e: {} };
  const payload = close ? { estado: 'cerrada', nota_cierre: note.trim() ? note : null } : { estado: 'abierta' };
  jrWrite('ed', id, 'PUT', JR_URL + '/' + encodeURIComponent(id), payload, close ? 'journal_closed_ok' : 'journal_reopened_ok',
    () => { S.jr.confirm = null; S.jr.note = ''; S.jr.rowErr = { id: null, e: {} }; });
}
function jrAction(act, id) {
  if (S.jr.busy) return;
  const e = jrEntry(id);
  S.jr.flash = null; S.jr.rowErr = { id: null, e: {} };
  if (act === 'edit' && e) {
    const c = e.condicion_salida || {};
    S.jr.confirm = null; S.jr.editId = id;
    S.jr.ed = { tesis: e.tesis || '', tipo: JR_TIPOS.indexOf(c.tipo) >= 0 ? c.tipo : 'precio', op: JR_OPS.indexOf(c.operador) >= 0 ? c.operador : '>=', valor: c.valor == null ? '' : String(c.valor) };
  } else if (act === 'close' || act === 'reopen') {
    S.jr.editId = null; S.jr.ed = null; S.jr.confirm = { id: id, kind: act }; S.jr.note = '';
  } else if (act === 'cancel') {
    S.jr.editId = null; S.jr.ed = null; S.jr.confirm = null; S.jr.note = '';
  } else if (act === 'confirm') { jrConfirmOk(id); return; }
  jrPaintAll();
}
function jrSetField(el) {
  const sc = el.dataset.jr, f = el.dataset.jrF;
  if (sc === 'nt') { S.jr.note = el.value; jrUpdateCount(el, sc, f, el.value, jrLim('nota')); return; }
  const o = sc === 'ed' ? S.jr.ed : S.jr.nw;
  if (!o) return;
  if (f === 'tipo') {
    if (o.tipo === el.value) return;
    o.tipo = el.value; o.valor = ''; o.op = o.tipo === 'fecha' ? null : (o.op || '>=');
    delete jrErrs(sc).cond;
    const box = $('jr-condv-' + sc);
    if (box) box.innerHTML = jrCondValHtml(sc, o);
    return;
  }
  o[f] = el.value;
  if (f === 'tesis') jrUpdateCount(el, sc, f, el.value, jrLim('tesis'));
}
function jrUpdateCount(el, sc, f, val, max) {
  const c = $('jr-' + sc + '-' + f + '-count');
  if (!c) return;
  c.textContent = jrCountText(jrLen(val), max);
  c.classList.toggle('over', jrLen(val) > max);
}
function bindJournalEvents() {
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-jr-act]');
    if (a && !a.disabled) { jrAction(a.dataset.jrAct, a.dataset.jrId); return; }
    const fl = e.target.closest('[data-jr-filter]');
    if (fl) { S.jr.filter = fl.dataset.jrFilter; jrPaintAll(); return; }
    const tr = e.target.closest('[data-jr-trace]');
    if (tr) { S.jr.trace = S.jr.trace === tr.dataset.jrTrace ? null : tr.dataset.jrTrace; jrPaintAll(); }
  });
  document.addEventListener('submit', e => {
    const f = e.target.closest('[data-jr-form]');
    if (!f) return;
    e.preventDefault();
    if (f.dataset.jrForm === 'nw') jrSubmitNew(); else jrSubmitEdit(f.dataset.jrId);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.id === 'labMovWin') applyMovWindow(); });
  document.addEventListener('input', e => { if (e.target.dataset && e.target.dataset.jr && e.target.dataset.jrF !== 'tipo') jrSetField(e.target); });
  document.addEventListener('change', e => { if (e.target.dataset && e.target.dataset.jr) jrSetField(e.target); });
}

/* ---------- Navegación ---------- */
function setActiveNav(id) {
  document.querySelectorAll('#labNav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + id));
}
function initNav() {
  const secs = [...document.querySelectorAll('.lab-section')];
  const fromHash = () => { const id = location.hash.slice(1); if (secs.some(s => s.id === id)) setActiveNav(id); };
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) setActiveNav(e.target.id); }), { rootMargin: '-30% 0px -60% 0px' });
    secs.forEach(s => io.observe(s));
  }
  window.addEventListener('hashchange', fromHash);
  if (location.hash) fromHash(); else setActiveNav(secs[0].id);
}

/* ---------- Eventos ---------- */
/* ---------- Actualizar: POST /api/lab/refresh (descarga precios; es sincrono y puede tardar) ---------- */
const LAB_REFRESH_URL = '/api/lab/refresh', LAB_HEALTH_URL = '/api/lab/health';
const LAB_CACHES = ['precios', 'estres', 'macro'];
S.refresh = { busy: false, out: null }; // out = { http, body } del ultimo intento
S.health = null;

function refreshWarnHtml(w, faltantes) {
  const code = w && typeof w === 'object' ? w.codigo : null, key = 'refresh_warn_' + code;
  const tk = w && w.ticker ? w.ticker : ((faltantes || []).join(', ') || LAB_NA);
  if (code && tHas(key)) return '<li>' + labEsc(t(key, { ticker: tk })) + '</li>';
  const raw = w && typeof w === 'object' ? (typeof w.detalle === 'string' ? w.detalle : '') : String(w == null ? '' : w);
  return '<li>' + labEsc(t('refresh_warn_generic')) + (raw ? ' <span class="lab-hint">' + labEsc(t('server_detail')) + ': ' + labEsc(raw) + '</span>' : '') + '</li>';
}
function refreshCacheHtml(name, r) {
  const ok = !!(r && r.ok), err = r && typeof r.error === 'string' ? r.error : '';
  return '<li><b>' + labEsc(t('cache_' + name)) + '</b>: <span class="' + (ok ? 'pos' : 'neg') + '">' + labEsc(t(ok ? 'refresh_cache_ok' : 'refresh_cache_error')) + '</span>'
    + ' <span class="lab-hint">' + labEsc(t('data_as_of')) + ' <span class="lab-mono">' + labEsc(r && r.datos_al ? labDate(r.datos_al) : LAB_NA) + '</span></span>'
    + (err ? ' <span class="lab-hint">' + labEsc(t('server_detail')) + ': ' + labEsc(err) + '</span>' : '') + '</li>';
}
/* Resultado del intento -> { sev: 'ok'|'warn'|'err', html } */
function refreshOutcome(out) {
  const b = out.body;
  if (out.http === 200 && b && b.resultados && typeof b.resultados === 'object') {
    const stKey = b.status === 'ok' || b.status === 'parcial' || b.status === 'error' ? b.status : 'error';
    const res = b.resultados, adv = [];
    LAB_CACHES.forEach(c => { const r = res[c]; if (r && Array.isArray(r.advertencias)) r.advertencias.forEach(w => adv.push(refreshWarnHtml(w, r.faltantes))); });
    return { sev: stKey === 'ok' ? 'ok' : (stKey === 'parcial' ? 'warn' : 'err'),
      html: '<div class="lab-refresh-title">' + labEsc(t('refresh_status_' + stKey)) + '</div>'
        + '<ul class="lab-refresh-list">' + LAB_CACHES.map(c => refreshCacheHtml(c, res[c])).join('') + '</ul>'
        + (adv.length ? '<div class="lab-hint">' + labEsc(t('refresh_warnings')) + '</div><ul class="lab-refresh-list">' + adv.join('') + '</ul>' : '') };
  }
  if (out.http === 0) return { sev: 'err', html: '<div class="lab-refresh-title">' + labEsc(t('refresh_err_network')) + '</div>' };
  const ck = labErrKey(b && b.codigo);
  if (ck === 'err_refresh_en_curso' || out.http === 409) return { sev: 'warn', html: '<div class="lab-refresh-title">' + labEsc(t('err_refresh_en_curso')) + '</div>' };
  const head = ck ? labErrHtml(b) : labEsc(t('refresh_err_http', { code: out.http }));
  return { sev: 'err', html: '<div class="lab-refresh-title">' + head + '</div>' };
}
function paintRefreshPanel() {
  const box = $('labRefreshPanel'), R = S.refresh;
  if (!R.busy && !R.out) { box.hidden = true; box.innerHTML = ''; box.removeAttribute('data-sev'); return; }
  box.hidden = false;
  if (R.busy) {
    box.dataset.sev = 'busy'; box.setAttribute('role', 'status'); box.setAttribute('aria-busy', 'true');
    box.innerHTML = '<span class="lab-spin"></span> <span>' + labEsc(t('refresh_wait')) + '</span>';
    return;
  }
  const o = refreshOutcome(R.out);
  box.dataset.sev = o.sev; box.setAttribute('role', o.sev === 'err' ? 'alert' : 'status'); box.setAttribute('aria-busy', 'false');
  box.innerHTML = '<div class="lab-refresh-body">' + o.html + '</div>'
    + '<button type="button" class="lab-btn" data-refresh-dismiss aria-label="' + labEsc(t('refresh_dismiss')) + '">' + labEsc(t('refresh_dismiss')) + '</button>';
}
function paintRefreshBtn() {
  const b = $('labRefresh'), busy = S.refresh.busy;
  b.disabled = busy; b.setAttribute('aria-disabled', busy ? 'true' : 'false');
  b.textContent = t(busy ? 'refreshing' : 'refresh'); b.title = t(busy ? 'refreshing_tip' : 'refresh_tip');
}
function paintCaches() {
  const el = $('labCaches'), h = S.health && S.health.caches;
  if (!h) { el.hidden = true; el.textContent = ''; return; }
  el.hidden = false;
  el.textContent = LAB_CACHES.map(c => t('cache_' + c) + ' ' + (h[c] && h[c].datos_al ? labDate(h[c].datos_al) : LAB_NA)).join(' · ');
  el.title = t('caches_tip');
}
async function loadHealth() {
  try { const h = await labApi(LAB_HEALTH_URL); S.health = h && h.ok !== false && h.caches ? h : null; } catch (e) { S.health = null; }
  paintCaches();
}
async function doRefresh() {
  if (S.refresh.busy) return; // sin doble clic
  S.refresh.busy = true; S.refresh.out = null;
  paintRefreshBtn(); paintRefreshPanel();
  let out;
  try {
    const r = await fetch(LAB_REFRESH_URL, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: '{}' });
    let b = null;
    try { b = await r.json(); } catch (e) { b = null; }
    out = { http: r.status, body: b };
  } catch (e) { out = { http: 0, body: null }; }
  S.refresh.out = out;
  try { await Promise.all([loadAll(), loadHealth()]); } finally { // cualquier resultado: vuelve a cargar todos los grupos
    S.refresh.busy = false;
    paintRefreshBtn(); paintRefreshPanel();
  }
}

function bindEvents() {
  document.addEventListener('click', e => {
    const tk = e.target.closest('[data-ticker]');
    if (tk) { window.location.href = '/?ticker=' + encodeURIComponent(tk.dataset.ticker); return; }
    const tile = e.target.closest('.lab-tile');
    if (tile) { const i = +tile.dataset.tile; if (S.trace === i) { S.trace = null; $('labTrace').hidden = true; tile.setAttribute('aria-expanded', 'false'); } else showTrace(i); return; }
    const mode = e.target.closest('[data-mode]');
    if (mode) { S.barMode = mode.dataset.mode; paintCard('periodos'); return; }
    const retry = e.target.closest('[data-retry]');
    if (retry) { loadGroup(LAB_CARD_GROUP[retry.closest('.lab-card').dataset.card]); return; }
    const pre = e.target.closest('[data-attr-preset]');
    if (pre && !pre.disabled) {
      S.attrPreset = pre.dataset.attrPreset; S.attrFrom = attrPresetFrom(S.attrPreset); S.attrTo = null; S.attrLimit = LAB_ATTR_STEP;
      fillAttrRange(); loadGroup('attr'); return;
    }
    const sp = e.target.closest('[data-stress-pick]');
    if (sp) { S.stressSel = sp.dataset.stressPick; S.stressTrace = null; paintCard('estres'); return; }
    const str = e.target.closest('[data-stress-trace]');
    if (str) { toggleStressTrace(str.dataset.stressTrace); return; }
    const ext = e.target.closest('[data-expo-trace]');
    if (ext) { toggleLabTrace('expoTrace', 'labExpoTrace', 'data-expo-trace', ext.dataset.expoTrace, expoTraceDef); return; }
    const mct = e.target.closest('[data-macro-trace]');
    if (mct) { toggleLabTrace('macroTrace', 'labMacroTrace', 'data-macro-trace', mct.dataset.macroTrace, macroTraceDef); return; }
    if (e.target.id === 'labMovApply') { applyMovWindow(); return; }
    const atr = e.target.closest('[data-attr-trace]');
    if (atr) { toggleAttrTrace(atr.dataset.attrTrace); return; }
    if (e.target.id === 'labAttrMore') { S.attrLimit += LAB_ATTR_STEP; updateAttrOps(); return; }
    if (e.target.id === 'labLedgerAll') { S.ledgerAll = !S.ledgerAll; updateOps(); return; }
    if (e.target.id === 'labRecoAll') { S.recoAll = !S.recoAll; paintCard('conciliacion'); }
  });
  document.addEventListener('input', e => { if (e.target.id === 'labMovWin') updateMovBtn(); if (e.target.id === 'labLedgerFilter') { S.ledgerFilter = e.target.value; updateOps(); } });
  document.addEventListener('change', e => {
    const id = e.target.id;
    if (id === 'labAttrTk' || id === 'labAttrSt') { if (id === 'labAttrTk') S.attrTk = e.target.value; else S.attrSt = e.target.value; S.attrLimit = LAB_ATTR_STEP; updateAttrOps(); }
    if (id === 'labAttrFrom' || id === 'labAttrTo') {
      S.attrFrom = $('labAttrFrom').value || null; S.attrTo = $('labAttrTo').value || null;
      S.attrPreset = S.attrFrom || S.attrTo ? 'custom' : 'all'; S.attrLimit = LAB_ATTR_STEP;
      loadGroup('attr');
    }
  });
  document.addEventListener('change', e => { if (e.target.id === 'labShowPos') { S.showPositions = e.target.checked; paintCard('dispersion'); } });
  $('labBench').addEventListener('change', e => {
    S.bench = e.target.value;
    try { localStorage.setItem(LAB_KEY_BENCH, S.bench); } catch (err) { /* solo en memoria */ }
    loadGroup('perf');
  });
  $('labPeriod').addEventListener('change', e => {
    S.period = e.target.value;
    try { localStorage.setItem(LAB_KEY_PERIOD, S.period); } catch (err) { /* solo en memoria */ }
    ['evolucion', 'periodos', 'drawdown'].forEach(c => { if (S.status[c].st === 'ok') paintCard(c); });
  });
  document.querySelectorAll('.lab-lang button').forEach(b => b.addEventListener('click', () => { setLabLang(b.dataset.lang); repaintAll(); }));
  window.addEventListener('storage', e => { if (e.key === 'lang') { setLabLang(e.newValue); repaintAll(); } });
  $('labRefresh').addEventListener('click', doRefresh);
  $('labRefreshPanel').addEventListener('click', e => { if (e.target.closest('[data-refresh-dismiss]')) { S.refresh.out = null; paintRefreshPanel(); $('labRefresh').focus(); } });
}

document.addEventListener('DOMContentLoaded', async () => {
  buildCards();
  applyI18n();
  initNav();
  bindEvents();
  bindJournalEvents();
  await Promise.all([loadAll(), loadHealth()]);
  const target = location.hash && document.getElementById(location.hash.slice(1));
  if (target) target.scrollIntoView();
});
