"""
Fase 2: frescura trimestral y botón de actualizar.

Regla central: sin un trimestre nuevo publicado en la fuente, nada vuelve a descargar los
estados contables (ni el GET, ni ?force=true, ni el POST de refresh). El sondeo es liviano.
"""
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import financials as fin
from fakes import QUARTER_DATES, QUARTER_INCOME, ETF_INFO, FakeTicker, statement

NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


def seed(client, sym='ACME', **overrides):
    """Crea la caché con una descarga real (fake) y ajusta campos de frescura."""
    client.get(f'/api/financials/{sym}')
    entry = {**fin.read_cache(sym), **overrides}
    fin.write_cache(sym, entry)
    return entry


def due(**extra):
    """Caché con el reporte ya vencido: último trimestre 2026-06-30, resultados hace 5 días."""
    return {'latest_period_end': '2026-06-30', 'newest_available_period_end': '2026-06-30',
            'next_earnings_date': (TODAY - timedelta(days=5)).isoformat(),
            'probed_at': (NOW - timedelta(hours=13)).isoformat(), **extra}


class ProbeOnlyTicker(FakeTicker):
    """Falla si el sondeo intenta algo más caro que quarterly_income_stmt."""
    EXPENSIVE = ('info', 'income_stmt', 'cashflow', 'calendar', 'earnings_history', 'funds_data')

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._armed = True

    def __getattribute__(self, name):
        if name in ProbeOnlyTicker.EXPENSIVE and object.__getattribute__(self, '__dict__').get('_armed'):
            raise AssertionError(f'El sondeo no debe pedir {name}')
        return super().__getattribute__(name)


# ── Reglas puras ─────────────────────────────────────────────────────────────

def test_needs_probe_rules():
    base = {'kind': 'stock', **due()}
    assert fin.needs_probe(base, NOW) is True
    assert fin.needs_probe({**base, 'probed_at': (NOW - timedelta(hours=1)).isoformat()}, NOW) is False
    assert fin.needs_probe({**base, 'probed_at': None}, NOW) is True
    assert fin.needs_probe({**base, 'newest_available_period_end': '2026-09-30'}, NOW) is False
    future = {**base, 'next_earnings_date': (TODAY + timedelta(days=10)).isoformat()}
    assert fin.needs_probe(future, NOW) is False
    assert fin.needs_probe({'kind': 'etf'}, NOW) is False
    assert fin.needs_probe({'kind': 'crypto'}, NOW) is False
    assert fin.needs_probe(None, NOW) is False


def test_source_with_newer_quarter_wins_over_calendar():
    """Reporte adelantado: la fuente ya publicó aunque el calendario diga una fecha futura."""
    s = fin.freshness_state('2026-06-30', TODAY + timedelta(days=20), TODAY, '2026-09-30')
    assert s == 'available'


def test_etf_expires_after_ttl():
    entry = {'kind': 'etf', 'fetched_at': (NOW - timedelta(days=31)).isoformat(), 'payload': {}}
    assert fin.freshness_from_entry(entry, NOW)['state'] == 'available'
    entry['fetched_at'] = (NOW - timedelta(days=2)).isoformat()
    assert fin.freshness_from_entry(entry, NOW)['state'] == 'fresh'


# ── Sondeo liviano ───────────────────────────────────────────────────────────

def test_probe_detects_new_quarter_with_a_single_cheap_call(client, fake_yf):
    seed(client, **due())
    fake_yf['ACME'] = ProbeOnlyTicker('ACME')          # la fuente ya tiene 2026-09-30
    fake_yf.calls.clear()
    f = fin.check_ticker('ACME')
    assert fake_yf.calls == ['ACME']
    assert f['state'] == 'available'
    assert f['newest_available_label'] == 'Q3 2026'
    assert fin.read_cache('ACME')['probed_at'] is not None


def test_probe_without_new_quarter_stays_pending(client, fake_yf):
    seed(client, **due())
    q = {k: v[2:] for k, v in QUARTER_INCOME.items()}       # la fuente todavía termina en 2026-03-31...
    fake_yf['ACME'] = ProbeOnlyTicker('ACME', q_income=statement(q, QUARTER_DATES[2:]))
    assert fin.check_ticker('ACME')['state'] == 'pending'


def test_probe_not_repeated_within_12_hours(client, fake_yf):
    seed(client, **due(probed_at=(NOW - timedelta(hours=2)).isoformat()))
    fake_yf.calls.clear()
    assert fin.check_ticker('ACME')['state'] == 'pending'
    assert fake_yf.calls == []


def test_probe_failure_is_harmless(client, fake_yf, monkeypatch):
    seed(client, **due())
    monkeypatch.setattr(fin, 'probe_newest_period', lambda t: None)
    assert fin.check_ticker('ACME')['state'] == 'pending'


# ── GET nunca descarga si hay caché ─────────────────────────────────────────

def test_get_serves_cache_even_when_report_is_due(client, fake_yf):
    seed(client, **due())
    fake_yf.calls.clear()
    data = client.get('/api/financials/ACME').get_json()
    assert data['cached'] is True and fake_yf.calls == []
    assert data['freshness']['state'] == 'pending'


