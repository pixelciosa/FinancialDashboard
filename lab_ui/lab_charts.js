/* Portfolio Lab: formatos, colores por entidad y gráficos (lightweight-charts + SVG propio). */
'use strict';

const labCss = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const labEsc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const LAB_NA = '—';

/* El color sigue a la entidad: cartera = acento, índice elegido = naranja, el resto = gris. */
function labColor(id, selId) {
  if (id === LAB_PORT_ID) return labCss('--accent');
  if (id === selId) return labCss('--orange');
  return labCss('--muted');
}
const LAB_PORT_ID = 'PORT'; // id de la cartera en la API (/correlation y /risk-return)

function labLocale() { return labLang === 'es' ? 'es-AR' : 'en-US'; }
function labNum(x, d = 1) {
  if (x == null || !isFinite(x)) return LAB_NA;
  return x.toLocaleString(labLocale(), { minimumFractionDigits: d, maximumFractionDigits: d });
}
function labPct(x, d = 1, signed = true) {
  if (x == null || !isFinite(x)) return LAB_NA;
  return (signed && x > 0 ? '+' : '') + labNum(x, d) + '%';
}
function labUsd(x) {
  if (x == null || !isFinite(x)) return LAB_NA;
  return (x < 0 ? '-' : '') + 'US$ ' + Math.abs(x).toLocaleString(labLocale(), { maximumFractionDigits: 0 });
}
function labDate(iso) {
  if (!iso) return LAB_NA;
  const d = new Date(iso + 'T00:00:00Z');
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(labLocale(), { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}
function labMonth(periodo) {
  if (!/^\d{4}-\d{2}$/.test(periodo)) return periodo;
  const d = new Date(periodo + '-01T00:00:00Z');
  return d.toLocaleDateString(labLocale(), { month: 'short', year: '2-digit', timeZone: 'UTC' });
}
function labSigned(x, d = 1) { return '<span class="lab-num ' + (x > 0 ? 'pos' : x < 0 ? 'neg' : '') + '">' + labPct(x, d) + '</span>'; }

/* ---- Series de tiempo: lightweight-charts 4.2.0 ---- */
function labLineChart(host, seriesList, opts = {}) {
  host.innerHTML = '';
  if (typeof LightweightCharts === 'undefined') throw new Error('lightweight-charts');
  const tip = document.createElement('div');
  tip.className = 'lab-chart-tip';
  host.appendChild(tip);
  const chart = LightweightCharts.createChart(host, {
    autoSize: true,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: labCss('--muted'), fontFamily: labCss('--mono') },
    grid: { vertLines: { visible: false }, horzLines: { color: labCss('--border') } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false },
    crosshair: { mode: 0 },
    localization: { locale: labLocale(), priceFormatter: opts.fmt || (v => labNum(v, 1)) },
  });
  const handles = seriesList.map(s => {
    const common = { color: s.color, lineWidth: s.width || 2, priceLineVisible: false, lastValueVisible: false };
    const api = s.area
      ? chart.addAreaSeries(Object.assign(common, { lineColor: s.color, topColor: s.color + '40', bottomColor: s.color + '05' }))
      : chart.addLineSeries(common);
    api.setData(s.data);
    return { s, api };
  });
  const fmt = opts.fmt || (v => labNum(v, 1));
  const paint = (time, vals) => {
    tip.innerHTML = '<div>' + labEsc(time ? labDate(time) : '') + '</div>' + handles.map(h => {
      const v = vals.get(h.s.id);
      return '<div><i style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:' + labEsc(h.s.color) + '"></i>'
        + labEsc(h.s.label) + ': <b>' + fmt(v) + '</b></div>';
    }).join('');
  };
  const last = new Map(handles.map(h => [h.s.id, h.s.data.length ? h.s.data[h.s.data.length - 1].value : null]));
  paint(seriesList[0].data.length ? seriesList[0].data[seriesList[0].data.length - 1].time : null, last);
  chart.subscribeCrosshairMove(p => {
    if (!p.time || !p.seriesData) { paint(seriesList[0].data.length ? seriesList[0].data[seriesList[0].data.length - 1].time : null, last); return; }
    const vals = new Map(handles.map(h => { const d = p.seriesData.get(h.api); return [h.s.id, d ? d.value : null]; }));
    paint(p.time, vals);
  });
  chart.timeScale().fitContent();
  return chart;
}

/* ---- Mapa de calor (SVG) ---- */
function labHexRgb(h) { h = h.replace('#', ''); return [0, 2, 4].map(i => parseInt(h.substr(i, 2), 16)); }
function labHeatColor(v) {
  const n = labHexRgb(labCss('--surface3')), e = labHexRgb(v >= 0 ? labCss('--accent') : labCss('--red'));
  const k = Math.min(1, Math.abs(v));
  return 'rgb(' + n.map((c, i) => Math.round(c + (e[i] - c) * k)).join(',') + ')';
}
function labHeatmapSvg(names, matrix, sel, nameFn, shortFn) {
  const cell = 36, left = 84, top = 78, n = names.length, W = left + n * cell + 4, H = top + n * cell + 4;
  let s = '<svg role="img" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">';
  names.forEach((a, i) => {
    const col = labColor(a, sel), strong = a === LAB_PORT_ID || a === sel ? ' lbl-strong' : '';
    s += '<text class="' + strong + '" style="fill:' + col + '" x="' + (left + i * cell + cell / 2) + '" y="' + (top - 6) + '" text-anchor="start" transform="rotate(-50 ' + (left + i * cell + cell / 2) + ' ' + (top - 6) + ')">' + labEsc(shortFn(a)) + '</text>';
    s += '<text class="' + strong + '" style="fill:' + col + '" x="' + (left - 6) + '" y="' + (top + i * cell + cell / 2 + 4) + '" text-anchor="end">' + labEsc(shortFn(a)) + '</text>';
  });
  names.forEach((a, i) => names.forEach((b, j) => {
    const v = matrix[i][j];
    if (v == null) return;
    const x = left + j * cell, y = top + i * cell;
    s += '<g><title>' + labEsc(nameFn(a) + ' / ' + nameFn(b) + ': ' + labNum(v, 2)) + '</title>'
      + '<rect x="' + x + '" y="' + y + '" width="' + (cell - 1) + '" height="' + (cell - 1) + '" rx="3" fill="' + labHeatColor(v) + '"/>'
      + '<text x="' + (x + cell / 2) + '" y="' + (y + cell / 2 + 3) + '" text-anchor="middle" style="font-size:10px;fill:' + (Math.abs(v) > 0.55 ? labCss('--text') : labCss('--text')) + '">' + labNum(v, 2) + '</text></g>';
  }));
  return s + '</svg>';
}
function labHeatScaleCss() {
  return 'linear-gradient(90deg,' + labHeatColor(-1) + ',' + labHeatColor(0) + ',' + labHeatColor(1) + ')';
}

/* ---- Escala "agradable" para ejes ---- */
function labTicks(lo, hi, count = 6) {
  const span = hi - lo || 1, raw = span / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v / step) * step);
  return out;
}

