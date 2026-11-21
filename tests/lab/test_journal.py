"""Fase 8: diario de decisiones. Offline; el archivo vive en tmp_path (nunca en data/lab real)."""
import hashlib
import json
import math
import os
import threading
from datetime import date

import pytest
from flask import Flask

from ._helpers import ROOT, load_lab

M = load_lab()
api, journal = M['api'], M['journal']

REAL = os.path.join(ROOT, 'data', 'lab', 'journal.json')


def _real_state():
    if not os.path.exists(REAL):
        return None
    with open(REAL, 'rb') as f:
        return (os.stat(REAL).st_mtime_ns, hashlib.sha256(f.read()).hexdigest())


REAL_BEFORE = _real_state()

DATES = ['2026-11-12', '2026-11-13']
PX = {'SPY': [500.0, 501.0], 'JPM': [100.0, 110.0], 'ORCL': [80.0, 90.0]}
OPS = [{'fecha': '2026-11-12', 'ticker': 'JPM', 'cantidad': 1, 'tipo': 'apertura', 'monto_usd': None},
       {'fecha': '2026-11-12', 'ticker': 'ORCL', 'cantidad': 1, 'tipo': 'apertura', 'monto_usd': None}]
# pesos: JPM 110/200 = 55 %, ORCL 45 %
TODAY = {'d': date(2026, 11, 14)}


@pytest.fixture
def jpath(tmp_path, monkeypatch):
    p = tmp_path / 'lab' / 'journal.json'
    monkeypatch.setattr(journal, '_journal_path', lambda: str(p))
    return p


@pytest.fixture
def client(jpath, monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, PX, OPS))
    TODAY['d'] = date(2026, 11, 14)
    monkeypatch.setattr(api, '_today', lambda: TODAY['d'])
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    return app.test_client()


def _post(c, **kw):
    body = {'ticker': 'jpm', 'tesis': 'Tesis <b>x</b>', 'condicion_salida': {'tipo': 'precio', 'operador': '>=', 'valor': 105}}
    body.update(kw)
    return c.post('/api/lab/journal', json=body)


def _get(c):
    return c.get('/api/lab/journal').get_json()


def _ev(c, cond, ticker='JPM'):
    assert _post(c, ticker=ticker, condicion_salida=cond).status_code == 201
    return _get(c)['entries'][-1]['evaluacion']


def _walk_finite(x):
    if isinstance(x, float):
        assert math.isfinite(x)
    elif isinstance(x, dict):
        [_walk_finite(v) for v in x.values()]
    elif isinstance(x, list):
        [_walk_finite(v) for v in x]


# --- CRUD ----------------------------------------------------------------------------
def test_get_empty_when_file_missing(client, jpath):
    d = _get(client)
    assert d['ok'] and d['entries'] == [] and d['resumen']['total'] == 0 and d['datos_al'] == '2026-11-13'
    assert not jpath.exists()   # GET no escribe


def test_create_read_edit(client, jpath):
    r = _post(client)
    assert r.status_code == 201
    e = r.get_json()['entry']
    assert e['ticker'] == 'JPM' and e['tesis'] == 'Tesis <b>x</b>' and e['estado'] == 'abierta'
    assert e['id'] and e['fecha_alta'] and e['actualizado'] and e['nota_cierre'] is None
    assert json.loads(jpath.read_text())['version'] == 1
    got = _get(client)['entries'][0]
    assert got['id'] == e['id'] and got['evaluacion']['estado'] == 'cumplida'
    r = client.put(f"/api/lab/journal/{e['id']}", json={'estado': 'cerrada', 'nota_cierre': 'vendida', 'tesis': 'nueva'})
    assert r.status_code == 200
    g = _get(client)['entries'][0]
    assert g['estado'] == 'cerrada' and g['nota_cierre'] == 'vendida' and g['tesis'] == 'nueva'
    assert g['id'] == e['id'] and g['fecha_alta'] == e['fecha_alta'] and g['evaluacion'] is None


def test_crypto_ticker_normalized(client):
    assert _post(client, ticker='sol').get_json()['entry']['ticker'] == 'SOL-USD'
    assert _post(client, ticker='SOL-USD').get_json()['entry']['ticker'] == 'SOL-USD'


