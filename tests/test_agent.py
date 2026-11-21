"""
Portfolio Agent (agent.py + POST /api/chat).

Qué protege:
- Contexto completo: posición, técnico, finanzas, riesgo y valuación de cada ticker, con los
  mismos valores que las cachés (los esperados se recalculan desde la entrada).
- Sin red al armar el contexto.
- Tablas armadas por el servidor; una tabla escrita por el modelo nunca llega a la UI.
- Cifras del texto verificadas; precio objetivo u órdenes de compra/venta se omiten.
"""
import json

import pytest

import agent
import server as _server
import risk
from fakes import FakeTicker, STOCK_INFO, daily_history


REAL_CHAT_CALL = _server._chat_call


def strict(resp, status=200):
    def bad(c):
        raise ValueError(f'Constante JSON inválida: {c}')
    assert resp.status_code == status, resp.get_data(as_text=True)
    return json.loads(resp.get_data(as_text=True), parse_constant=bad)


POSITIONS = {
    'ACME': {'shares': 12.5, 'costBasis': 1834.75},
    'CHEAP': {'shares': 40.25, 'costBasis': 3.1},          # inversión recuperada (< 4)
    'SOL': {'shares': 0.0153, 'costBasis': 812.4},
    'USD_CASH': {'currentValue': 2345.67},
}


def cheap_ticker():
    info = {**STOCK_INFO, 'longName': 'Cheapo Industries', 'trailingPE': 9.4, 'forwardPE': 7.9,
            'priceToSalesTrailing12Months': 1.2, 'marketCap': 3.1e11}
    return FakeTicker('CHEAP', info=info, daily=daily_history(start_price=41.37))


@pytest.fixture
def caches(client, fake_yf):
    fake_yf['CHEAP'] = cheap_ticker()
    for t in ('ACME', 'CHEAP', 'SOL'):
        assert client.get(f'/api/risk/{t}').status_code == 200
    fake_yf.calls.clear()
    return ['ACME', 'CHEAP', 'SOL']


@pytest.fixture
def rows(caches):
    return agent.build_rows(caches, {'ACME': {'category': 'portfolio'}, 'CHEAP': {'category': 'watching'}},
                            POSITIONS, {'ACME': {'price': 131.27, 'chg_pct': -1.37, 'ts': 1794070000.0}})


@pytest.fixture
def model(monkeypatch, server_module):
    """Reemplaza a Gemma: responde lo que el test indique y guarda los mensajes recibidos."""
    state = {'reply': 'ok', 'messages': None}

    def call(messages):
        state['messages'] = messages
        return state['reply']
    monkeypatch.setattr(server_module, 'NVIDIA_API_KEY', 'test-key')
    monkeypatch.setattr(server_module, '_chat_call', call)
    monkeypatch.setattr(server_module, 'load_portfolio_from_csv', lambda: dict(POSITIONS))
    return state


def chat(client, msg, tickers=('ACME', 'CHEAP', 'SOL'), lang='es'):
    return strict(client.post('/api/chat', json={'message': msg, 'history': [], 'lang': lang,
                                                 'tickers': list(tickers)}))


# ── Contexto ────────────────────────────────────────────────────────────────

def test_row_has_position_technical_financial_and_risk(rows):
    r, rep = rows['ACME'], risk.read_cache('ACME')
    assert r['price'] == 131.27 and r['chg_day_pct'] == -1.37, 'la cotización en memoria gana si existe'
    assert r['value'] == pytest.approx(12.5 * 131.27, abs=0.01)
    assert r['pnl'] == pytest.approx(12.5 * 131.27 - 1834.75, abs=0.01)
    assert r['pnl_pct'] == pytest.approx((12.5 * 131.27 - 1834.75) / 1834.75 * 100, abs=0.01)
    assert r['risk_score'] == rep['risk_score'] and r['valuation'] == rep['valuation_label']
    pe = next(m['value'] for m in rep['pillars'][0]['metrics'] if m['key'] == 'pe')
    assert r['pe'] == pytest.approx(pe, abs=0.01), 'P/E al precio del día, igual que el Reporte de Riesgo'
    assert r['rsi'] == rep['market']['rsi14'] and r['ema20'] == pytest.approx(rep['market']['ema20'], abs=1e-4)
    assert r['bb_upper'] > r['bb_lower']
    assert r['net_margin'] is not None and r['gross_margin'] is not None and r['revenue_ttm'] > 0
    assert r['last_quarter'] and r['next_earnings']


def test_ema20_matches_chart_formula(rows):
    closes = [float(x) for x in daily_history(dividends=(40, 103, 166, 229))['Close']]
    import server
    assert rows['ACME']['ema20'] == pytest.approx(server._compute_ema(closes, 20)[-1], abs=1e-4)


