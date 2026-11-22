---
name: test-guardrails
description: Cómo escribir y mantener los tests del dashboard (offline, fakes, xfail estricto, anti-alucinación). Usar al agregar funcionalidad, arreglar un bug o cuando un test falla.
---

# Tests del dashboard

## Comandos
- `python -m pytest`: offline, por defecto. Debe tardar segundos.
- `python -m pytest -m live`: red real, a mano. Nunca en el ciclo de desarrollo.
- `python -m pytest -rxX`: lista bugs conocidos (`xfail`) y arreglos sin desmarcar (`XPASS`).
- `python -m pytest -m e2e`: tab financiera en Chromium headless con yfinance simulado. Correrlo solo
  (modifica estado global del servidor). Requiere `pip install playwright && playwright install chromium`.

## Qué garantiza `tests/conftest.py`
- Red bloqueada en tests no `live` (falla con `NetworkBlocked`, no se cuelga).
- Cachés en carpetas temporales (`financials`, `risk`, `narrative`), preferencias de Explorar temporales,
  `NVIDIA_API_KEY` vacía y cachés en memoria limpias.
- Fixtures: `client`, `server_module`, `fake_yf` (registro `{símbolo: FakeTicker}`), `index_html`.
- Si un endpoint usa un ejecutor en segundo plano, reemplazalo por uno sincrónico (ver `SyncExecutor` en
  `test_valuation.py`) para que no corra después del test.
- Para Gemma en el chat, reemplazar `server._chat_call`; para el screener, `explore.yf.screen` (ver `test_explore.py`).

## Dónde va cada test
| Riesgo | Archivo |
|---|---|
| Rompí algo que andaba | `test_server.py`, `test_frontend.py` |
| Número mal calculado | `test_data_integrity.py` (recalcular desde la fuente, no snapshot) |
| La IA impone un dato | `test_ai_guardrails.py` |
| Se consulta el activo equivocado | `test_asset_identity.py` |
| Portfolio Lab | `tests/lab/` (ver skill `portfolio-lab`) |
| Contrato HTML ↔ API, i18n, JS | `test_frontend_contracts.py` |
| Nueva tab financiera | `test_financials_spec.py` |

## Reglas
1. **Esperado calculado desde los datos de entrada**, no copiado de la salida del código.
2. Datos de prueba con decimales (los redondos ocultan bugs de redondeo).
3. Para la IA, simulá respuestas malas (fuera de rango, números inventados, JSON roto) y verificá
   que el reporte final conserve los datos de la fuente.
4. Bug conocido que no se arregla ahora: `@pytest.mark.xfail(reason='...')`. Con `xfail_strict`
   activo, cuando se arregla el test da XPASS y falla: **quitá la marca** en el mismo cambio.
5. Prohibido para "poner en verde": borrar aserciones, ampliar tolerancias sin justificar,
   marcar `xfail` algo que andaba o agregar `skip` sin motivo escrito.
6. Si un test falla y no está claro si el bug es del test o del código: reportalo, no adivines.
7. Un test intermitente puede depender del RELOJ: aquí, sellos ISO con hora (`risk_computed_at=...T23:50:37`) que `extract_numbers`
   leía como cifras (arreglado en `narrative._ISO_DATE`). Para depurar, capturar el prompt/contexto en una corrida que falle (loop de ~40).
8. Los tests sobre los archivos de la demo (`sample_data/`, ficticios) pueden fijar conteos exactos; si los datos se regeneran con `scripts/generate_demo_data.py`, actualizar esos valores (y los dorados) en el mismo cambio y decirlo en el docstring.
9. Un test de mutación/revert de código de producción (probar que falla si se revierte) exige re-verificar el archivo después: confirmar que quedó restaurado.
