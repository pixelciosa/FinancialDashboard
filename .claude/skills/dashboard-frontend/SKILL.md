---
name: dashboard-frontend
description: Mapa de index.html (anclas para grep), convenciones de UI, i18n y estado global del dashboard. Usar antes de leer o editar index.html para no cargar el archivo completo.
---

# index.html: mapa y convenciones

El archivo tiene ~5.700 líneas. **Ubicá con grep y leé rangos cortos**; los números de línea
cambian, las anclas no.

## Anclas (`grep -n '<ancla>' index.html`)
| Sección | Ancla |
|---|---|
| Estilos de la tab financiera | `FINANCIALS STYLES (fin- prefix` |
| Header, menú Actualizar | `id="refreshDropdownWrap"` |
| Botones de tabs | `data-tab="financial"` |
| Panel de la tab financiera | `id="tab-financial"` |
| Traducciones EN / ES | `const TRANSLATIONS = {` · `  es: {` · helper `function t(key)` |
| Estado global | `const state = ` |
| Cambio de ticker / tab | `function selectTicker(` · `tab === 'financial'` |
| Refresh individual financiero | `window.refreshFinancialSingle` |
| Píldora de estado | `function renderStatusPill(` (config `'fin-status'`) |
| Caché de la tab (solo en memoria, sin datos fijos) | `const FD = {};` · `FIN_INFLIGHT` · `FIN_NA` |
| Píldora de frescura y botón | `function renderFinFreshness(` |
| Menú: contador de trimestres nuevos | `async function updateFinancialMenu(` · `id="btnRefreshFinancialAll"` |
| Refresco masivo | `window.refreshFinancialData` |
| Formatos de la tab | `function finDate(` · `function finMoney(` · `function finEsc(` |
| Render de la tab | `function renderFinancialDash(` · `async function renderFinancialTab(` |
| Gráficos canvas propios | `function drawBars(` · `function drawEtfReturns(` |
| Reporte de Riesgo (R3) | `RISK REPORT (R3)` · `function renderRiskOverview(` · `function renderRiskDeep(` · `async function renderRiskTicker(` |
| Caché de riesgo | `const RISK = {}` · `RISK_SUMMARY` · `RISK_NARR` · `async function fetchRisk(` |
| Píldora de estado compartida | `function renderStatusPill(` (vive en el bloque de riesgo; la usan todas las tabs) |
| Portfolio | `PORTFOLIO TAB` |
| Portfolio Agent (chat) | `PORTFOLIO AGENT (chat` · `function renderChatMarkdown(` · `function renderChatTable(` · `async function sendChatMessage(` |
| Modo Explorar | `MODO EXPLORAR` · `function renderExploreBlock(` · `function addToWatching(` · `function toggleExplorePrefs(` |
| Registro de valuaciones | `REGISTRO DE VALUACIONES` · `function renderValuations(` · `function refreshValuations(` · `id="valModalBackdrop"` |

## Convenciones
- Textos: `t('clave')`, con la clave en `TRANSLATIONS.en` y `.es`. `t()` devuelve la clave cruda
  si falta, así que `t(k) || 'texto'` **no** sirve como fallback.
- Async: después de cada `await`, `if (ticker !== state.activeTicker) return;`.
- Feedback: `showToast(msg, isLoading)`; estado por tab con `renderStatusPill(id, tipo, fecha, {generating})`.
- Refresh por ticker: `state.singleRefreshes[sym]` evita dobles clics.
- Clases de la tab financiera con prefijo `fin-`; colores con las variables CSS existentes (`--accent`, `--muted`, `--danger`).
- Gráficos de la tab: canvas propio (no Chart.js); velas: Lightweight Charts 4.2.0.
- Formato: `fmtVal(v, unit)` para `$B`/`$M`/`$T`; `finMoney(v, d)` agrega la moneda si no es USD;
  `null` se muestra con `FIN_NA` (guion corto), nunca como `0`. Fechas ISO con `finDate` (local, no UTC).
- Textos del servidor siempre con `finEsc()` antes de insertarlos en HTML.
- Pedidos al servidor por ticker deduplicados con `FIN_INFLIGHT`.

## Contrato de `GET /api/financials/<t>` (Fase 1)
Compatibles con el render actual: `name`, `type`, `etf`, `kpis` (pares [etiqueta, texto], faltante = 'N/A'),
`segs`, `years`, `rev`, `fcf`, `gm`, `om`, `nm`, `annualReturns`, `unit`/`revUnit`, `bankNote`.
Nuevos:
- `kind` ('stock' | 'etf' | 'crypto'), `supported` (false en cripto: mostrar estado vacío)
- `years` termina en `'TTM'` cuando `has_ttm`; en ETFs el año en curso es `'YTD'`
- `quarters`: lista vieja → nueva de `{period_end, label, fiscal_label, rev, ni, fcf, gm, om, nm,
  eps (GAAP), eps_adj, eps_est, surprise_pct, beat ('BEAT'|'MISS'|'IN LINE'|null), rev_yoy}`
- `currency` de los estados (puede ser 'ARS'): mostrarla si no es USD
- `latest_period_end`, `latest_period_label`, `next_earnings_date`, `fiscal_year_end_month`
- ETF: `sectors` [{l, v%}], `segs_source` ('holdings' o null), `expense_ratio_pct`
- `fetched_at`, `cached`, `stale_source` (la fuente vino vacía y se sirvió la caché)
- `freshness`: `{state, latest_period_label, next_earnings_date, next_check_date, newest_available_label, probed_at}`

## Endpoints de frescura (Fase 2)
- `GET /api/financials/status?tickers=A,B` (sin red, para sidebar y menú): `{T: freshness}`
- `POST /api/financials/<t>/refresh`: 200 `{updated, freshness, data}` o **409** `{updated:false, state}`
- `POST /api/refresh/financials`: `{updated: [...], failed: [...], checked: n}` (solo los `available`)
Botón: habilitado solo con `state === 'available'` (o `unknown`). Tooltip con `latest_period_label`
y `next_check_date`; badge con `newest_available_label`. Ante un 409, refrescar el estado, no reintentar.
Segmentos de ingresos: no hay fuente gratuita. Decisión A: no mostrar donut para acciones.

## Chat y registro
- El texto del modelo pasa por `renderChatMarkdown` (escapa con `finEsc` y solo agrega negrita, código, listas).
  Nunca asignar `data.reply` a `innerHTML`. Las tablas llegan como `{columns:[{key,label,unit}], rows}` y se dibujan
  con `renderChatTable` (`chatFmtCell` por unidad: pct, x, price, money, bigmoney, int, num, date, code).
- El registro usa `getAllMeta()` para la lista y la categoría. Al terminar una actualización se vacía `RISK`, se recarga
  `RISK_SUMMARY` y el sidebar: comparten la caché con el tab de Riesgo.

## Verificación rápida
`python -m pytest -q tests/test_frontend.py tests/test_frontend_contracts.py`
(valida sintaxis JS con `node --check`, i18n EN/ES, rutas `/api/*` existentes y tabs).
Con navegador real (opcional): `python -m pytest -m e2e` (requiere playwright + chromium; deja capturas en `tests/e2e/shots/`).