def test_recovered_position_has_no_pnl_pct(rows):
    assert rows['CHEAP']['recovered'] is True and rows['CHEAP']['pnl_pct'] is None


def test_crypto_row_uses_usd_symbol_data(rows):
    r = rows['SOL']
    assert r['kind'] == 'crypto' and r['price'] == risk.read_cache('SOL')['market']['price']
    assert r['valuation'] is None and r['pe'] is None


def test_weights_add_up(rows):
    assert sum(r['weight_pct'] for r in rows.values() if r['weight_pct']) == pytest.approx(100, abs=0.05)


def test_rows_without_cache_are_null_and_offline(fake_yf):
    rows = agent.build_rows(['NOPE'], {}, {}, {})
    assert rows['NOPE']['price'] is None and rows['NOPE']['risk_score'] is None
    assert fake_yf.calls == []


def test_focus_by_symbol_or_name(rows):
    assert agent.focus_tickers('¿Cómo viene acme comparada con Cheapo?', [], rows) == ['ACME', 'CHEAP']
    hist = [{'role': 'user', 'content': 'Hablame de SOL'}]
    assert agent.focus_tickers('¿y su volatilidad?', hist, rows) == ['SOL']


def test_context_respects_budget(caches):
    many = [f'T{i:03d}' for i in range(80)] + caches
    rows = agent.build_rows(many, {}, POSITIONS, {})
    ctx = agent.build_context(rows, many, agent.totals(rows, POSITIONS))
    assert len(ctx) <= agent.MAX_CONTEXT_CHARS + 2000
    assert ctx.count('### ') <= agent.MAX_FOCUS


def test_prompt_sent_to_model_has_all_areas(client, caches, model):
    chat(client, 'Analizá ACME')
    prompt = model['messages'][0]['content']
    assert 'ACME|Acme Corp|' in prompt and 'risk pillar valuation' in prompt
    assert 'quarter ' in prompt and 'rsi=' in prompt and 'cash_value=2345.67' in prompt
    assert '[[TABLE' in prompt, 'el modelo sabe pedir tablas en vez de escribirlas'


# ── Tablas ──────────────────────────────────────────────────────────────────

def test_table_is_built_from_data(client, caches, model):
    model['reply'] = ('Acá va la comparación.\n'
                      '[[TABLE cols=pe,risk_score,valuation,inventada tickers=ACME,CHEAP,ZZZZ sort=-pe title="Comparación"]]')
    out = chat(client, 'Haceme una tabla')
    assert '[[' not in out['reply'] and out['reply'] == 'Acá va la comparación.'
    t = out['tables'][0]
    assert [c['key'] for c in t['columns']] == ['ticker', 'pe', 'risk_score', 'valuation']
    by = {r['ticker']: r for r in t['rows']}
    for sym in ('ACME', 'CHEAP'):
        rep = risk.read_cache(sym)
        assert by[sym]['risk_score'] == rep['risk_score'] and by[sym]['valuation'] == rep['valuation_label']
    assert by['ZZZZ']['pe'] is None, 'ticker sin datos: celda vacía, nunca un número'
    assert [r['ticker'] for r in t['rows']][:2] == sorted(['ACME', 'CHEAP'], key=lambda s: -by[s]['pe'])


def test_table_filters(rows):
    t = agent.build_table('cols=valuation,pe where=valuation:cheap', rows, 'es')
    assert [r['ticker'] for r in t['rows']] == [s for s, r in rows.items() if r['valuation'] == 'cheap']
    assert 'CHEAP' in [r['ticker'] for r in t['rows']] and 'SOL' not in [r['ticker'] for r in t['rows']]
    t = agent.build_table('cols=pe where=pe<10', rows, 'es')
    assert [r['ticker'] for r in t['rows']] == [s for s, r in rows.items() if r['pe'] is not None and r['pe'] < 10]
    t = agent.build_table('cols=pe tickers=portfolio', rows, 'en')
    assert [r['ticker'] for r in t['rows']] == ['ACME'] and t['columns'][1]['label'] == 'P/E'
    assert agent.build_table('cols=nada', rows, 'es') is None


def test_model_written_table_never_reaches_ui(client, caches, model):
    model['reply'] = ('Resumen:\n| Ticker | P/E |\n|---|---|\n| ACME | 12.3 |\n| CHEAP | 4.1 |\nEso es todo.')
    out = chat(client, 'tabla')
    assert '|' not in out['reply'] and '12.3' not in out['reply'] and out['tables'] == []
    assert out['omitted']['model_table'] == 1


# ── Verificación de cifras ──────────────────────────────────────────────────