@pytest.mark.parametrize('kw,campo', [
    ({'ticker': ''}, 'ticker'), ({'ticker': 'X' * 16}, 'ticker'), ({'ticker': 'A B'}, 'ticker'),
    ({'ticker': 5}, 'ticker'), ({'tesis': ''}, 'tesis'), ({'tesis': 'x' * 2001}, 'tesis'),
    ({'tesis': 3}, 'tesis'),
    ({'condicion_salida': {'tipo': 'otro', 'operador': '>=', 'valor': 1}}, 'condicion_salida.tipo'),
    ({'condicion_salida': {'tipo': 'precio', 'operador': '==', 'valor': 1}}, 'condicion_salida.operador'),
    ({'condicion_salida': {'tipo': 'precio', 'operador': '>=', 'valor': 'abc'}}, 'condicion_salida.valor'),
    ({'condicion_salida': {'tipo': 'precio', 'operador': '>=', 'valor': True}}, 'condicion_salida.valor'),
    ({'condicion_salida': {'tipo': 'precio', 'operador': '>=', 'valor': 0}}, 'condicion_salida.valor'),
    ({'condicion_salida': {'tipo': 'peso', 'operador': '>=', 'valor': 101}}, 'condicion_salida.valor'),
    ({'condicion_salida': {'tipo': 'peso', 'operador': '<=', 'valor': -1}}, 'condicion_salida.valor'),
    ({'condicion_salida': {'tipo': 'fecha', 'operador': '>=', 'valor': '2026-12-01'}}, 'condicion_salida.operador'),
    ({'condicion_salida': {'tipo': 'fecha', 'valor': '2026-13-01'}}, 'condicion_salida.valor'),
    ({'condicion_salida': {'tipo': 'fecha', 'valor': 20261201}}, 'condicion_salida.valor'),
    ({'condicion_salida': 'x'}, 'condicion_salida'),
    ({'estado': 'zzz'}, 'estado'), ({'nota_cierre': 'n' * 501}, 'nota_cierre'),
    ({'id': 'mio'}, 'id'), ({'fecha_alta': '2020-01-01'}, 'fecha_alta'),
])
def test_create_validation_400(client, jpath, kw, campo):
    r = _post(client, **kw)
    d = r.get_json()
    assert r.status_code == 400 and d['ok'] is False and d['campo'] == campo and d['error']
    assert not jpath.exists()


def test_missing_fields_and_non_object_body(client):
    r = client.post('/api/lab/journal', json={'ticker': 'JPM'})
    assert r.status_code == 400 and r.get_json()['campo'] == 'tesis'
    r = client.post('/api/lab/journal', json=[1, 2])
    assert r.status_code == 400


def test_nan_in_body_rejected(client):
    r = client.post('/api/lab/journal', data='{"ticker":"JPM","tesis":"x","condicion_salida":{"tipo":"precio","operador":">=","valor":NaN}}',
                    content_type='application/json')
    assert r.status_code == 400


def test_415_and_413(client, jpath):
    r = client.post('/api/lab/journal', data='{}', content_type='text/plain')
    assert r.status_code == 415
    r = client.post('/api/lab/journal', json={'tesis': 'x' * 40000})
    assert r.status_code == 413
    assert client.put('/api/lab/journal/abc', data='{}', content_type='text/plain').status_code == 415
    assert not jpath.exists()


def test_put_404_and_immutable_fields(client):
    assert client.put('/api/lab/journal/nope', json={'tesis': 'x'}).status_code == 404
    e = _post(client).get_json()['entry']
    for k in ('id', 'fecha_alta', 'ticker'):
        r = client.put(f"/api/lab/journal/{e['id']}", json={k: 'zzz'})
        assert r.status_code == 400 and r.get_json()['campo'] == k
    r = client.put(f"/api/lab/journal/{e['id']}", json={'condicion_salida': {'tipo': 'fecha', 'valor': 'mala'}})
    assert r.status_code == 400
    assert _get(client)['entries'][0]['condicion_salida']['tipo'] == 'precio'   # sin cambios


def test_no_delete_route(client):
    e = _post(client).get_json()['entry']
    assert client.delete(f"/api/lab/journal/{e['id']}").status_code == 405


def test_entry_limit(client, jpath, monkeypatch):
    monkeypatch.setattr(journal, 'MAX_ENTRIES', 3)
    for _ in range(3):
        assert _post(client).status_code == 201
    r = _post(client)
    assert r.status_code == 409 and 'limite' in r.get_json()['error']
    assert len(json.loads(jpath.read_text())['entries']) == 3


# --- Persistencia ----------------------------------------------------------------------
def test_corrupt_file_is_kept_and_reported(client, jpath):
    jpath.parent.mkdir(parents=True)
    jpath.write_text('{"entries": [ roto')
    d = client.get('/api/lab/journal')
    j = d.get_json()
    assert d.status_code == 200 and j['status'] == 'journal_corrupto' and j['error'] and j['entries'] == []
    assert _post(client).status_code == 409
    assert client.put('/api/lab/journal/x', json={'tesis': 'a'}).status_code == 409
    assert jpath.read_text() == '{"entries": [ roto'