/* ---- Dispersión riesgo/rendimiento (SVG) ----
   points: [{id, x, y, kind: 'cartera'|'indice'|'posicion', label, title, ticker?}] */
function labScatterSvg(points, sel, axes) {
  const W = 920, H = 460, L = 56, R = 70, T = 16, B = 46;
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const padX = (Math.max(...xs) - Math.min(...xs)) * 0.06 || 1, padY = (Math.max(...ys) - Math.min(...ys)) * 0.08 || 1;
  const x0 = Math.min(0, Math.min(...xs) - padX), x1 = Math.max(...xs) + padX;
  const y0 = Math.min(...ys) - padY, y1 = Math.max(...ys) + padY;
  const X = v => L + (v - x0) / (x1 - x0) * (W - L - R), Y = v => H - B - (v - y0) / (y1 - y0) * (H - T - B);
  let s = '<svg role="img" viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:auto;min-width:560px">';
  labTicks(x0, x1, 8).forEach(v => {
    s += '<line x1="' + X(v) + '" x2="' + X(v) + '" y1="' + T + '" y2="' + (H - B) + '" stroke="' + labCss('--border') + '"/>'
      + '<text x="' + X(v) + '" y="' + (H - B + 16) + '" text-anchor="middle">' + labNum(v, 0) + '%</text>';
  });
  labTicks(y0, y1, 7).forEach(v => {
    s += '<line y1="' + Y(v) + '" y2="' + Y(v) + '" x1="' + L + '" x2="' + (W - R) + '" stroke="' + labCss(v === 0 ? '--muted' : '--border') + '"/>'
      + '<text y="' + (Y(v) + 4) + '" x="' + (L - 8) + '" text-anchor="end">' + labNum(v, 0) + '%</text>';
  });
  s += '<text x="' + ((L + W - R) / 2) + '" y="' + (H - 8) + '" text-anchor="middle">' + labEsc(axes.x) + '</text>';
  s += '<text transform="rotate(-90 14 ' + ((T + H - B) / 2) + ')" x="14" y="' + ((T + H - B) / 2) + '" text-anchor="middle">' + labEsc(axes.y) + '</text>';
  const order = { cartera: 3, indice: 1, posicion: 0 };
  const rad = p => p.kind === 'cartera' ? 8 : (p.id === sel ? 6 : (p.kind === 'posicion' ? 4 : 5));
  const placed = points.map(p => ({ x0: X(p.x) - rad(p), y0: Y(p.y) - rad(p), x1: X(p.x) + rad(p), y1: Y(p.y) + rad(p) }));
  const labels = [];
  const prio = p => (p.kind === 'cartera' ? 3 : p.id === sel ? 2 : p.kind === 'indice' ? 1 : 0);
  points.slice().sort((a, b) => prio(b) - prio(a)).forEach(p => {
    const w = p.label.length * 6.6 + 2, h = 13, r = rad(p), g = r + 4, px = X(p.x), py = Y(p.y);
    const cand = [[px + g, py - h / 2], [px - g - w, py - h / 2], [px - w / 2, py - g - h], [px - w / 2, py + g], [px + g, py - g - h / 2], [px + g, py + g - h / 2], [px - g - w, py - g - h / 2], [px - g - w, py + g - h / 2]];
    for (const [bx, by] of cand) {
      const box = { x0: bx - 1, y0: by - 1, x1: bx + w + 1, y1: by + h + 1 };
      if (box.x0 < L || box.x1 > W - 2 || box.y0 < T - 6 || box.y1 > H - B) continue;
      if (placed.some(q => !(box.x1 < q.x0 || box.x0 > q.x1 || box.y1 < q.y0 || box.y0 > q.y1))) continue;
      placed.push(box); labels.push({ p, x: bx, y: by + h - 2 }); break;
    }
  });
  points.slice().sort((a, b) => order[a.kind] - order[b.kind] || (a.id === sel ? 1 : 0) - (b.id === sel ? 1 : 0)).forEach(p => {
    const col = labColor(p.id, sel), clickable = p.kind === 'posicion' && p.ticker;
    s += '<g' + (clickable ? ' class="lab-clickable" data-ticker="' + labEsc(p.ticker) + '"' : '') + '><title>' + labEsc(p.title) + '</title>'
      + '<circle cx="' + X(p.x) + '" cy="' + Y(p.y) + '" r="' + rad(p) + '" fill="' + (p.kind === 'posicion' ? 'none' : col) + '" stroke="' + (p.kind === 'posicion' ? col : labCss('--surface')) + '" stroke-width="' + (p.kind === 'posicion' ? 1.5 : 2) + '"/></g>';
  });
  labels.forEach(l => {
    const strong = l.p.kind === 'cartera' || l.p.id === sel;
    const clickable = l.p.kind === 'posicion' && l.p.ticker;
    s += '<text' + (clickable ? ' class="lab-clickable" data-ticker="' + labEsc(l.p.ticker) + '"' : (strong ? ' class="lbl-strong"' : '')) + ' x="' + l.x + '" y="' + l.y + '" style="fill:' + (strong ? labColor(l.p.id, sel) : labCss('--muted')) + '">' + labEsc(l.p.label) + '</text>';
  });
  return s + '</svg>';
}

