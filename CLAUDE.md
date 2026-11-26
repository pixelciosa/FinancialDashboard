# Financial Dashboard (demo edition)

- **Datos**: ficticios, en `sample_data/` (rutas configurables con `PORTFOLIO_CSV`, `CASH_CSV`, `WATCHLIST_CSV`, `LEDGER_CSV`, `INVESTMENTS_LEDGER_CSV`)
- **Servidor**: `source venv/bin/activate && python server.py` en `http://localhost:5050`
- **Tests**: `python -m pytest` (offline, ~20 s, 853 tests). Con red real: `python -m pytest -m live`
- **Plan y estado**: `tasks/todo.md` · **Lecciones**: `tasks/lessons.md` (locales, fuera de git)

## Objetivo
Dashboard local para seguir y analizar un portfolio (acciones, ETFs, cripto, liquidez) y tomar
decisiones informadas. Público: inversor de nivel intermedio; la UI explica términos y fórmulas.

## Stack (no migrar sin pedido explícito)
Python 3.11 · Flask · yfinance 1.3 · pandas · CoinGecko (sin key).
Frontend: un solo `index.html` con CSS y JS embebidos, TradingView Lightweight Charts 4.2.
IA: modelo de NVIDIA NIM (`NVIDIA_API_KEY` y `NVIDIA_MODEL` en `.env`) solo para texto narrativo.

## Mapa del código
| Archivo | Contenido |
|---|---|
| `server.py` | Rutas Flask, cachés TTL en memoria, `/api/portfolio`, `/api/financials`, `/api/risk`, valuaciones, chat y Explorar |
| `portfolio_data.py` | Lectura de los CSV de entrada (portfolio, liquidez, watchlist) y metadatos de tickers |
| `financials.py` | Tab financiera: trimestres, TTM, EPS vs estimado, ETFs, caché en `data/financials/` |
| `risk.py` | Modelo de riesgo 0-100 (más alto = más riesgo), sin IA. Reglas: skill `risk-model` |
| `narrative.py` | Narrativa IA validada contra los datos calculados (sin cifras inventadas) |
| `valuations.py` | Registro de valuaciones (Barata/Justa/Cara/Info no disponible) y actualización en lote con una sola descarga de precios |
| `agent.py` | Portfolio Agent (chat): contexto desde las cachés, tablas armadas por el servidor, cifras verificadas |
| `explore.py` | Modo Explorar: perfil del portfolio, screener de Yahoo (NYSE/Nasdaq), candidatos con el cálculo propio, motivos por plantilla, preferencias en `data/explore_profile.json` |
| `lab/` | Backend del Portfolio Lab: libro, motor, exposición, macro, estrés, diario. Blueprint en `/api/lab/*`. Skill `portfolio-lab` y README (sección Portfolio Lab) |
| `lab_ui/` | UI del Lab en `/portfolio`: `index.html`, `lab.css`, `tokens.css`, `lab_app.js`, `lab_charts.js`, `lab_i18n.js` |
| `index.html` | ~6.600 líneas. **No leerlo entero**: ubicar con `grep -n` y leer rangos |
| `sample_data/` | CSV ficticios de la demo (portfolio, liquidez, watchlist, libro del Lab) |
| `config/lab/` | Configuración del Portfolio Lab (bloques, escenarios, reglas macro, plataformas excluidas) |
| `tests/` | Suite offline + `tests/live/` |

## Portfolio Lab
- Activo por defecto (`PORTFOLIO_LAB=0` lo desactiva). El libro se lee en cada pedido: historial/aperturas de `LEDGER_CSV` y libros de inversiones de `ledger_files` en `config/lab/config.json` (o `INVESTMENTS_LEDGER_CSV`).
- Un servidor Flask con `debug=False` no recarga: tras cambiar código, reiniciarlo.
- Verificar UI con Chrome headless desde Bash (`--headless=new --window-size=W,H --screenshot=...`).

## Reglas de oro
1. **La IA nunca es fuente de un número.** Precios, métricas, trimestres y veredictos salen de
   yfinance/CoinGecko o de cálculos deterministas. La IA solo redacta texto.
2. **Nada de datos hardcodeados** en el frontend (tickers, años, cifras). Todo viene de la API.
3. **Cripto**: en yfinance siempre con sufijo `-USD` (un símbolo cripto sin sufijo puede ser otro activo).
4. **Datos faltantes son `null`**, nunca `0`. Ningún `NaN`/`Infinity` en JSON.
5. Datos trimestrales: no se re-consultan si no hay trimestre nuevo (ver skill `quarterly-freshness`).
6. Cada cambio de comportamiento lleva su test. Un bug conocido se registra como `xfail` con motivo.

## Cómo trabajamos (agentes)
La sesión principal orquesta: planifica, delega y consolida. No implementa tareas grandes ella misma.

| Quién | Para qué | Modelo |
|---|---|---|
| `Explore` (built-in) | Ubicar código o responder "¿dónde está X?" | haiku |
| `data-engineer` | `server.py`, `financials.py`, fuentes de datos | sonnet |
| `frontend-engineer` | `index.html` | sonnet |
| `qa-verifier` | Correr tests y auditar el cambio. Solo lectura, no corrige | sonnet |

Comandos: `/arquitecto <pedido>` · `/fase <n>` · `/verificar [live]` · `/auditar <TICKER>`

### Presupuesto (para no gastar tokens en loops)
- Máximo **2 intentos** para arreglar un test que falla. Al tercero: parar y reportar causa probable.
- Un agente por tarea, con el contexto justo (archivos, líneas, criterio de aceptación).
- No delegar lo trivial (cambio de una línea, pregunta de estado).
- No releer archivos ya leídos; usar `grep -n` + rangos.
- Al cerrar una tarea: `qa-verifier` una sola vez, actualizar `tasks/todo.md`.
