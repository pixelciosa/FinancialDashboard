---
name: portfolio-lab
description: Reglas de oro del Portfolio Lab (lab/, lab_ui/, tests/lab/, config/lab/): encapsulamiento, mapa de módulos, esquema del libro, convenciones del motor, endpoints, archivos de configuración del usuario y cómo agregar una fase. Usar antes de tocar cualquier cosa del Lab.
---

# Portfolio Lab

Segundo módulo del dashboard (página `/portfolio`, API `/api/lab/*`), activado con `PORTFOLIO_LAB=1`.
Plan y fases: `tasks/` (local, fuera de git). Documentación de uso: sección "Portfolio Lab" del `README.md`.

## Reglas de oro
1. **Encapsulamiento**: código nuevo solo en `lab/`, `lab_ui/`, `tests/lab/`, `config/lab/`. Toques permitidos a lo existente, y ninguno más:
   `server.py` (registrar el Blueprint dentro de `try/except`, detrás de `PORTFOLIO_LAB=1`), `index.html` (un enlace a `/portfolio` con su
   clave i18n EN/ES, y el deep link `/?ticker=XXX`). Cualquier otro toque: PARAR y preguntar. Revisar `git diff` antes de cerrar.
2. `lab/` no importa funciones de `server.py`, `financials.py`, `risk.py`, etc. Usa su propio adaptador de precios y su caché en `data/lab/prices/`.
3. **Sin IA** en el Lab. Faltantes = `null`, nunca `0`; nada de `NaN`/`Infinity` en JSON.
4. Funciones puras para el cálculo (reciben listas/DataFrames) y rutas Flask finas. Fallas aisladas: todo `/api/lab/*` responde JSON legible, nunca rompe rutas existentes.
5. Todo `{ok:false}` lleva `codigo` (cadena estable) además de `error`. Un código nuevo se agrega a la tabla del README (un test lo exige).
6. Tests offline: downloaders falsos, `tmp_path`, sin red, sin escribir en `data/lab/` real. Un agente a la vez sobre `tests/`.

## Mapa de módulos (`lab/`)
| Módulo | Qué hace |
|---|---|
| `api.py` | Blueprint `/api/lab`, página `/portfolio`, handler de errores JSON, helper `_err(codigo, ...)` |
| `ledger.py` | Parser del libro (CSV limpio, `LEDGER_CSV`), plataformas excluidas, conciliación con `PORTFOLIO_CSV`, ventas recortadas |
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
Archivo: `fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen[;plataforma]` (ver docstring de `lab/ledger.py`).
Normalizado: `fecha, ticker, cantidad (con signo), precio_usd, monto_usd, tipo, plataforma, origen, nota`, con `tipo` en
`apertura, compra, venta, canje, dividendo_unidades, dividendo_efectivo`. Cripto sin sufijo en el libro; con `-USD` en precios
(`prices.price_symbol` / `prices.to_price_ops`). Las filas de una plataforma listada en `excluded_platforms` (`config/lab/config.json`)
quedan fuera del Lab y solo sirven para conciliar (estado `esperada_excluida`).
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