/* ---- Barras agrupadas (SVG): cartera vs índice elegido ----
   cats: [{label, port, bench}] (null = sin dato) */
function labBarsSvg(cats, sel, names) {
  const W = 920, H = 340, L = 52, R = 10, T = 14, B = 34;
  const vals = cats.flatMap(c => [c.port, c.bench]).filter(v => v != null);
  const hi = Math.max(0, ...vals), lo = Math.min(0, ...vals), pad = (hi - lo) * 0.08 || 1;
  const y0 = lo - (lo < 0 ? pad : 0), y1 = hi + pad;
  const Y = v => H - B - (v - y0) / (y1 - y0) * (H - T - B);
  const slot = (W - L - R) / cats.length, bw = Math.min(26, slot * 0.38);
  let s = '<svg role="img" viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:auto;min-width:' + Math.min(900, cats.length * 38 + 80) + 'px">';
  labTicks(y0, y1, 6).forEach(v => {
    s += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" stroke="' + labCss(v === 0 ? '--muted' : '--border') + '"/>'
      + '<text x="' + (L - 8) + '" y="' + (Y(v) + 4) + '" text-anchor="end">' + labNum(v, 0) + '%</text>';
  });
  const every = Math.ceil(cats.length / 14);
  cats.forEach((c, i) => {
    const cx = L + slot * i + slot / 2;
    [['PORT', c.port, -bw], [sel, c.bench, 0]].forEach(([id, v, dx]) => {
      if (v == null) return;
      const y = Math.min(Y(v), Y(0)), h = Math.max(1, Math.abs(Y(v) - Y(0)));
      s += '<g><title>' + labEsc(names(id) + ' ' + c.label + ': ' + labPct(v, 2)) + '</title><rect x="' + (cx + dx) + '" y="' + y + '" width="' + bw + '" height="' + h + '" rx="2" fill="' + labColor(id, sel) + '"/></g>';
    });
    if (i % every === 0) s += '<text x="' + cx + '" y="' + (H - 12) + '" text-anchor="middle">' + labEsc(c.label) + '</text>';
  });
  return s + '</svg>';
}

