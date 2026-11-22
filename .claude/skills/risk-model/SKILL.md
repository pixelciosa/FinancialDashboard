---
name: risk-model
description: Reglas del modelo de riesgo determinista (risk.py) del Reporte de Riesgo: pilares, bandas de puntos, faltantes vs excluidos, niveles y contrato de /api/risk. Usar antes de tocar risk.py, el Reporte de Riesgo o la narrativa IA.
---

# Modelo de riesgo (risk.py)

**0 a 100, más alto = más riesgo.** Sin IA: cada punto sale de una métrica publicada o calculada.
Decisiones de producto (2026-11-06): riesgo real (no "calidad"), veredicto descriptivo
(valuación + nivel de riesgo, sin COMPRAR/VENDER ni precios objetivo), narrativa con Gemma y
"No disponible en el modelo actual" para lo que el modelo no pueda dar.

## Pilares y bandas (puntos = más riesgo)
| Pilar | Métrica | Máx | Bandas |
|---|---|---|---|
| Valuación (35) | P/E | 12 | <15: 2 · <25: 5 · <40: 8 · resto: 12 · pérdidas: 10 |
| | P/E futuro / actual | 8 | <0.85: 2 · <1: 4 · ≥1: 8 · pérdidas esperadas: 8 |
| | Precio/ventas | 8 | <2: 1 · <5: 3 · <10: 6 · resto: 8 |
| | Rend. flujo de caja libre (FCF/cap.) | 7 | <0 %: 7 · <2: 5 · <5: 3 · resto: 1 · monedas distintas: excluida |
| Finanzas (35) | Margen neto TTM | 8 | <0: 8 · <10: 5 · <20: 3 · resto: 1 |
| | Crecimiento interanual de ingresos | 8 | <-10: 8 · <0: 6 · <5: 4 · <15: 2 · resto: 1 |
| | Deuda/patrimonio | 8 | <0.5: 1 · <1: 3 · <2: 5 · resto u otro negativo: 8 · bancos: excluida |
| | Liquidez corriente | 5 | <1: 5 · <1.2: 3 · <2: 2 · resto: 1 · bancos: excluida |
| | FCF TTM positivo | 6 | ≥0: 1 · <0: 6 · bancos: excluida |
| Costo (ETF, 35) | Costo anual | 12 | <0.2 %: 1 · <0.5: 4 · <1: 8 · resto: 12 |
| | Peso top 10 posiciones | 12 | <30 %: 2 · <50: 6 · <70: 9 · resto: 12 |
| | Peso del mayor sector | 11 | <30 %: 2 · <50: 5 · <70: 8 · resto: 11 |
| Mercado (30) | Volatilidad anualizada | 10 | <20 %: 2 · <35: 4 · <50: 7 · resto: 10 |
| | Caída máxima 1 año | 8 | >-15 %: 1 · >-30: 3 · >-50: 6 · resto: 8 |
| | Tendencia vs media 200 días | 6 | arriba y subiendo: 1 · arriba: 2 · abajo: 4 · abajo y bajando: 6 |
| | Beta | 6 | <0.8: 1 · <1.2: 3 · <1.6: 4 · resto: 6 · cripto: excluida |

Acciones: valuación + finanzas + mercado. ETF: costo + mercado. Cripto: mercado (siempre `-USD`).
Puntaje = puntos / máximo de las métricas incluidas × 100.

## Reglas
- **Faltante** (`missing: true`): la mitad del máximo. No saber también es riesgo.
- **Excluida** (`excluded: true`, con `note`): no aplica; no suma puntos ni máximo.
- **Cobertura** < 50 % → `level: 'insufficient'` y sin etiqueta de valuación.
- Niveles: <30 `low` · <45 `moderate` · <60 `high` · resto `very_high`.
- Valuación (solo acciones): puntos/máx del pilar <0.35 `cheap` · <0.60 `fair` · resto `expensive`.
  Requiere **al menos 2 métricas reales** del pilar (pérdidas cuentan como dato); si no, `valuation_label: null` y
  `valuation_reason` (`insufficient_valuation`, `insufficient_coverage`, `not_applicable`).
- **Valuación al precio del día** (`reprice_metrics`): P/E, P/E futuro, P/S y capitalización × `k = cierre / precio de la
  caché contable`, con el precio de referencia ajustado por splits posteriores (`split_factor_since`). El rendimiento
  FCF se deriva de la capitalización. Detalle en `valuation_basis` del reporte. Sin precio válido no se toca nada.
