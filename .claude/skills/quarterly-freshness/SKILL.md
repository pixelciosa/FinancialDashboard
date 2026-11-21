---
name: quarterly-freshness
description: Contrato de la lógica que decide si hay datos financieros nuevos y si el botón de actualizar se habilita (estados fresh, pending, available). Usar al implementar financials.py, sus endpoints o el botón en la UI.
---

# Frescura de datos trimestrales

Los estados contables cambian una vez por trimestre. Consultar yfinance en cada visita es
desperdicio y el botón de actualizar solo se habilita cuando hay un trimestre nuevo publicado.
El contrato ejecutable está en `tests/test_financials_spec.py`.

## Caché en disco: `data/financials/<TICKER>.json`
```json
{
  "ticker": "JPM",
  "latest_period_end": "2026-09-27",
  "next_earnings_date": "2027-01-29",
  "probed_at": "2026-11-06T20:00:00Z",
  "newest_available_period_end": "2026-09-27",
  "fetched_at": "2026-10-31T13:10:00Z",
  "payload": { "...": "lo que devuelve GET /api/financials/<t>" }
}
```

## API de `financials.py`
Implementado completo (Fases 1 y 2). Funciones clave: `get_financials` (GET, siempre caché),
`refresh_ticker` (compuerta), `check_ticker` / `needs_probe` (sondeo), `freshness_from_entry` (sin red),
`start_background_checker` (hilo).
```python
quarter_label(ts) -> 'Q3 2026'                         # trimestre calendario del cierre
fiscal_quarter_label(ts, fiscal_year_end_month) -> 'Q3 FY26'
compute_ttm(series_newest_first) -> float | None        # 4 valores completos o None
asset_kind(ticker) -> 'stock' | 'etf' | 'crypto'
next_check_date(latest_period_end, next_earnings_date) -> date
freshness_state(latest_period_end, next_earnings_date, today,
                newest_available_period_end=None) -> 'fresh' | 'pending' | 'available'
get_freshness(ticker) -> {'state', 'latest_period', 'next_earnings', 'checked_at'}
```

## Reglas de estado
| Estado | Condición | UI |
|---|---|---|
| `fresh` | `today < next_check_date` | botón deshabilitado, tooltip "Datos al Q3 2026 · próximo reporte 29 ene" |
| `pending` | ya pasó la fecha, la fuente no tiene un período posterior | deshabilitado, "Esperando publicación" |
| `available` | `newest_available_period_end > latest_period_end` (gana aunque el calendario diga otra fecha) | habilitado, badge "Nuevo Q4 2026" |
| `unknown` | nunca se pidió el ticker | el GET descarga la primera vez |
| `unsupported` | cripto | sin botón, mensaje de que no aplica |

- `next_check_date` = `next_earnings_date` si existe; si no: `latest_period_end + 3 meses + 45 días`.
- ETFs: TTL fijo de 30 días desde `fetched_at`.
- Chequeo liviano (solo `quarterly_income_stmt.columns[0]`), como máximo cada 12 h por ticker y
  solo para tickers cuya `next_check_date` ya pasó.

## Endpoints
- `GET /api/financials/<t>`: siempre desde disco (instantáneo) + bloque `freshness`. Primera vez: descarga completa.
  `?force=true` no saltea la compuerta (se mantiene por compatibilidad con el frontend viejo).
- `GET /api/financials/status?tickers=A,B`: lote, **sin red**, solo lee caché.
- `POST /api/financials/<t>/refresh`: revalida en el servidor. Si el estado no es `available` → **409** `{"updated": false, "state": ...}`.
- `POST /api/refresh/financials`: actualiza solo los `available`.
- Background: al iniciar y cada 12 h, un hilo con `ThreadPoolExecutor(max_workers=4)`.

## No hacer
- No confiar solo en el `disabled` del botón: el servidor valida siempre.
- No sobrescribir la caché con una respuesta vacía de yfinance.
- No usar IA para estimar fechas ni datos faltantes.