/* Barras horizontales por posición. El color es semántico del signo (verde gana, rojo pierde), no de entidad.
   rows: [{ ticker, value, text, tip }] ya ordenadas; el clic lo resuelve el manejador global por data-ticker. */
function labAttrBarsSvg(rows, aria) {
  const pos = labCss('--green'), neg = labCss('--red'), axis = labCss('--border');
  const rowH = 22, padT = 6, left = 96, gap = 90, right = 90, plotW = 480, W = left + gap + plotW + right, H = padT * 2 + rows.length * rowH;
  const vals = rows.map(r => r.value);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), span = (hi - lo) || 1;
  const x = v => left + gap + (v - lo) / span * plotW, x0 = x(0);
  const bars = rows.map((r, i) => {
    const y = padT + i * rowH, bx = Math.min(x0, x(r.value)), bw = Math.max(1, Math.abs(x(r.value) - x0));
    const isNeg = r.value < 0, color = isNeg ? neg : pos;
    const lx = isNeg ? bx - 6 : bx + bw + 6, anchor = isNeg ? 'end' : 'start';
    return '<g><title>' + labEsc(r.tip) + '</title>'
      + '<rect x="' + bx.toFixed(1) + '" y="' + (y + 3) + '" width="' + bw.toFixed(1) + '" height="' + (rowH - 6) + '" rx="2" fill="' + labEsc(color) + '"/>'
      + '<text x="' + lx.toFixed(1) + '" y="' + (y + rowH / 2 + 4) + '" text-anchor="' + anchor + '">' + labEsc(r.text) + '</text>'
      + '<text class="lbl-strong lab-clickable" role="link" data-ticker="' + labEsc(r.ticker) + '" x="' + (left - 10) + '" y="' + (y + rowH / 2 + 4) + '" text-anchor="end">' + labEsc(r.ticker) + '</text></g>';
  }).join('');
  return '<svg role="img" aria-label="' + labEsc(aria) + '" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">'
    + '<line x1="' + x0.toFixed(1) + '" x2="' + x0.toFixed(1) + '" y1="0" y2="' + H + '" stroke="' + labEsc(axis) + '"/>' + bars + '</svg>';
}

/* ---- Exposición: banda de referencia por bloque (SVG propio) ----
   Escala común a todas las filas (hi) para que los anchos sean comparables.
   rows: [{ min, max, ref, weight, state, aria }]. Estado dentro = verde; fuera de la banda = ámbar (semántico, sin juicio). */
function labBandSvg(r, hi) {
  const W = 260, H = 24, pad = 6, span = hi || 1, x = v => pad + Math.max(0, Math.min(v, hi)) / span * (W - 2 * pad);
  const track = labCss('--border'), bandC = labCss('--accent'), refC = labCss('--text');
  const stC = r.state === 'dentro' ? labCss('--green') : labCss('--amber');
  const band = r.min != null && r.max != null
    ? '<rect x="' + x(r.min).toFixed(1) + '" y="4" width="' + Math.max(1, x(r.max) - x(r.min)).toFixed(1) + '" height="' + (H - 8) + '" rx="2" fill="' + labEsc(bandC) + '" fill-opacity="0.28"/>' : '';
  const ref = r.ref != null ? '<line x1="' + x(r.ref).toFixed(1) + '" x2="' + x(r.ref).toFixed(1) + '" y1="2" y2="' + (H - 2) + '" stroke="' + labEsc(refC) + '" stroke-width="2"/>' : '';
  const w = r.weight != null ? '<circle cx="' + x(r.weight).toFixed(1) + '" cy="' + (H / 2) + '" r="5" fill="' + labEsc(stC) + '" stroke="' + labEsc(labCss('--bg')) + '" stroke-width="1.5"/>' : '';
  return '<svg role="img" aria-label="' + labEsc(r.aria) + '" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">'
    + '<title>' + labEsc(r.aria) + '</title><rect x="' + pad + '" y="' + (H / 2 - 1) + '" width="' + (W - 2 * pad) + '" height="2" fill="' + labEsc(track) + '"/>' + band + ref + w + '</svg>';
}

