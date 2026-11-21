"""
Alucinaciones: la IA (Gemma) puede redactar texto, pero NUNCA debe ser la fuente
de un número que se muestra como dato. Estos tests simulan respuestas de IA
maliciosas o erróneas y verifican qué llega a la UI.

(R4, 2026-11-07: se retiró el generador de reportes viejo, `_build_report` y compañía,
junto con sus xfail. La narrativa del riesgo se protege en test_narrative.py, el chat
en test_agent.py y el modo Explorar en test_explore.py.)
"""
import pytest


# ── Parseo robusto de la respuesta de la IA ─────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('{"a": 1}', {'a': 1}),
    ('<think>razono {no json}</think>{"a": 2}', {'a': 2}),
    ('Texto previo\n```json\n{"a": 3}\n```\nTexto posterior', {'a': 3}),
    ('```\n{"a": {"b": 4}}\n```', {'a': {'b': 4}}),
    ('Respuesta: {"a": 5} fin', {'a': 5}),
])
def test_extract_json_valid(server_module, raw, expected):
    assert server_module._extract_json(raw) == expected


@pytest.mark.parametrize('raw', ['', None, 'sin json', '{roto', '```json\n{"a": }\n```'])
def test_extract_json_invalid_returns_none(server_module, raw):
    assert server_module._extract_json(raw) is None


# ── Portfolio Agent (chat) ───────────────────────────────────────────────────

def test_chat_model_cannot_impose_table_numbers_or_invented_figures():
    """Gemma escribe una tabla propia y una cifra sin respaldo: ninguna llega a la UI."""
    import agent
    rows = {'AAA': {'ticker': 'AAA', 'pe': 21.37, 'risk_score': 42, 'category': 'portfolio'}}
    allowed = [21.37, 42.0]
    raw = ('AAA tiene un P/E de 21.37.\n| Ticker | P/E |\n|---|---|\n| AAA | 8.5 |\n'
           'Su BPA crecerá 64.2% el año próximo.\n[[TABLE cols=pe,risk_score tickers=AAA]]')
    out = agent.process_reply(raw, rows, allowed, 'es')
    assert out['reply'] == 'AAA tiene un P/E de 21.37.'
    assert out['tables'][0]['rows'] == [{'ticker': 'AAA', 'pe': 21.37, 'risk_score': 42}]
    assert out['omitted'] == {'ungrounded': 1, 'forbidden': 0, 'model_table': 1, 'unknown_ticker': 0}