def test_wrong_structure_is_corrupt(client, jpath):
    jpath.parent.mkdir(parents=True)
    jpath.write_text('[1,2,3]')
    assert _get(client)['status'] == 'journal_corrupto'
    assert jpath.read_text() == '[1,2,3]'


def test_atomic_write_failure_keeps_old_file(client, jpath, monkeypatch):
    _post(client)
    before = jpath.read_text()

    def boom(a, b):
        raise OSError('disco lleno')
    monkeypatch.setattr(journal.os, 'replace', boom)
    with pytest.raises(OSError):
        journal.create_entry({'ticker': 'JPM', 'tesis': 'x',
                              'condicion_salida': {'tipo': 'precio', 'operador': '>=', 'valor': 1}})
    assert jpath.read_text() == before
    assert [p.name for p in jpath.parent.iterdir()] == ['journal.json']   # sin temporales


def test_concurrent_creates_do_not_lose_entries(client, jpath):
    def go():
        journal.create_entry({'ticker': 'JPM', 'tesis': 'x',
                              'condicion_salida': {'tipo': 'precio', 'operador': '>=', 'valor': 1}})
    ts = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(json.loads(jpath.read_text())['entries']) == 12


# --- Evaluacion ------------------------------------------------------------------------
def test_precio_ge_and_le(client):
    a = _ev(client, {'tipo': 'precio', 'operador': '>=', 'valor': 110})   # en el borde
    assert a['estado'] == 'cumplida' and a['valor_actual'] == 110.0 and a['detalle'] == 'precio_cumple'
    assert a['datos_al'] == '2026-11-13'
    assert _ev(client, {'tipo': 'precio', 'operador': '>=', 'valor': 110.01})['estado'] == 'no_cumplida'
    assert _ev(client, {'tipo': 'precio', 'operador': '<=', 'valor': 110.5})['estado'] == 'cumplida'
    b = _ev(client, {'tipo': 'precio', 'operador': '<=', 'valor': 100})
    assert b['estado'] == 'no_cumplida' and b['detalle'] == 'precio_no_cumple'


def test_peso_in_portfolio_and_zero_holding(client):
    a = _ev(client, {'tipo': 'peso', 'operador': '>=', 'valor': 50})
    assert a['estado'] == 'cumplida' and a['valor_actual'] == 55.0
    assert _ev(client, {'tipo': 'peso', 'operador': '>=', 'valor': 60})['estado'] == 'no_cumplida'
    assert _ev(client, {'tipo': 'peso', 'operador': '<=', 'valor': 60})['estado'] == 'cumplida'
    z = _ev(client, {'tipo': 'peso', 'operador': '<=', 'valor': 5}, ticker='SPY')   # con precio, sin tenencia
    assert z['estado'] == 'cumplida' and z['valor_actual'] == 0.0 and z['detalle'] == 'tenencia_cero'
    z2 = _ev(client, {'tipo': 'peso', 'operador': '>=', 'valor': 5}, ticker='CRM')
    assert z2['estado'] == 'no_cumplida' and z2['detalle'] == 'tenencia_cero'


def test_peso_holding_without_price_is_sin_dato_not_tenencia_cero(client, monkeypatch):
    px = dict(PX, CRM=[None, None])
    ops = OPS + [{'fecha': '2026-11-12', 'ticker': 'CRM', 'cantidad': 2, 'tipo': 'apertura', 'monto_usd': None}]
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, px, ops))
    a = _ev(client, {'tipo': 'peso', 'operador': '<=', 'valor': 5}, ticker='CRM')   # tenido, sin precio
    assert a['estado'] == 'sin_dato' and a['valor_actual'] is None and a['detalle'] == 'sin_precio_ticker'
    p = _ev(client, {'tipo': 'precio', 'operador': '<=', 'valor': 5}, ticker='CRM')
    assert p['estado'] == 'sin_dato' and p['valor_actual'] is None and p['detalle'] == 'sin_precio_ticker'
    z = _ev(client, {'tipo': 'peso', 'operador': '<=', 'valor': 5}, ticker='TSLA')   # no tenido
    assert z['estado'] == 'cumplida' and z['valor_actual'] == 0.0 and z['detalle'] == 'tenencia_cero'
    w = _ev(client, {'tipo': 'peso', 'operador': '>=', 'valor': 50})   # con precio: sin cambios
    assert w['estado'] == 'cumplida' and w['valor_actual'] == 55.0 and w['detalle'] == 'peso_cumple'


