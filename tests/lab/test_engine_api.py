"""Fase 2: endpoints /performance, /correlation, /risk-return con datos sinteticos (sin red)."""
import json

import pytest
from flask import Flask

from ._helpers import load_lab

M = load_lab()
api = M['api']

DATES = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09',
         '2025-01-10', '2025-01-13']
PX = {'SPY': [100, 101, 99, 102, 103, 101, 104, 105],
      'QQQ': [200, 204, 198, 210, 212, 205, 215, 219],
      'SOL-USD': [50, 52, 49, 55, 54, 50, 58, 60],
      'BTC-USD': [50, 52, 49, 55, 54, 50, 58, 60],
      'JPM': [10, 11, 12, 12, 14, 13, 14, 15]}
OPS = [{'fecha': '2025-01-02', 'ticker': 'JPM', 'cantidad': 10, 'tipo': 'apertura', 'monto_usd': None},
       {'fecha': '2025-01-04', 'ticker': 'SOL-USD', 'cantidad': 2, 'tipo': 'compra', 'monto_usd': 104}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: (DATES, PX, OPS))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    return app.test_client()


def _strict_json(resp):
    return json.loads(resp.get_data(as_text=True), parse_constant=lambda c: pytest.fail(f'{c} en JSON'))


def test_performance_shape(client):
    r = client.get('/api/lab/performance?benchmark=SPY')
    d = _strict_json(r)
    assert r.status_code == 200 and d['ok'] and d['benchmark'] == 'SPY'
    assert len(d['dates']) == len(d['series']['portfolio']['index']) == len(d['series']['benchmark']['drawdown_pct'])
    assert d['series']['portfolio']['index'][0] == 100
    assert {'cum_pct', 'ann_pct', 'vol_pct', 'max_dd_pct'} <= set(d['stats']['portfolio'])
    assert d['monthly']['portfolio'][0]['periodo'] == '2025-01'
    assert d['yearly']['benchmark'][0]['periodo'] == '2025'
    c = d['cash']
    assert c['valor_inicial'] == 100 and c['aportes_netos'] == 104
    assert d['mismos_aportes']['valor_final'] is not None


def test_performance_accepts_crypto_alias_and_rejects_unknown(client):
    assert client.get('/api/lab/performance?benchmark=BTC').get_json()['benchmark'] == 'BTC-USD'
    r = client.get('/api/lab/performance?benchmark=FOO')
    assert r.status_code == 400 and r.get_json()['ok'] is False


def test_correlation_matrix_and_rolling(client):
    d = _strict_json(client.get('/api/lab/correlation?window=5'))
    n = len(d['names'])
    assert d['names'][0] == 'PORT' and len(d['matrix']) == n and all(len(row) == n for row in d['matrix'])
    assert d['matrix'][0][0] == 1.0
    roll = d['rolling']['SPY']
    assert len(roll) == len(d['dates']) and roll[0] is None
    assert 'ADA-USD' not in d['names'] and 'XLK' in d['missing']


def test_correlation_bad_window(client):
    assert client.get('/api/lab/correlation?window=abc').status_code == 400
    assert client.get('/api/lab/correlation?window=2').status_code == 400


def test_risk_return_items(client):
    d = _strict_json(client.get('/api/lab/risk-return'))
    kinds = {i['id']: i['tipo'] for i in d['items']}
    assert kinds['PORT'] == 'cartera' and kinds['SPY'] == 'indice' and kinds['JPM'] == 'posicion'
    jpm = next(i for i in d['items'] if i['id'] == 'JPM')
    assert jpm['vol_pct'] is None  # menos de 20 dias: no se anualiza (None, no 0)
    assert sum(i['peso_pct'] for i in d['items'] if i['tipo'] == 'posicion') == pytest.approx(100, abs=0.1)


def test_no_prices_returns_clear_status_not_500(monkeypatch):
    monkeypatch.setattr(api, '_load_inputs', lambda: ([], {}, OPS))
    app = Flask(__name__)
    app.register_blueprint(api.lab_bp)
    c = app.test_client()
    for url in ('/api/lab/performance', '/api/lab/correlation', '/api/lab/risk-return'):
        r = c.get(url)
        assert r.status_code == 200 and r.get_json()['status'] == 'sin_precios' and r.get_json()['ok'] is False
