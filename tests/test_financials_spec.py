"""
Especificación ejecutable del módulo financials.py (Fases 1 y 2 del plan).

Escrita antes de implementar las Fases 1 y 2; hoy la cumplen financials.py y server.py.
El contrato completo está documentado en .claude/skills/quarterly-freshness/SKILL.md.
"""
from datetime import date

import pandas as pd
import pytest

import financials as fin


# ── Etiquetas de trimestre ──────────────────────────────────────────────────

@pytest.mark.parametrize('end,label', [
    ('2026-03-31', 'Q1 2026'), ('2026-06-30', 'Q2 2026'),
    ('2026-09-30', 'Q3 2026'), ('2025-12-31', 'Q4 2025'),
    ('2026-07-27', 'Q2 2026'),   # cierre irregular: cubre may-jul, o sea mayormente Q2
    ('2026-06-27', 'Q2 2026'),   # Apple
])
def test_quarter_label(end, label):
    assert fin.quarter_label(pd.Timestamp(end)) == label


def test_fiscal_label_for_offset_fiscal_year():
    # Apple cierra el año fiscal en septiembre: el trimestre a jun-2026 es su Q3 FY26
    assert fin.fiscal_quarter_label(pd.Timestamp('2026-06-27'), fiscal_year_end_month=9) == 'Q3 FY26'


# ── TTM ─────────────────────────────────────────────────────────────────────

def test_ttm_sums_last_four():
    assert fin.compute_ttm([110, 105, 100, 104, 102]) == 419


@pytest.mark.parametrize('series', [[1, 2, 3], [1, None, 3, 4], []])
def test_ttm_requires_four_complete_quarters(series):
    assert fin.compute_ttm(series) is None


# ── Estado de frescura (habilita o no el botón de actualizar) ──────────────

TODAY = date(2026, 11, 6)


def test_fresh_before_next_earnings():
    s = fin.freshness_state(latest_period_end=date(2026, 9, 30),
                            next_earnings_date=date(2027, 1, 29), today=TODAY)
    assert s == 'fresh'


def test_pending_after_earnings_until_published():
    s = fin.freshness_state(latest_period_end=date(2026, 6, 30),
                            next_earnings_date=date(2026, 10, 30), today=TODAY,
                            newest_available_period_end=date(2026, 6, 30))
    assert s == 'pending'


def test_available_when_source_has_newer_quarter():
    s = fin.freshness_state(latest_period_end=date(2026, 6, 30),
                            next_earnings_date=date(2026, 10, 30), today=TODAY,
                            newest_available_period_end=date(2026, 9, 30))
    assert s == 'available'


def test_fallback_without_earnings_date():
    # Sin fecha (ADRs): próximo chequeo = fin del trimestre + 3 meses + 45 días
    assert fin.next_check_date(latest_period_end=date(2026, 6, 30), next_earnings_date=None) == date(2026, 11, 14)
    s = fin.freshness_state(latest_period_end=date(2026, 6, 30), next_earnings_date=None, today=TODAY)
    assert s == 'fresh'


def test_crypto_is_unsupported():
    assert fin.asset_kind('SOL') == 'crypto'


# ── Endpoints (requiere que server.py registre las rutas nuevas) ───────────

def test_refresh_is_rejected_when_no_new_data(client, monkeypatch):
    monkeypatch.setattr(fin, 'get_freshness', lambda t: {'state': 'fresh'})
    res = client.post('/api/financials/ACME/refresh')
    assert res.status_code == 409
    assert res.get_json()['updated'] is False


def test_status_endpoint_is_offline_and_batched(client):
    res = client.get('/api/financials/status?tickers=JPM,ORCL')
    assert res.status_code == 200
    body = res.get_json()
    assert set(body) >= {'JPM', 'ORCL'}
    for v in body.values():
        assert v['state'] in ('fresh', 'pending', 'available', 'unsupported', 'unknown')