def test_evaluate_sin_precio_param_is_optional():
    e = {'estado': 'abierta', 'ticker': 'CRM', 'condicion_salida': {'tipo': 'peso', 'operador': '<=', 'valor': 5}}
    r = journal.evaluate(e, {}, {}, date(2026, 11, 14), '2026-11-13')
    assert r['detalle'] == 'tenencia_cero' and r['valor_actual'] == 0.0
    r = journal.evaluate(e, {}, {}, date(2026, 11, 14), '2026-11-13', {'CRM'})
    assert r['estado'] == 'sin_dato' and r['valor_actual'] is None


def test_fecha_with_injected_clock(client):
    cond = {'tipo': 'fecha', 'valor': '2026-11-14'}
    _post(client, condicion_salida=cond)
    for hoy, esperado in ((date(2026, 11, 13), 'no_cumplida'), (date(2026, 11, 14), 'cumplida'),
                          (date(2026, 11, 15), 'cumplida')):
        TODAY['d'] = hoy
        ev = _get(client)['entries'][0]['evaluacion']
        assert ev['estado'] == esperado and ev['valor_actual'] == hoy.isoformat()
    assert ev['detalle'] == 'fecha_alcanzada'
    TODAY['d'] = date(2026, 11, 13)
    assert _get(client)['entries'][0]['evaluacion']['detalle'] == 'fecha_pendiente'


def test_precio_unknown_ticker_is_sin_dato_not_zero(client):
    ev = _ev(client, {'tipo': 'precio', 'operador': '<=', 'valor': 5}, ticker='CRM')
    assert ev['estado'] == 'sin_dato' and ev['valor_actual'] is None and ev['detalle'] == 'sin_precio_ticker'


def test_without_prices_is_sin_dato_never_500(client, monkeypatch):
    for c in ({'tipo': 'precio', 'operador': '>=', 'valor': 1}, {'tipo': 'peso', 'operador': '<=', 'valor': 1},
              {'tipo': 'fecha', 'valor': '2026-01-01'}):
        _post(client, condicion_salida=c)
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    r = client.get('/api/lab/journal')
    d = r.get_json()
    assert r.status_code == 200 and d['status'] == 'sin_precios' and d['datos_al'] is None
    evs = [e['evaluacion'] for e in d['entries']]
    assert [e['estado'] for e in evs] == ['sin_dato', 'sin_dato', 'cumplida']   # fecha no necesita precios
    assert evs[0]['valor_actual'] is None and evs[1]['valor_actual'] is None
    assert d['resumen']['sin_dato'] == 2


def test_empty_portfolio_peso_is_sin_dato(client, monkeypatch):
    _post(client, condicion_salida={'tipo': 'peso', 'operador': '<=', 'valor': 1})
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, PX, []))
    ev = _get(client)['entries'][0]['evaluacion']
    assert ev['estado'] == 'sin_dato' and ev['valor_actual'] is None


def test_nan_price_is_sin_dato_and_json_is_finite(client, monkeypatch):
    _post(client)
    monkeypatch.setattr(api, '_load_inputs',
                        lambda: (DATES, dict(PX, JPM=[100.0, float('nan')]), OPS))
    raw = client.get('/api/lab/journal').get_data(as_text=True)
    assert 'NaN' not in raw and 'Infinity' not in raw
    assert json.loads(raw)['entries'][0]['evaluacion']['estado'] == 'sin_dato'


def test_closed_not_evaluated_and_summary(client):
    _post(client)                                               # cumplida
    _post(client, condicion_salida={'tipo': 'precio', 'operador': '>=', 'valor': 999})   # no cumplida
    e = _post(client).get_json()['entry']
    client.put(f"/api/lab/journal/{e['id']}", json={'estado': 'cerrada'})
    d = _get(client)
    assert d['resumen'] == {'total': 3, 'abiertas': 2, 'cerradas': 1, 'cumplidas': 1, 'sin_dato': 0}
    assert d['entries'][2]['evaluacion'] is None
    _walk_finite(d)


def test_thesis_returned_verbatim(client):
    t = '  <script>alert(1)</script> {{7*7}} ${x}\n'
    _post(client, tesis=t)
    assert _get(client)['entries'][0]['tesis'] == t


# --- Aislamiento -------------------------------------------------------------------------
def test_real_data_dir_untouched():
    assert _real_state() == REAL_BEFORE