- Propiedad que protegen los tests: **empeorar un dato nunca baja el riesgo** (con el resto fijo).
- Cifras contables: siempre de `financials.py` (`metrics`), las mismas que ve la tab financiera.
- Yahoo informa `debtToEquity` en %: 150 = 1.5x (se divide por 100 en `financials.raw_metrics`).

## Caché y endpoints
- `data/risk/<T>.json` (schema 2), TTL 6 h; se recalcula antes si la tab financiera descargó un trimestre nuevo.
- `GET /api/risk/<t>`: reporte completo (`pillars`, `market`, `prices_12m`, `events`, `inputs_hash`).
- `GET /api/risk/summary?tickers=`: solo caché, sin red (para el sidebar).
- Registro de valuaciones (`valuations.py`): `GET /api/valuations`, `POST /api/valuations/refresh`, `/status`.
  Escribe la misma caché, así registro, tab y sidebar coinciden. `build_report(hist=..., prev=...)` acepta precios
  ya descargados en lote y reutiliza las fechas de resultados si el trimestre no cambió.
- `market` agrega `ema20` (misma fórmula que el gráfico) y `bb_upper`/`bb_lower` (Bollinger 20, 2σ); no suman puntos.
- **`risk_score` es `null` (nunca 0)** cuando no hay métricas aplicables (todas excluidas; suma de máximos de
  pilares = 0): un 0 significaría "riesgo mínimo", lo cual sería falso. En ese caso `coverage` = 0 y
  `level` = `'insufficient'` (lo mismo ocurre si `coverage` < `MIN_COVERAGE`). `level_for` solo se llama con score no nulo.
- Consumidores (UI, narrativa, agente): con `risk_score` nulo mostrar "No disponible" / "n/a", nunca 0 ni "null/100"
  (`narrative.build_facts` ya imprime "n/a"). En `index.html` lo hacen los helpers `riskGaugeValueSvg` y `riskScoreText`
  (clave i18n `risk_score_na`, EN/ES) y el puntero de la barra de bandas; todos usan `Number.isFinite`, así que NaN/Infinity también
  muestran "No disponible" y un 0 real sí dibuja el gauge.
- `inputs_hash`: la narrativa IA (fase R2) se regenera solo si cambia.
- Eventos: fechas de presentación con `get_earnings_dates` (necesita `lxml`). Si fallan, se usan los
  cierres de trimestre como `quarter_end` (nunca como `earnings`) y el error se informa en consola.

## Narrativa IA (narrative.py)
- La IA recibe el bloque de HECHOS (`build_facts`) y solo redacta: `update`, `catalysts`, `risks`, `bottom_line`.
- Validador por campo (`check_text`): todo número del texto debe estar en los HECHOS (tolerancia de
  redondeo 1.5 %; enteros 0 a 10 libres); sin precio objetivo ni compra/venta; idioma de la UI; largo máximo.
  Un ítem malo descarta la lista entera.
- Inválido o sin respuesta → `None` + motivo (`no_api_key`, `model_error`, `invalid_json`,
  `ungrounded_numbers`, `forbidden_content`, `wrong_language`, `empty`, `too_long`).
  La UI muestra "No disponible en el modelo actual". Nunca texto de relleno.
- Caché `data/narrative/<T>.<lang>.json`; regenera si cambia `inputs_hash` o a los 30 días.

## Pantalla (R3, `index.html`)
- Datos: `fetchRisk(t, force)` → `GET /api/risk/<t>` (caché `RISK`); sidebar con `RISK_SUMMARY` de `/api/risk/summary`.
- Overview: `renderRiskOverview` (gauge con degradado `userSpaceOnUse`, tarjetas por métrica, `RISK_BANDS`
  con los mismos cortes que `risk.LEVELS`; un test lo verifica).
- Deep Dive: `renderRiskDeep` + `loadRiskNarrative` (sondeo cada 4 s mientras Gemma genera).
- Campo de IA rechazado → `.risk-na` con "No disponible en el modelo actual" y el motivo en el tooltip.
- Recalcular (menú o píldora) llama a `?force=1`, nunca a Gemma.

## No hacer
- No usar IA para números, puntajes, eventos ni fechas.
- No mostrar COMPRAR/VENDER ni precios objetivo.
- No cambiar una banda sin actualizar esta tabla y los tests de bordes en `tests/test_risk.py`.
