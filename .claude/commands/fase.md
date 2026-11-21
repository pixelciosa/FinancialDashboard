---
description: Ejecuta una fase del plan de tasks/todo.md con el agente responsable y la cierra con verificación.
argument-hint: <número de fase, ej. 1>
---

Ejecutá la **Fase $ARGUMENTS** de `tasks/todo.md`.

1. Leé solo la sección de esa fase en `tasks/todo.md` y los tests que la definen como contrato
   (por ejemplo `tests/test_financials_spec.py` y los `xfail` que mencionan "Fase $ARGUMENTS").
2. Si la fase anterior tiene ítems sin marcar de los que esta depende, avisá y pará.
3. Marcá la fase como "En progreso" y delegá:
   - Fases 1 y 2 → `data-engineer`
   - Fase 3 → `frontend-engineer`
   El brief incluye: ítems de la fase, tests que deben pasar, skills a cargar y qué queda fuera.
4. Al volver el agente, lanzá `qa-verifier` una vez.
5. Si hay observaciones altas: una sola ronda de corrección y una sola re-verificación. Si persisten, pará y reportá.
6. Marcá los ítems hechos en `tasks/todo.md` y resumí en ≤10 líneas: cambios, tests, pendientes.