def test_invented_numbers_and_targets_are_omitted(client, caches, model, rows):
    pe = rows['ACME']['pe']
    model['reply'] = (f'El P/E de ACME es {pe:.1f}. Creo que puede subir 37.4% este año.\n'
                      '- Su precio objetivo es alto.\n- Te recomiendo vender ya.\n- Tiene riesgo bajo.')
    out = chat(client, 'ACME')
    assert f'{pe:.1f}' in out['reply'] and '37.4' not in out['reply']
    assert 'objetivo' not in out['reply'] and 'vender' not in out['reply']
    assert '- Tiene riesgo bajo.' in out['reply']
    assert out['omitted']['ungrounded'] == 1 and out['omitted']['forbidden'] == 2


def test_agent_context_is_isolated_from_real_data(client, caches, model, monkeypatch, tmp_path):
    """El contexto del agente no depende de data/ reales ni del CSV: ningún número permitido cae
    cerca de 37.4 (el 'inventado' del test anterior) y las rutas apuntan a tmp_path."""
    import os
    import explore
    import financials
    import narrative
    real_data = os.path.join(os.path.dirname(os.path.abspath(agent.__file__)), 'data')
    for path in (financials.CACHE_DIR, risk.CACHE_DIR, narrative.CACHE_DIR, explore.PREFS_PATH):
        assert not os.path.abspath(path).startswith(real_data), path
        assert os.path.abspath(path).startswith(str(tmp_path)), path
    seen = {}
    original = agent.process_reply

    def spy(raw, rows_, allowed, lang):
        seen['allowed'] = list(allowed)
        return original(raw, rows_, allowed, lang)
    monkeypatch.setattr(agent, 'process_reply', spy)
    model['reply'] = 'Sube 37.4% este año.'
    chat(client, 'ACME')
    assert seen['allowed']
    near = [a for a in seen['allowed'] if abs(37.4 - a) <= max(0.051, 0.015 * abs(a))]
    assert near == [], near


def test_numbers_from_the_question_are_allowed(client, caches, model):
    model['reply'] = 'Si invertís 1234.5 dólares, eso no está en los datos del dashboard.'
    out = chat(client, '¿Y si invierto 1234.5 dólares?')
    assert '1234.5' in out['reply']


def test_nothing_verifiable_gives_honest_message(client, caches, model):
    model['reply'] = 'Va a valer 999.99 en marzo.'
    out = chat(client, '¿cuánto va a valer?')
    assert out['reply'] == agent.NO_ANSWER['es'] and out['omitted']['ungrounded'] == 1


def test_no_api_key(client, monkeypatch, server_module):
    monkeypatch.setattr(server_module, 'NVIDIA_API_KEY', '')
    assert client.post('/api/chat', json={'message': 'hola'}).status_code == 503


def test_model_failure(client, caches, model, monkeypatch, server_module):
    monkeypatch.setattr(server_module, '_chat_call', lambda m: None)
    assert client.post('/api/chat', json={'message': 'hola', 'tickers': ['ACME']}).status_code == 502


def test_chat_is_offline(client, caches, model, fake_yf):
    chat(client, 'Resumí mi portfolio')
    assert fake_yf.calls == []


def test_upstream_error_reason_reaches_the_ui(client, caches, model, monkeypatch, server_module):
    """Un 502 trae el motivo real de NVIDIA (estado y mensaje) para poder diagnosticarlo desde el chat."""
    class Resp:
        status_code = 400
        text = '{"error":"prompt too long for model"}'
    monkeypatch.setattr(server_module, '_chat_call', REAL_CHAT_CALL)
    monkeypatch.setattr(server_module.http_requests, 'post', lambda *a, **k: Resp())
    r = client.post('/api/chat', json={'message': 'hola', 'tickers': ['ACME']})
    assert r.status_code == 502 and r.get_json()['detail'] == 'NVIDIA 400: {"error":"prompt too long for model"}'


class _StreamResp:
    status_code = 200
    text = ''

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        for ln in self._lines:
            if isinstance(ln, Exception):
                raise ln
            yield ln

    def close(self):
        pass


def test_gemma_is_called_in_streaming_and_text_is_joined(monkeypatch, server_module):
    """Con stream=False NVIDIA no manda nada hasta terminar y se superaba el timeout de 120 s.
    En streaming el límite aplica entre fragmentos y el texto se arma con los deltas."""
    seen = {}

    def post(url, headers=None, json=None, stream=False, timeout=None):
        seen.update(stream=stream, payload_stream=json['stream'], timeout=timeout)
        return _StreamResp(['', ': keep-alive', 'data: {"choices":[{"delta":{"role":"assistant"}}]}',
                            'data: {"choices":[{"delta":{"content":"El riesgo de "}}]}',
                            'data: {"choices":[{"delta":{"content":"ACME es moderado."}}]}',
                            'data: [DONE]', 'data: {"choices":[{"delta":{"content":"ignorado"}}]}'])
    monkeypatch.setattr(server_module, 'NVIDIA_API_KEY', 'k')
    monkeypatch.setattr(server_module.http_requests, 'post', post)
    assert REAL_CHAT_CALL([{'role': 'user', 'content': 'hola'}]) == 'El riesgo de ACME es moderado.'
    assert seen['stream'] is True and seen['payload_stream'] is True
    assert seen['timeout'] == (server_module.CHAT_CONNECT_TIMEOUT, server_module.CHAT_IDLE_TIMEOUT)


