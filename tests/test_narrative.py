"""
Narrativa IA (narrative.py, fase R2): la IA solo redacta y un validador descarta lo que no
cumple. Lo descartado o no generado queda en None ("No disponible en el modelo actual").

Los textos de prueba imitan errores reales de los reportes viejos de Gemma:
"Gross Margin 67.8% · Industry High", "Target price $420", "peaking at $6.86B".
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import financials as fin
import narrative as nar
import risk


def consistent_daily():
    """Precios diarios cuyo último cierre coincide con currentPrice de la caché contable (200):
    así el P/E revaluado al día (risk.reprice_metrics) es el mismo 30.0x que publicó Yahoo."""
    from fakes import STOCK_INFO, daily_history
    raw = daily_history(dividends=(40, 103, 166, 229))
    return daily_history(start_price=100.0 * STOCK_INFO['currentPrice'] / float(raw['Close'].iloc[-1]),
                         dividends=(40, 103, 166, 229))


@pytest.fixture
def report(fake_yf):
    from fakes import FakeTicker
    fake_yf['ACME'] = FakeTicker('ACME', daily=consistent_daily())
    return risk.get_risk('ACME')


@pytest.fixture
def facts(report, fake_yf):
    return nar.build_facts(report, fin.get_financials('ACME'))


@pytest.fixture
def allowed(facts):
    return nar.extract_numbers(facts)


GOOD_ES = {
    'update': 'En el Q3 2026 los ingresos fueron de 110B USD, un 7.8% más que un año antes, '
              'y el BPA ajustado de 1.95 superó la estimación de 1.85.',
    'catalysts': ['El margen neto se mantiene en torno al 25%.', 'La volatilidad anual es baja, de 10.8%.'],
    'risks': ['La deuda equivale a 1.5 veces el patrimonio.', 'La valuación es exigente con un P/E de 30.'],
    'bottom_line': 'El riesgo total es moderado, de 37 sobre 100, con una valuación justa.',
}


def model_returning(payload):
    return lambda prompt: payload if isinstance(payload, str) else json.dumps(payload)


# ── Extracción de números ────────────────────────────────────────────────────

@pytest.mark.parametrize('tok,val', [('25,1', 25.1), ('1.234,5', 1234.5), ('1,234.5', 1234.5), ('-3.4', -3.4),
                                     ('110', 110.0), ('1.234.567', 1234567.0), ('−1,2', -1.2)])
def test_parse_number(tok, val):
    assert nar.parse_number(tok) == val


def test_numbers_with_units_are_extracted():
    """'$6.86B' o '1.5x' tienen que verificarse: antes se escapaban del validador."""
    nums = nar.extract_numbers('tocó $6.86B, deuda 1.5x, media de 200d, COVID19')
    assert 6.86 in nums and 1.5 in nums and 200.0 in nums and 19.0 not in nums


def test_periods_and_dates_are_not_numbers_to_verify():
    nums = nar.extract_numbers('En el Q3 2026 y el FY26, el 2026-10-17 subió 5.5%')
    assert 5.5 in nums and 3.0 not in nums and 26.0 not in nums
    assert -10.0 not in nums and -17.0 not in nums


def test_grounding_tolerates_rounding(allowed):
    assert nar.is_grounded(25.1, allowed) and nar.is_grounded(10.8, allowed)   # 10.79 redondeado
    assert nar.is_grounded(110, allowed) and nar.is_grounded(3, allowed)
    assert not nar.is_grounded(67.8, allowed) and not nar.is_grounded(420, allowed)


# ── Validador ───────────────────────────────────────────────────────────────

def test_good_narrative_passes(allowed):
    fields, unavailable = nar.validate(GOOD_ES, allowed, 'es')
    assert unavailable == {}, unavailable
    assert fields['risks'] == GOOD_ES['risks']


@pytest.mark.parametrize('text,reason', [
    ('El margen bruto de 67.8% es el más alto de la industria.', 'ungrounded_numbers'),
    ('Los ingresos tocaron un máximo de $6.86B en el trimestre.', 'ungrounded_numbers'),
    ('Con un precio objetivo de 140 la acción tiene margen.', 'forbidden_content'),
    ('Recomendamos comprar en baja con un riesgo moderado.', 'forbidden_content'),
    ('Strong fundamentals justify a buy rating for the stock.', 'forbidden_content'),
    ('The company grew revenue in the quarter and the margin is stable.', 'wrong_language'),
    ('N/A', 'empty'),
    ('x' * 800, 'too_long'),
])
def test_bad_bottom_line_rejected(allowed, text, reason):
    fields, unavailable = nar.validate({**GOOD_ES, 'bottom_line': text}, allowed, 'es')
    assert fields['bottom_line'] is None and unavailable['bottom_line'] == reason
    assert fields['update'] == GOOD_ES['update'], 'Un campo malo no arrastra a los demás'


def test_sell_off_is_not_a_recommendation(allowed):
    txt, why = nar.check_text('The sell-off left the price below its 200-day average.', allowed, 'en', 500)
    assert why is None


def test_one_bad_item_discards_the_list(allowed):
    raw = {**GOOD_ES, 'catalysts': ['El margen neto ronda el 25%.', 'Las ventas crecerían 40% el año próximo.']}
    fields, unavailable = nar.validate(raw, allowed, 'es')
    assert fields['catalysts'] is None and unavailable['catalysts'] == 'ungrounded_numbers'


def test_lists_capped_at_three(allowed):
    raw = {**GOOD_ES, 'risks': GOOD_ES['risks'] + ['El RSI es de 62.1.', 'El beta es 1.1.']}
    assert len(nar.validate(raw, allowed, 'es')[0]['risks']) == 3


@pytest.mark.parametrize('raw', [None, [], 'texto', {'update': 5, 'catalysts': 'uno'}])
def test_malformed_model_output(allowed, raw):
    fields, unavailable = nar.validate(raw, allowed, 'es')
    assert all(v is None for v in fields.values()) and set(unavailable) == set(nar.FIELDS)


# ── Generación ──────────────────────────────────────────────────────────────

def test_prompt_contains_facts_and_rules(report, facts):
    p = nar.build_prompt(facts, 'es')
    assert facts in p and 'Spanish' in p
    assert 'price target' in p and 'higher = more risk' in p


def test_facts_come_from_computed_data(report, facts):
    assert f"RISK SCORE: {report['risk_score']}/100" in facts
    assert 'pe=30.0x' in facts and 'QUARTER Q3 2026' in facts
    assert 'NEXT EARNINGS' in facts


def test_generate_with_good_model(report):
    fp = fin.get_financials('ACME')
    out = nar.generate(report, fp, 'es', model_returning('```json\n' + json.dumps(GOOD_ES) + '\n```'), 'gemma')
    assert out['unavailable'] == {} and out['inputs_hash'] == report['inputs_hash']


@pytest.mark.parametrize('model,reason', [
    (None, 'no_api_key'),
    (lambda p: None, 'model_error'),
    (lambda p: (_ for _ in ()).throw(TimeoutError()), 'model_error'),
    (lambda p: 'no es json', 'invalid_json'),
])
def test_model_failures_never_produce_filler(report, model, reason):
    out = nar.generate(report, {}, 'es', model)
    assert all(v is None for v in out['fields'].values())
    assert set(out['unavailable'].values()) == {reason}


# ── Caché y endpoint ─────────────────────────────────────────────────────────

class SyncExecutor:
    def __init__(self):
        self.jobs = 0

    def submit(self, fn, *a, **k):
        self.jobs += 1
        fn(*a, **k)


@pytest.fixture
def sync_exec(monkeypatch):
    ex = SyncExecutor()
    monkeypatch.setattr(nar, 'EXECUTOR', ex)
    return ex


@pytest.fixture
def gemma(monkeypatch, server_module):
    calls = []

    def fake(prompt, **kw):
        calls.append(prompt)
        return json.dumps(GOOD_ES)
    monkeypatch.setattr(server_module, 'NVIDIA_API_KEY', 'test-key')
    monkeypatch.setattr(server_module, '_call_gemma', fake)
    return calls


def test_endpoint_generates_then_serves_cache(client, fake_yf, sync_exec, gemma):
    first = client.get('/api/risk/ACME/narrative?lang=es').get_json()
    assert first['status'] == 'generating' and len(gemma) == 1
    second = client.get('/api/risk/ACME/narrative?lang=es').get_json()
    assert second['status'] == 'ready' and second['fields']['update'] == GOOD_ES['update']
    client.get('/api/risk/ACME/narrative?lang=es')
    assert len(gemma) == 1, 'Con los mismos datos no se vuelve a llamar al modelo'


def test_regenerates_when_inputs_change(client, fake_yf, sync_exec, gemma):
    client.get('/api/risk/ACME/narrative?lang=es')
    cached = nar.read_cache('ACME', 'es')
    nar.write_cache('ACME', 'es', {**cached, 'inputs_hash': 'otro'})
    r = client.get('/api/risk/ACME/narrative?lang=es').get_json()
    assert r['status'] == 'generating' and r['previous']['inputs_hash'] == 'otro'
    assert len(gemma) == 2


def test_regenerates_after_30_days(report, fake_yf):
    old = {'generated_at': (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
           'inputs_hash': report['inputs_hash']}
    assert nar.is_current(old, report['inputs_hash']) is False
    assert nar.is_current({**old, 'generated_at': datetime.now(timezone.utc).isoformat()}, report['inputs_hash'])


def test_languages_cached_separately(client, fake_yf, sync_exec, gemma):
    client.get('/api/risk/ACME/narrative?lang=es')
    client.get('/api/risk/ACME/narrative?lang=en')
    assert len(gemma) == 2 and 'English' in gemma[1]


def test_without_api_key_no_http_and_marked_unavailable(client, fake_yf, sync_exec, server_module, monkeypatch):
    monkeypatch.setattr(server_module.http_requests, 'post', lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    client.get('/api/risk/ACME/narrative?lang=es')
    r = client.get('/api/risk/ACME/narrative?lang=es').get_json()
    assert r['status'] == 'ready' and set(r['unavailable'].values()) == {'no_api_key'}


def test_single_generation_in_flight(client, fake_yf, monkeypatch, gemma):
    jobs = []
    monkeypatch.setattr(nar, 'EXECUTOR', type('E', (), {'submit': lambda self, fn: jobs.append(fn)})())
    for _ in range(4):
        assert client.get('/api/risk/ACME/narrative?lang=es').get_json()['status'] == 'generating'
    assert len(jobs) == 1, 'Pedidos repetidos mientras genera: una sola llamada al modelo'
    jobs[0]()
    assert client.get('/api/risk/ACME/narrative?lang=es').get_json()['status'] == 'ready'


def test_invalid_lang_falls_back_to_spanish(client, fake_yf, sync_exec, gemma):
    client.get('/api/risk/ACME/narrative?lang=xx')
    assert 'Spanish' in gemma[0]
