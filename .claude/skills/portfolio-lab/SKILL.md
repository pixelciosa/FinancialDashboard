---
name: portfolio-lab
description: Reglas de oro del Portfolio Lab (lab/, lab_ui/, tests/lab/, config/lab/): encapsulamiento, mapa de módulos, esquema del libro, convenciones del motor, endpoints, archivos de configuración del usuario y cómo agregar una fase. Usar antes de tocar cualquier cosa del Lab.
---

# Portfolio Lab

Segundo módulo del dashboard (página `/portfolio`, API `/api/lab/*`), activo por defecto (`PORTFOLIO_LAB=0` lo desactiva).
Plan y fases: `tasks/` (local, fuera de git). Documentación de uso: sección "Portfolio Lab" del `README.md`.

## Reglas de oro
1. **Encapsulamiento**: código nuevo solo en `lab/`, `lab_ui/`, `tests/lab/`, `config/lab/`. Toques permitidos a lo existente, y ninguno más:
   `server.py` (registrar el Blueprint con condición opt-out, `PORTFOLIO_LAB` fuera de 0/false/off/no, dentro de `try/except`), `index.html` (el enlace
   `#pfLabLink` a `/portfolio`, oculto por defecto, con su clave i18n EN/ES; la función `initLabLink()` lo muestra solo si `GET /api/lab/health` responde
   `ok: true`; y el deep link `/?ticker=XXX`). Cualquier otro toque: PARAR y preguntar. Revisar `git diff` antes de cerrar.
2. `lab/` no importa funciones de `server.py`, `financials.py`, `risk.py`, etc. Usa su propio adaptador de precios y su caché en `data/lab/prices/`.
3. **Sin IA** en el Lab. Faltantes = `null`, nunca `0`; nada de `NaN`/`Infinity` en JSON.
4. Funciones puras para el cálculo (reciben listas/DataFrames) y rutas Flask finas. Fallas aisladas: todo `/api/lab/*` responde JSON legible, nunca rompe rutas existentes.
5. Todo `{ok:false}` lleva `codigo` (cadena estable) además de `error`. Un código nuevo se agrega a la tabla del README (un test lo exige).
6. Tests offline: downloaders falsos, `tmp_path`, sin red, sin escribir en `data/lab/` real. Un agente a la vez sobre `tests/`.

## Reglas de UI del Lab (`lab_ui/`)
- Header: la fila 1 (`#labTopbar`: ← Dashboard, título, selector EN/ES, Actualizar) va en el flujo y se desplaza con la página. La toolbar `#labToolbar`
  (Índice de referencia, Período y estado "Datos al…") es lo único fijo (`position: sticky; top: 0`). En <= 860 px nada queda fijo salvo el menú de anclas.
- `trackTopbarHeight` (lab_app.js) mide `#labToolbar` y publica `--lab-topbar-h`; el menú lateral y `scroll-margin-top` usan esa variable.
- Los tokens de color de `lab_ui/tokens.css` son idénticos a los del `:root` del dashboard: lo vigila `tests/lab/test_scaffold.py::test_tokens_match_dashboard`.
  Medir antes de suponer que los colores difieren.
- Selector EN/ES con las clases `.lang-picker` / `.lang-btn` del dashboard (`test_header_lang_picker_matches_dashboard`).
- Verificar la UI con capturas (Chrome headless) a 1470/1440/1280/1024/375 px; un arreglo visual "a ciegas" no alcanza.
- Nota de test: bajo pytest `tests/lab/__init__.py` tapa el paquete `lab`, así que el Blueprint no se registra en `server.app`; por eso
  `tests/test_frontend_contracts.py::test_every_api_call_has_a_route` valida las llamadas `/api/lab/*` contra los `lab_bp.route` de `lab/api.py`.

## Mapa de módulos (`lab/`)
| Módulo | Qué hace |
|---|---|
| `api.py` | Blueprint `/api/lab`, página `/portfolio`, handler de errores JSON, helper `_err(codigo, ...)` |
| `ledger.py` | Parser del libro de inversiones (export de planilla, `ledger_files` / `INVESTMENTS_LEDGER_CSV`), historial/aperturas (CSV limpio, `LEDGER_CSV`), plataformas incluidas/excluidas, conciliación con `PORTFOLIO_CSV`, ventas recortadas |
| `paths.py` | Rutas de entrada: `LAB_CONFIG_DIR`, `LEDGER_CSV`, `PORTFOLIO_CSV` (por defecto `config/lab/` y `sample_data/`) |
| `prices.py` | Cierres ajustados, caché incremental `closes.csv`, `price_symbol` (cripto `-USD`), alineación al calendario de SPY, `update_prices` |
| `engine.py` | TWR, volatilidad, drawdown, mismos aportes, retornos por período, correlación |
| `correlation.py` | Correlación móvil contra referencias (cripto, oro, Nasdaq, dólar) |
| `blocks.py` | Lee/valida `blocks.json` (bloques, banda 5/25, tope por posición) |
| `exposure.py` | Peso y aporte al riesgo por bloque, bandas, alertas (modo descriptivo) |
| `attribution.py` | Aporte por posición (Dietz modificado) y por operación (lotes FIFO) |
| `stress.py` / `stress_prices.py` | Escenarios históricos sobre pesos actuales / caché `stress_closes.csv` |
| `macro.py` / `macro_prices.py` | Semáforo macro (luces, histéresis, régimen, calibración) / caché `macro_closes.csv` |
| `journal.py` | Diario de decisiones (`journal.json`, escritura atómica, evaluación por reglas) |
| `refresh.py` | `POST /refresh`: lock no bloqueante, tres pasos aislados, último resultado en memoria, fechas de las cachés |

