---
description: Audita un ticker contra la fuente real: compara lo que muestra el dashboard con yfinance/CoinGecko y marca diferencias. Solo lectura.
argument-hint: <TICKER>
---

Auditá el ticker **$ARGUMENTS** sin modificar archivos. Usá la red real; no uses IA.

1. Con `venv/bin/python`, en un solo script corto:
   - Llamá a los endpoints con `server.app.test_client()`: `/api/financials/$ARGUMENTS`,
     `/api/risk/$ARGUMENTS` y `/api/financials/status?tickers=$ARGUMENTS`.
   - Traé la fuente cruda: `yf.Ticker(sym)` con `-USD` si es cripto: `info`, `income_stmt`,
     `quarterly_income_stmt`, `cashflow`, `calendar`.
2. Compará y reportá en una tabla: métrica · dashboard · fuente · diferencia · OK/ERROR.
   Mínimo: nombre del activo, precio, último período disponible, ingresos, márgenes, FCF,
   P/E, EPS y próxima fecha de resultados.
3. Marcá aparte cualquier dato del reporte de riesgo que no tenga respaldo en la fuente
   (cifras de la tabla trimestral, tarjetas de valuación, segmentos).
4. Cerrá con: veredicto (confiable / con diferencias / no confiable) y, si hay errores, qué test
   faltaría para atraparlos.
