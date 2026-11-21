"""
Configuración compartida de la suite.

Reglas que garantiza este archivo:
1. Ningún test sin marca `live` puede salir a la red (yfinance, CoinGecko, Gemma).
   Si lo intenta, falla con un mensaje claro en vez de quedar colgado o gastar API.
2. Ningún test escribe en `reports/`, `data/financials/`, `data/risk/` ni `data/narrative/` reales: apuntan a carpetas temporales.
3. Gemma queda deshabilitada (sin API key) salvo en tests `live`.
"""
import os
import socket
import sys

import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for p in (PROJECT_DIR, TESTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

_real_connect = socket.socket.connect


class NetworkBlocked(RuntimeError):
    pass


def _blocked_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host in ('127.0.0.1', 'localhost', '::1'):
        return _real_connect(self, address)
    raise NetworkBlocked(
        f"Test offline intentó conectarse a {address}. "
        "Usá el fixture fake_yf o marcá el test con @pytest.mark.live."
    )


@pytest.fixture(autouse=True)
def _offline_guard(request, monkeypatch):
    if request.node.get_closest_marker('live'):
        yield
        return
    monkeypatch.setattr(socket.socket, 'connect', _blocked_connect)
    yield


@pytest.fixture(scope='session')
def server_module():
    import server
    return server


@pytest.fixture(autouse=True)
def _isolate_server_state(request, monkeypatch, tmp_path):
    """Aísla disco, cachés y IA en cada test offline (los e2e preparan su propio entorno)."""
    if request.node.get_closest_marker('live') or request.node.get_closest_marker('e2e'):
        yield
        return
    import server
    import financials
    monkeypatch.setattr(financials, 'CACHE_DIR', str(tmp_path / 'fin_cache'))
    import risk
    monkeypatch.setattr(risk, 'CACHE_DIR', str(tmp_path / 'risk_cache'))
    import narrative
    monkeypatch.setattr(narrative, 'CACHE_DIR', str(tmp_path / 'narr_cache'))
    import explore
    monkeypatch.setattr(explore, 'PREFS_PATH', str(tmp_path / 'explore_profile.json'))
    explore._screen_cache.clear()
    monkeypatch.setattr(server, 'NVIDIA_API_KEY', '')
    server.QUOTE_CACHE.clear()
    server.OHLCV_CACHE.clear()
    yield


@pytest.fixture
def client(server_module):
    server_module.app.config['TESTING'] = True
    return server_module.app.test_client()


@pytest.fixture
def fake_yf(monkeypatch, server_module):
    """
    Reemplaza yf.Ticker dentro de server por FakeTicker.
    Uso: registry = fake_yf; registry['JPM'] = FakeTicker('JPM', ...)
    Un símbolo no registrado recibe la empresa ficticia por defecto.
    registry.calls lista los símbolos consultados (para verificar caché e identidad).
    """
    from fakes import FakeTicker

    class Registry(dict):
        calls = None

    registry = Registry()
    registry.calls = []

    def _factory(symbol, *a, **k):
        registry.calls.append(symbol)
        return registry.get(symbol) or FakeTicker(symbol)

    monkeypatch.setattr(server_module.yf, 'Ticker', _factory)
    return registry


@pytest.fixture
def index_html():
    with open(os.path.join(PROJECT_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()