# ── POST /api/financials/<t>/refresh ─────────────────────────────────────────

def test_refresh_rejected_with_409_when_fresh(client, fake_yf):
    seed(client)
    fake_yf.calls.clear()
    res = client.post('/api/financials/ACME/refresh')
    assert res.status_code == 409
    body = res.get_json()
    assert body['updated'] is False and body['state'] == 'fresh'
    assert fake_yf.calls == [], 'Rechazado = cero llamadas a la fuente'


def test_refresh_rejected_when_pending(client, fake_yf):
    seed(client, **due())
    assert client.post('/api/financials/ACME/refresh').status_code == 409


def test_refresh_applies_when_available(client, fake_yf):
    seed(client, **due(newest_available_period_end='2026-09-30'))
    res = client.post('/api/financials/ACME/refresh')
    assert res.status_code == 200
    body = res.get_json()
    assert body['updated'] is True
    assert body['data']['latest_period_end'] == '2026-09-30'
    assert body['freshness']['state'] == 'fresh', 'Tras actualizar, vuelve a fresh hasta el próximo reporte'
    assert client.post('/api/financials/ACME/refresh').status_code == 409


def test_refresh_first_time_downloads(client, fake_yf):
    res = client.post('/api/financials/NEWCO/refresh')
    assert res.status_code == 200 and res.get_json()['updated'] is True


def test_refresh_crypto_rejected(client, fake_yf):
    client.get('/api/financials/SOL')
    assert client.post('/api/financials/SOL/refresh').status_code == 409
    assert fake_yf.calls == []


def test_etf_refresh_only_after_ttl(client, fake_yf):
    fake_yf['FETF'] = FakeTicker('FETF', info=dict(ETF_INFO))
    seed(client, 'FETF')
    assert client.post('/api/financials/FETF/refresh').status_code == 409
    seed(client, 'FETF', fetched_at=(NOW - timedelta(days=31)).isoformat())
    assert client.post('/api/financials/FETF/refresh').status_code == 200


# ── Estado en lote ───────────────────────────────────────────────────────────

def test_status_is_offline_and_reports_each_state(client, fake_yf):
    seed(client, 'ACME')
    seed(client, 'DUE', **due())
    seed(client, 'NEW', **due(newest_available_period_end='2026-09-30'))
    fake_yf.calls.clear()
    body = client.get('/api/financials/status?tickers=ACME,DUE,NEW,NOCACHE,SOL').get_json()
    assert fake_yf.calls == []
    assert body['ACME']['state'] == 'fresh'
    assert body['ACME']['next_check_date'] == body['ACME']['next_earnings_date']
    assert body['DUE']['state'] == 'pending'
    assert body['NEW']['state'] == 'available' and body['NEW']['newest_available_label'] == 'Q3 2026'
    assert body['NOCACHE']['state'] == 'unknown'
    assert body['SOL']['state'] == 'unsupported'


def test_status_ignores_invalid_symbols(client):
    body = client.get('/api/financials/status?tickers=JPM,<x>').get_json()
    assert list(body) == ['JPM']


# ── Refresh masivo ───────────────────────────────────────────────────────────

def test_bulk_refresh_only_touches_available(client, fake_yf, server_module, monkeypatch):
    monkeypatch.setattr(server_module, 'ACTIVE_TICKERS', ['ACME', 'DUE', 'NEW', 'SOL'])
    seed(client, 'ACME')
    seed(client, 'DUE', **due())
    seed(client, 'NEW', **due(newest_available_period_end='2026-09-30'))
    fake_yf.calls.clear()
    body = client.post('/api/refresh/financials').get_json()
    assert body['updated'] == ['NEW'] and body['checked'] == 1
    assert set(fake_yf.calls) == {'NEW'}


# ── Hilo de fondo ────────────────────────────────────────────────────────────

def test_background_checker_probes_due_tickers(client, fake_yf):
    seed(client, 'DUE', **due())
    seed(client, 'ACME')
    fake_yf.calls.clear()
    stop = fin.start_background_checker(lambda: ['DUE', 'ACME'], interval_hours=24, initial_delay_s=0)
    try:
        deadline = time.time() + 3
        while time.time() < deadline and fin.get_freshness('DUE')['state'] != 'available':
            time.sleep(0.05)
    finally:
        stop.set()
    assert fin.get_freshness('DUE')['state'] == 'available'
    assert fake_yf.calls == ['DUE'], 'Solo se sondea lo vencido'


# ── Concurrencia ─────────────────────────────────────────────────────────────

def test_concurrent_first_requests_download_once(client, fake_yf):
    """Al abrir la tab, selectTicker y switchTab piden lo mismo a la vez (antes: 500 por carrera de archivos)."""
    import threading
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(client.get('/api/financials/ACME').status_code))
               for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes == [200] * 8
    assert fake_yf.calls.count('ACME') == 1, 'Ocho pedidos simultáneos, una sola descarga'


def test_cache_dir_has_no_leftover_temp_files(client, fake_yf):
    import os
    client.get('/api/financials/ACME')
    assert not [f for f in os.listdir(fin.CACHE_DIR) if f.endswith('.tmp')]
