"""Hallazgos de auditoria: sin rutas absolutas en las respuestas, detalle solo en el log,
y limite de cuerpo del journal/refresh con o sin Content-Length."""
import io
import logging
import os

import pytest
from flask import Flask

from ._helpers import ROOT, load_lab

M = load_lab()
api, journal, refresh, prices = M['api'], M['journal'], M['refresh'], M['prices']
blocks, macro, stress = M['blocks'], M['macro'], M['stress']

SECRET = '/Users/x/secreto'
JSON = {'Content-Type': 'application/json'}


def _client():
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    return app.test_client()


def test_redact_function():
    assert SECRET not in api._redact(f'no se pudo leer {SECRET}/blocks.json: roto')
    assert api._redact(f'falta {ROOT}/config/lab/blocks.json') == 'falta config/lab/blocks.json'
    assert '/Users/' not in api._redact(f"abrir '/Users/otro/x y/z.csv' fallo")
    assert api._redact('campo "peso" debe ser > 0') == 'campo "peso" debe ser > 0'
    assert api._redact(f'{ROOT}') == '.'


def test_internal_error_is_generic_and_detail_goes_to_log(monkeypatch, caplog):
    monkeypatch.setattr(api, '_load_inputs', lambda: (_ for _ in ()).throw(RuntimeError(f'boom {SECRET}')))
    with caplog.at_level(logging.ERROR):
        r = _client().get('/api/lab/exposure')
    d = r.get_json()
    assert r.status_code == 500 and d['codigo'] == 'error_interno'
    assert d['error'] == 'Error interno del servidor' and SECRET not in r.get_data(as_text=True)
    assert SECRET in caplog.text and 'RuntimeError' in caplog.text and 'Traceback' in caplog.text


@pytest.mark.parametrize('route,mod,exc,patch', [
    ('/api/lab/exposure', blocks, 'BlocksError', 'load_blocks'),
    ('/api/lab/macro', macro, 'MacroRulesError', 'load_rules'),
])
def test_config_invalida_is_redacted_but_keeps_the_message(monkeypatch, route, mod, exc, patch):
    monkeypatch.setattr(mod, patch, lambda *a, **k: (_ for _ in ()).throw(getattr(mod, exc)(f'{SECRET}.json: falta "x"')))
    r = _client().get(route)
    d = r.get_json()
    assert d['codigo'] == 'config_invalida' and SECRET not in r.get_data(as_text=True) and 'falta "x"' in d['error']


def test_stress_config_invalida_is_redacted(monkeypatch):
    monkeypatch.setattr(stress, 'load_scenarios', lambda: (_ for _ in ()).throw(stress.ScenariosError(f'{SECRET}: roto')))
    r = _client().get('/api/lab/stress')
    assert r.get_json()['codigo'] == 'config_invalida' and SECRET not in r.get_data(as_text=True)


def test_journal_errors_are_redacted(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    err = journal.JournalError(f'journal ilegible en {SECRET}/journal.json')
    boom = lambda *a, **k: (_ for _ in ()).throw(err)
    monkeypatch.setattr(journal, 'load_entries', boom)
    monkeypatch.setattr(journal, 'create_entry', boom)
    monkeypatch.setattr(journal, 'update_entry', boom)
    c = _client()
    for r in (c.get('/api/lab/journal'), c.post('/api/lab/journal', json={'a': 1}),
              c.put('/api/lab/journal/x', json={'a': 1})):
        d = r.get_json()
        assert d['codigo'] == 'journal_corrupto' and SECRET not in r.get_data(as_text=True)
        assert 'journal ilegible' in d['error']


def test_refresh_step_error_has_only_the_class_name_and_detail_in_log(monkeypatch, caplog):
    def boom(**k):
        raise ConnectionError(f'fallo {SECRET}')
    monkeypatch.setattr(prices, 'refresh', boom, raising=False)
    with caplog.at_level(logging.WARNING):
        res = refresh._step(lambda: boom())
    assert res['error'] == 'ConnectionError' and res['ok'] is False
    assert SECRET in caplog.text
    res2 = refresh._step(lambda: {'ok': False, 'error': f'no se pudo {SECRET}'})
    assert SECRET not in str(res2)


# --- limite de cuerpo sin Content-Length -----------------------------------------------------------
def _raw(c, method, url, body):
    return c.open(url, method=method, input_stream=io.BytesIO(body), content_type='application/json',
                  environ_overrides={'CONTENT_LENGTH': None, 'wsgi.input_terminated': True})


BIG = b'{"tesis": "' + b'a' * (api.MAX_JOURNAL_BODY + 10) + b'"}'


@pytest.mark.parametrize('method,url', [('POST', '/api/lab/journal'), ('PUT', '/api/lab/journal/x'),
                                        ('POST', '/api/lab/refresh')])
def test_oversized_body_without_content_length_is_413(method, url):
    r = _raw(_client(), method, url, BIG)
    assert r.status_code == 413 and r.get_json()['codigo'] == 'cuerpo_demasiado_grande'


@pytest.mark.parametrize('method,url', [('POST', '/api/lab/journal'), ('PUT', '/api/lab/journal/x')])
def test_oversized_body_with_content_length_is_413(method, url):
    r = _client().open(url, method=method, data=BIG, headers=JSON)
    assert r.status_code == 413


def test_normal_body_without_content_length_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, '_journal_path', lambda: str(tmp_path / 'j.json'))
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, []))
    c = _client()
    body = b'{"ticker": "AAA", "tesis": "t", "condicion_salida": {"tipo": "precio", "operador": ">=", "valor": 5}}'
    r = _raw(c, 'POST', '/api/lab/journal', body)
    assert r.status_code in (201, 400) and r.get_json()['codigo' if r.status_code == 400 else 'ok'] != 'cuerpo_invalido'
    r = _raw(c, 'PUT', '/api/lab/journal/inexistente', b'{"tesis": "x"}')
    assert r.status_code == 404
    monkeypatch.setattr(refresh, 'run_refresh', lambda: {'ok': True})
    r = _raw(c, 'POST', '/api/lab/refresh', b'{}')
    assert r.status_code == 200