def test_stream_stall_reports_timeout(monkeypatch, server_module):
    import requests
    monkeypatch.setattr(server_module.http_requests, 'post', lambda *a, **k: _StreamResp(
        ['data: {"choices":[{"delta":{"content":"Hola"}}]}', requests.exceptions.ReadTimeout('Read timed out.')]))
    assert REAL_CHAT_CALL([{'role': 'user', 'content': 'hola'}]) is None
    assert server_module.CHAT_LAST_ERROR['detail'].startswith('ReadTimeout')


def test_compact_context_stays_small(caches):
    """Sin activos nombrados, el contexto de todo el portfolio entra en pocos miles de caracteres."""
    many = caches + [f'T{i:02d}' for i in range(40)]
    rows = agent.build_rows(many, {}, POSITIONS, {})
    prompt = agent.build_prompt(agent.build_context(rows, [], agent.totals(rows, POSITIONS)), 'es')
    assert len(prompt) < 9000, len(prompt)


def test_stream_keeps_accents_utf8(monkeypatch, server_module):
    """El stream llega sin charset y requests lo trataba como ISO-8859-1: "valoración" salía "valoraciÃ³n"."""
    class Latin1Stream(_StreamResp):
        encoding = 'ISO-8859-1'

        def iter_lines(self, decode_unicode=False):
            for ln in self._lines:
                b = ln.encode('utf-8')
                yield b.decode('latin-1') if decode_unicode else b
    body = 'data: {"choices":[{"delta":{"content":"La valoración de ACME está en línea, ¿no?"}}]}'
    monkeypatch.setattr(server_module.http_requests, 'post', lambda *a, **k: Latin1Stream([body, 'data: [DONE]']))
    assert REAL_CHAT_CALL([{'role': 'user', 'content': 'hola'}]) == 'La valoración de ACME está en línea, ¿no?'


def test_retries_once_when_nvidia_sends_nothing(monkeypatch, server_module):
    """Cola saturada de NVIDIA: el primer intento no manda nada y se reintenta una vez."""
    import requests
    calls = []

    def post(*a, **k):
        calls.append(k['timeout'])
        if len(calls) == 1:
            return _StreamResp([requests.exceptions.ReadTimeout('Read timed out. (read timeout=150)')])
        return _StreamResp(['data: {"choices":[{"delta":{"content":"Listo."}}]}', 'data: [DONE]'])
    monkeypatch.setattr(server_module.time, 'sleep', lambda s: None)
    monkeypatch.setattr(server_module.http_requests, 'post', post)
    assert REAL_CHAT_CALL([{'role': 'user', 'content': 'hola'}]) == 'Listo.'
    assert len(calls) == 2 and calls[0] == (server_module.CHAT_CONNECT_TIMEOUT, server_module.CHAT_IDLE_TIMEOUT)


def test_gives_up_after_attempts_and_reports(monkeypatch, server_module):
    import requests
    monkeypatch.setattr(server_module.time, 'sleep', lambda s: None)
    monkeypatch.setattr(server_module.http_requests, 'post',
                        lambda *a, **k: _StreamResp([requests.exceptions.ReadTimeout('Read timed out.')]))
    assert REAL_CHAT_CALL([{'role': 'user', 'content': 'hola'}]) is None
    assert server_module.CHAT_LAST_ERROR['detail'] == 'ReadTimeout: Read timed out. · 2 intentos'


def test_rate_limit_is_retried_but_bad_request_is_not(monkeypatch, server_module):
    seq = []

    class R:
        def __init__(self, code):
            self.status_code, self.text = code, f'{{"status":{code}}}'
    monkeypatch.setattr(server_module.time, 'sleep', lambda s: None)
    monkeypatch.setattr(server_module.http_requests, 'post', lambda *a, **k: seq.append(1) or R(429))
    assert REAL_CHAT_CALL([{'role': 'user', 'content': 'x'}]) is None and len(seq) == 2
    seq.clear()
    monkeypatch.setattr(server_module.http_requests, 'post', lambda *a, **k: seq.append(1) or R(400))
    assert REAL_CHAT_CALL([{'role': 'user', 'content': 'x'}]) is None and len(seq) == 1
