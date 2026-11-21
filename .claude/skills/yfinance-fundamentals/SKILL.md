---
name: yfinance-fundamentals
description: Referencia práctica de yfinance 1.3 para estados contables, trimestres, TTM, earnings, ETFs y cripto en este dashboard. Usar antes de escribir o corregir código que lea datos fundamentales.
---

# yfinance 1.3: datos fundamentales

## Superficie que usamos
| Necesito | Atributo / método | Notas |
|---|---|---|
| Anual (4-5 FY) | `t.income_stmt`, `t.cashflow`, `t.balance_sheet` | columnas = fechas de cierre, **más nueva primero** |
| Trimestral (4-8 Q) | `t.quarterly_income_stmt`, `t.quarterly_cashflow`, `t.quarterly_balance_sheet` | mismo formato |
| Próxima fecha de resultados | `t.calendar` → `dict`, clave `'Earnings Date'` (lista de `date`) | puede faltar en ADRs |
| EPS estimado vs real | `t.get_earnings_history()` → columnas `epsEstimate`, `epsActual`, `surprisePercent` | sin estimado de ingresos histórico |
| Fin de año fiscal | `t.info['lastFiscalYearEnd']` (epoch) | mes de cierre: varía por empresa (p. ej. ORCL 5, COST 8, CRM 1) |
| ETF | `t.funds_data.top_holdings`, `.sector_weightings`, `.fund_operations` | `info['holdings']` **no existe** |
| Precio | `info['currentPrice'] or info['regularMarketPrice'] or info['navPrice']` | |

## Filas frecuentes (índice del DataFrame)
`Total Revenue`, `Gross Profit`, `Operating Income`, `Net Income`, `Diluted EPS`,
`Operating Cash Flow`, `Capital Expenditure` (negativo), `Free Cash Flow`, `Stockholders Equity`.
Siempre chequear `if 'Fila' in df.index` y tratar la celda con `_clean_num` (puede ser `NaN`).

## Cálculos
- FCF = `Operating Cash Flow + Capital Expenditure` (capex ya viene negativo).
- Margen = concepto / `Total Revenue` × 100, redondeado a 1 decimal. Si falta cualquiera: `None`.
- TTM = suma de los **últimos 4** trimestres; si hay menos de 4 o alguno es `None` → `None`.
- Etiquetas: usar `financials.quarter_label` (corre 45 días para que un cierre del 27-jul sea Q2)
  y `financials.fiscal_quarter_label`. Nunca `strftime('Q%m')` (da el mes).
- Bancos (`sector == 'Financial Services'`): margen bruto y FCF no tienen sentido; ocultarlos.

## Trampas conocidas
- **Moneda de los estados**: `info['financialCurrency']` puede no ser USD (algunos ADRs latinoamericanos reportan en moneda local)
  aunque `info['currency']` (precio) sea USD. Nunca anteponer `$` a cifras contables sin mirarla.
- **`dividendYield`** cambió de fracción a porcentaje en 2025. Calcular `dividendRate / precio`.
- **EPS**: `Diluted EPS` del estado es GAAP; `earnings_history.epsActual` es el ajustado que se
  compara con `epsEstimate`. Son números distintos: mostrar cada uno con su nombre.
- **Columnas vacías**: yfinance a veces agrega un período todo `NaN`. Usar `financials.data_columns(df)`.
- **Unidades**: si el payload dice `$M`, los valores tienen que estar en millones (bug histórico).
- **Cripto**: un símbolo cripto sin sufijo puede ser otro activo (un ETF o una acción homónima). Usar `'<SYM>-USD'`
  (`CRYPTO_YF_SUFFIX` en server.py). Para fundamentales, cripto no aplica.
- Columnas trimestrales de ADRs (presentan 6-K/20-F) llegan con meses de atraso.
- Un `t.info` cuesta una llamada HTTP; `income_stmt` y `quarterly_income_stmt` otra cada uno.
  Para chequear si hay trimestre nuevo alcanza con `quarterly_income_stmt.columns[0]`.
- yfinance a veces devuelve DataFrames vacíos ante rate limit: tratar vacío como "sin datos",
  no como "sin cambios", y no sobrescribir la caché con vacío.
- `pandas 3`: evitar `inplace=True` y encadenar asignaciones; usar `.loc`.

## En tests
Nunca red real: usar `tests/fakes.py::FakeTicker` y el fixture `fake_yf`. Para un caso nuevo,
construir el DataFrame con `statement({'Total Revenue': [...]}, [fechas])`.