/* Barras paralelas por bloque: peso % y aporte al riesgo % (null = sin barra y guion, nunca 0).
   rows: [{ name, w, r, wText, rText }] */
function labPairBarsSvg(rows, aria) {
  const cw = labCss('--accent'), cr = labCss('--orange');
  const rowH = 36, padT = 6, left = 150, plotW = 360, right = 80, W = left + plotW + right, H = padT * 2 + rows.length * rowH;
  const hi = Math.max(1, ...rows.map(q => Math.max(q.w || 0, q.r || 0)));
  const bar = (v, y, color, text) => (v == null ? '' : '<rect x="' + left + '" y="' + y + '" width="' + Math.max(1, v / hi * plotW).toFixed(1) + '" height="11" rx="2" fill="' + labEsc(color) + '"/>')
    + '<text x="' + (left + (v == null ? 6 : Math.max(1, v / hi * plotW) + 6)).toFixed(1) + '" y="' + (y + 10) + '">' + labEsc(text) + '</text>';
  return '<svg role="img" aria-label="' + labEsc(aria) + '" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">'
    + rows.map((q, i) => { const y = padT + i * rowH;
      return '<g><text class="lbl-strong" x="' + (left - 10) + '" y="' + (y + 20) + '" text-anchor="end">' + labEsc(q.name) + '</text>'
        + bar(q.w, y + 4, cw, q.wText) + bar(q.r, y + 19, cr, q.rText) + '</g>'; }).join('') + '</svg>';
}

/* Mini-gráfico macro: serie, promedio auxiliar (punteado) y la luz diaria como bandas de fondo.
   ind.serie = { fechas, valores, luces, auxiliares }. Las luces tienen texto en la tarjeta; aquí son solo fondo. */
function labMacroSvg(ind, aria, avgKey) {
  const s = ind.serie || {}, v = s.valores || [], lu = s.luces || [], n = v.length;
  const avg = avgKey && s.auxiliares && Array.isArray(s.auxiliares[avgKey]) ? s.auxiliares[avgKey] : null;
  const W = 440, H = 96, padL = 4, padR = 56, padT = 6, padB = 6;
  const cols = { verde: labCss('--green'), ambar: labCss('--amber'), rojo: labCss('--red'), sin_datos: labCss('--muted') };
  const all = v.concat(avg || []).filter(q => q != null && isFinite(q));
  if (!n || !all.length) return '';
  const lo = Math.min(...all), hi = Math.max(...all), sp = (hi - lo) || 1;
  const x = i => padL + (n > 1 ? i / (n - 1) : 0) * (W - padL - padR), y = q => padT + (1 - (q - lo) / sp) * (H - padT - padB);
  let bands = '', i = 0;
  while (i < n) {
    let j = i; while (j + 1 < n && lu[j + 1] === lu[i]) j++;
    const x0 = i === 0 ? padL : (x(i) + x(i - 1)) / 2, x1 = j === n - 1 ? W - padR : (x(j) + x(j + 1)) / 2;
    bands += '<rect x="' + x0.toFixed(1) + '" y="0" width="' + Math.max(0.5, x1 - x0).toFixed(1) + '" height="' + H + '" fill="' + labEsc(cols[lu[i]] || cols.sin_datos) + '" fill-opacity="0.2"/>';
    i = j + 1;
  }
  const line = (arr, attrs) => { const segs = []; let cur = null; arr.forEach((q, k) => { if (q == null || !isFinite(q)) { cur = null; return; } if (!cur) { cur = []; segs.push(cur); } cur.push(x(k).toFixed(1) + ',' + y(q).toFixed(1)); });
    return segs.filter(g => g.length > 1).map(g => '<polyline points="' + g.join(' ') + '" fill="none" ' + attrs + '/>').join(''); };
  return '<svg role="img" aria-label="' + labEsc(aria) + '" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '"><title>' + labEsc(aria) + '</title>' + bands
    + (avg ? line(avg, 'stroke="' + labEsc(labCss('--muted')) + '" stroke-width="1.2" stroke-dasharray="4 3"') : '')
    + line(v, 'stroke="' + labEsc(labCss('--text')) + '" stroke-width="1.6"')
    + '<text x="' + (W - padR + 4) + '" y="' + (padT + 8) + '">' + labEsc(labNum(hi, 1)) + '</text>'
    + '<text x="' + (W - padR + 4) + '" y="' + (H - padB) + '">' + labEsc(labNum(lo, 1)) + '</text></svg>';
}