## Contrato de datos: esquema normalizado del libro
Entradas (detalle en el docstring de `lab/ledger.py`):
- Historial/aperturas `LEDGER_CSV` (default `sample_data/ledger.csv`): `fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen[;plataforma]`.
- Libros de inversiones (`ledger_files` en `config/lab/config.json`, rutas relativas al proyecto o a `ledger_dir`; `INVESTMENTS_LEDGER_CSV` las reemplaza):
  export de planilla con `;`, fecha d/m/aa, números en formato es-AR (`US$\t(1.234,50)`), columnas
  `Fecha;Activo;Símbolo;Tipo de Operación;Tipo de Activo;Cantidad;Plataforma;Precio en USD (MEP);Precio en moneda de cambio;Moneda de cambio;Inversión en dólares`
  (las siguientes se ignoran). Activos `Stable` se omiten; pagar con otro activo genera la pata `canje`. Demo: `sample_data/investments_ledger.csv` (ficticio).

Normalizado: `fecha, ticker, cantidad (con signo), precio_usd, monto_usd, tipo, plataforma, titular, origen, nota`, con `tipo` en
`apertura, compra, venta, canje, dividendo_unidades, dividendo_efectivo`. Cripto sin sufijo en el libro; con `-USD` en precios
(`prices.price_symbol` / `prices.to_price_ops`). Plataformas: `included_platforms` (si no está vacía, otra plataforma es error de fila) y
`excluded_platforms` (fuera del Lab, solo para conciliar: estado `esperada_excluida`) en `config/lab/config.json`.
Dividendo en efectivo = ingreso, no operación. Venta mayor que la tenencia se recorta (y se informa).

## Convenciones del motor
TWR `r_t = (V_t − F_t)/V_{t−1} − 1`, base 100 encadenado; 252 días; vol = desvío × √252; drawdown contra el máximo previo; calendario = días de SPY
con ffill; atribución con Dietz modificado (no coincide con TWR: esperado); mismos aportes `u += F_t/P_t`.
Macro: ventanas y umbrales salen de `macro_rules.json` (claves del payload neutras: `promedio_movil`, `cambio_pct`; nunca números de ventana en el código).

## Endpoints
`GET /health`, `POST /refresh`, `GET /ledger`, `/reconcile`, `/performance`, `/correlation`, `/risk-return`, `/exposure`, `/attribution`, `/macro`, `/stress`,
`GET|POST /journal`, `PUT /journal/<id>`. Formas y códigos de error en el README. POST/PUT exigen `application/json` (415 si no: evita CSRF).
Los endpoints leen de disco en cada petición; si agregás una caché en memoria, invalidala en `refresh.py` (hay un test que lo vigila).

## Archivos del usuario: no se editan salvo pedido explícito
`config/lab/blocks.json`, `config/lab/macro_rules.json` (el texto), `config/lab/scenarios.json`, `config/lab/config.json` y los CSV de
`sample_data/` (o los que apunten `PORTFOLIO_CSV` / `LEDGER_CSV`). Si hace falta un cambio: aditivo y con respaldo previo.
El código se adapta al JSON, no al revés. Si la calibración marca una luz para revisión, el sistema NO ajusta umbrales: informar.

## Test dorado
`python -m pytest tests/lab/test_engine_golden.py`. Fija el motor contra cifras congeladas al 13/11/2026 (`tests/lab/fixtures/prices_2026-11-13.csv`, precios sintéticos para los
tickers de la demo, generados por `scripts/generate_demo_data.py`).
**Nunca se ajusta el test ni su tolerancia para forzarlo**: si falla, buscar la causa en el motor o en el libro; si la diferencia supera 0,1 pp, PARAR.
Solo se regenera si se cambian a propósito el libro o los datos de la demo (`scripts/generate_demo_data.py`).

## Cómo agregar una fase o endpoint
1. Escribir primero el test con fixtures (en `tests/lab/`, carga el paquete con `_helpers.load_lab()`; agregar el módulo nuevo a esa lista).
2. Módulo puro en `lab/<nombre>.py` (entrada: listas/DataFrames; salida: datos, `None` si falta).
3. Ruta fina en `lab/api.py` que lee entradas (`_load_inputs`, seam monkeypatcheable), llama al módulo y devuelve JSON con `status` y `codigo` en los errores.
4. Tarjeta en `lab_ui/` con estados de carga, vacío y error (una tarjeta que falla no tumba la página), textos EN/ES en `lab_i18n.js`.
5. Documentar el endpoint y sus códigos en el README; correr `python -m pytest` completo.

## Condiciones de parada (sección 9 del plan): parar y preguntar si
- Hace falta tocar un archivo existente fuera de los toques permitidos.
- Un test existente falla y la causa no es evidente.
- El test dorado no reproduce los valores y la diferencia supera 0,1 pp.
- Aparece una fila del libro que ninguna regla de lectura cubre.
- Los pesos por bloque al 13/11/2026 difieren de las referencias de `blocks.json` en más de 0,5 pp.
- La calibración del semáforo marca alguna luz para revisión.
