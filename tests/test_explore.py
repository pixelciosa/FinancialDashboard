"""
Modo Explorar (explore.py + /api/explore + /api/chat con [[EXPLORE ...]]).

Qué protege:
- La IA no elige tickers ni números: el pedido se valida contra una lista cerrada y los
  candidatos salen del screener; los números, del mismo cálculo que el resto del dashboard.
- Filtros del sondeo real: sin OTC, sin acciones de prueba, sin duplicados, sin tickers propios,
  orden por capitalización (Yahoo devuelve Z→A si no se ordena).
- La calificación de analistas nunca llega a la UI ni al prompt.
- Preferencias guardadas y validadas.
"""
import json

import pytest

import agent
import explore
import financials as fin
import risk
from fakes import ETF_INFO, FakeTicker, STOCK_INFO, daily_history


class FakeEQ:
    def __init__(self, op, operands):
        self.op, self.operands = op, operands

    def as_tuple(self):
        return (self.op, [o.as_tuple() if isinstance(o, FakeEQ) else o for o in self.operands])


QUOTES = [
    {'symbol': 'ZZZTX', 'quoteType': 'EQUITY', 'exchange': 'TEX', 'shortName': 'TXSE Test Stk 5'},
    {'symbol': 'HLTHF', 'quoteType': 'EQUITY', 'exchange': 'PNK', 'longName': 'Health Co OTC'},
    {'symbol': 'HLTH', 'quoteType': 'EQUITY', 'exchange': 'NYQ', 'longName': 'Health Co',
     'trailingPE': 5.0, 'averageAnalystRating': '1.0 - Strong Buy'},
    {'symbol': 'HLTH2', 'quoteType': 'EQUITY', 'exchange': 'NMS', 'longName': 'Health Co'},     # misma empresa
    {'symbol': 'ACME', 'quoteType': 'EQUITY', 'exchange': 'NMS', 'longName': 'Acme Corp'},      # ya está
    {'symbol': 'UTIL.TO', 'quoteType': 'EQUITY', 'exchange': 'NYQ', 'longName': 'Util Power CDR'},
    {'symbol': 'FUND', 'quoteType': 'MUTUALFUND', 'exchange': 'NAS', 'longName': 'Some Fund'},
    {'symbol': 'UTIL', 'quoteType': 'EQUITY', 'exchange': 'NYQ', 'longName': 'Util Power',
     'dividendYield': 4.2, 'averageAnalystRating': '2.0 - Buy'},
    {'symbol': 'RISKY', 'quoteType': 'EQUITY', 'exchange': 'NMS', 'longName': 'Risky Bio'},
]


class FakeScreen:
    def __init__(self, quotes=QUOTES, fail=False):
        self.quotes, self.fail, self.calls = quotes, fail, []

    def __call__(self, query, **kw):
        self.calls.append((query.as_tuple() if isinstance(query, FakeEQ) else query, kw))
        if self.fail:
            raise RuntimeError('Yahoo screener caído')
        return {'quotes': [dict(q) for q in self.quotes], 'total': len(self.quotes)}


def consistent_daily(price=200.0, **kw):
    """Último cierre = currentPrice: la revaluación al día no cambia los números de la caché."""
    raw = daily_history(**kw)
    return daily_history(start_price=100.0 * price / float(raw['Close'].iloc[-1]), **kw)


@pytest.fixture
def screen(monkeypatch, fake_yf):
    s = FakeScreen()
    monkeypatch.setattr(explore.yf, 'EquityQuery', FakeEQ, raising=False)
    monkeypatch.setattr(explore.yf, 'screen', s, raising=False)
    fake_yf['HLTH'] = FakeTicker('HLTH', info={**STOCK_INFO, 'longName': 'Health Co', 'sector': 'Healthcare',
                                               'industry': 'Drug Manufacturers', 'trailingPE': 9.8, 'forwardPE': 8.1,
                                               'priceToSalesTrailing12Months': 1.4, 'marketCap': 2.4e11,
                                               'beta': 0.71}, daily=consistent_daily())
    fake_yf['UTIL'] = FakeTicker('UTIL', info={**STOCK_INFO, 'longName': 'Util Power', 'sector': 'Utilities',
                                               'industry': 'Utilities - Regulated Electric', 'dividendRate': 8.4,
                                               'beta': 0.42, 'marketCap': 8.7e10}, daily=consistent_daily())
    fake_yf['RISKY'] = FakeTicker('RISKY', info={**STOCK_INFO, 'longName': 'Risky Bio', 'sector': 'Healthcare',
                                                 'industry': 'Biotechnology', 'trailingPE': None, 'forwardPE': -12.0,
                                                 'beta': 2.6, 'debtToEquity': 410.0, 'currentRatio': 0.6},
                                  daily=daily_history(wiggle=0.09, drift=-0.004))
    fake_yf['ACME'] = FakeTicker('ACME', daily=consistent_daily())
    return s


POSITIONS = {'ACME': {'shares': 10.0, 'costBasis': 1500.0}, 'SOL': {'shares': 0.02, 'costBasis': 900.0}}


@pytest.fixture
def portfolio(client, fake_yf, monkeypatch, server_module):
    fake_yf.setdefault('ACME', FakeTicker('ACME', daily=consistent_daily()))
    for t in POSITIONS:
        client.get(f'/api/risk/{t}')
    monkeypatch.setattr(server_module, 'load_portfolio_from_csv', lambda: dict(POSITIONS))
    return agent.build_rows(list(POSITIONS), {}, POSITIONS, {})


def strict(resp, status=200):
    def bad(c):
        raise ValueError(f'Constante JSON inválida: {c}')
    assert resp.status_code == status, resp.get_data(as_text=True)
    return json.loads(resp.get_data(as_text=True), parse_constant=bad)


# ── Perfil ──────────────────────────────────────────────────────────────────

def test_profile_from_positions(portfolio):
    prof = explore.profile(portfolio)
    acme, sol = portfolio['ACME']['value'], portfolio['SOL']['value']
    total = acme + sol
    assert prof['sector_weights'] == {'Technology': round(acme / total * 100, 2)}
    assert prof['crypto_pct'] == round(sol / total * 100, 2)
    assert 'Healthcare' in prof['missing_sectors'] and 'Technology' not in prof['missing_sectors']
    exp_risk = round((acme * portfolio['ACME']['risk_score'] + sol * portfolio['SOL']['risk_score']) / total, 2)
    assert prof['avg_risk'] == exp_risk


def test_profile_uses_etf_sector_weights(fake_yf, client):
    fake_yf['FETF'] = FakeTicker('FETF', info=dict(ETF_INFO))
    client.get('/api/risk/FETF')
    rows = agent.build_rows(['FETF'], {}, {'FETF': {'shares': 3.0, 'costBasis': 900.0}}, {})
    prof = explore.profile(rows)
    weights = fin.read_cache('FETF')['payload']['sectors']
    mapped = {explore.ETF_SECTOR_KEYS[s['l']]: s['v'] for s in weights if s['l'] in explore.ETF_SECTOR_KEYS}
    tot = sum(mapped.values())
    for name, v in mapped.items():
        assert prof['sector_weights'][name] == pytest.approx(v / tot * 100, abs=0.02)


# ── Pedido del modelo: lista cerrada ────────────────────────────────────────

def test_spec_is_validated_and_clamped():
    prefs = explore.clean_prefs({'exclude_sectors': ['Energy']})
    params, rejected = explore.parse_spec(
        'intent=hackear sectors=Healthcare,Crypto,energy,real_estate max_beta=12 pe_max=abc min_mcap_b=0.01', prefs)
    assert params['intent'] == 'diversify'
    assert params['sectors'] == ['Healthcare', 'Real Estate'], 'Energy excluido por preferencia, Crypto no existe'
    assert params['max_beta'] == explore.BOUNDS['max_beta'][1] and params['min_mcap_b'] == explore.BOUNDS['min_mcap_b'][0]
    assert 'pe_max' not in params
    assert set(rejected) == {'intent=hackear', 'sector=Crypto', 'pe_max=abc'}


def test_intent_defaults():
    p, _ = explore.parse_spec('intent=dividends', explore.DEFAULT_PREFS)
    assert p['min_div'] == 3.0 and p['min_mcap_b'] == explore.DEFAULT_MIN_MCAP_B
    p, _ = explore.parse_spec('intent=value', explore.DEFAULT_PREFS)
    assert p['pe_max'] == 18.0


def test_query_uses_only_probed_fields_and_sorts_by_market_cap(screen):
    params, _ = explore.parse_spec('intent=dividends sectors=Utilities,Healthcare max_beta=1', explore.DEFAULT_PREFS)
    q = explore.build_query(params, params['sectors'])
    explore.run_screen(q)
    sent, kw = screen.calls[0]
    assert kw == {'size': explore.SCREEN_SIZE, 'sortField': 'intradaymarketcap', 'sortAsc': False}
    assert sent == ('and', [('eq', ['region', 'us']), ('gt', ['intradaymarketcap', 2e9]),
                            ('is-in', ['sector', 'Utilities', 'Healthcare']), ('gt', ['forward_dividend_yield', 3.0]),
                            ('lt', ['beta', 1.0])])
    explore.run_screen(q)
    assert len(screen.calls) == 1, 'la misma búsqueda se reutiliza 15 minutos'


def test_market_filters_from_probe():
    syms, dropped = explore.filter_quotes(QUOTES, exclude={'ACME'})
    assert syms == ['HLTH', 'UTIL', 'RISKY']
    assert dropped == {'exchange': 3, 'owned': 1, 'duplicate': 1, 'type': 1}


# ── Exploración completa ────────────────────────────────────────────────────

def test_explore_candidates_use_dashboard_numbers(client, screen, portfolio):
    out = strict(client.post('/api/explore', json={'intent': 'diversify', 'owned': list(POSITIONS)}))
    assert out['ok'] is True and len(screen.calls) == 1
    by = {c['ticker']: c for c in out['candidates']}
    assert set(by) == {'HLTH', 'UTIL'}, 'RISKY queda afuera por nivel de riesgo (preferencia: moderado)'
    assert out['skipped']['level'] == 1
    h, rep = by['HLTH'], risk.read_cache('HLTH')
    pe = next(m['value'] for m in rep['pillars'][0]['metrics'] if m['key'] == 'pe')
    assert h['pe'] == round(pe, 2) and h['pe'] != 5.0, 'P/E del cálculo propio, no del screener'
    assert h['risk_score'] == rep['risk_score'] and h['valuation'] == rep['valuation_label']
    assert h['sector'] == 'Healthcare'
    u = by['UTIL']
    assert u['dividend_yield'] == pytest.approx(8.4 / 200 * 100, abs=0.01), 'dividendo en %, desde tasa/precio'
    assert {'code': 'new_sector', 'sector': 'Healthcare'} in h['reasons']
    assert {'code': 'dividend', 'value': u['dividend_yield']} in u['reasons']
    assert {'code': 'low_beta', 'value': 0.42} in u['reasons']
    assert [c['fit'] for c in out['candidates']] == sorted((c['fit'] for c in out['candidates']), reverse=True)


def test_analyst_rating_never_reaches_ui(client, screen, portfolio):
    body = client.post('/api/explore', json={'intent': 'diversify'}).get_data(as_text=True)
    assert 'Strong Buy' not in body and 'averageAnalystRating' not in body and '2.0 - Buy' not in body


def test_screener_failure_is_reported(client, monkeypatch, screen, portfolio):
    screen.fail = True
    out = strict(client.post('/api/explore', json={'intent': 'value'}))
    assert out['ok'] is False and out['error'] == 'screener_unavailable' and 'caído' in out['detail']


def test_excluded_sector_never_searched(client, screen, portfolio):
    client.put('/api/explore/preferences', json={'exclude_sectors': ['Healthcare']})
    client.post('/api/explore', json={'intent': 'diversify'})
    sent = json.dumps(screen.calls[0][0])
    assert 'Healthcare' not in sent


# ── Preferencias ────────────────────────────────────────────────────────────

def test_preferences_validated_and_persisted(client):
    d = strict(client.get('/api/explore/preferences'))
    assert d['max_level'] == 'moderate' and len(d['sectors']) == 11
    saved = strict(client.put('/api/explore/preferences', json={
        'max_level': 'extreme', 'priority': 'dividends', 'exclude_sectors': ['Energy', 'Crypto'],
        'min_mcap_b': 99999, 'avoid_crypto': 'si'}))
    assert saved == {'max_level': 'moderate', 'priority': 'dividends', 'exclude_sectors': ['Energy'],
                     'min_mcap_b': 3000.0, 'avoid_crypto': True}
    assert explore.load_prefs() == saved


# ── Chat ────────────────────────────────────────────────────────────────────

def test_chat_explore_directive(client, screen, portfolio, monkeypatch, server_module):
    reply = ('Busco empresas de sectores que no tenés.\n'
             '[[EXPLORE intent=diversify sectors=Healthcare,Utilities]]\n'
             'Te sugiero mirar XYZW y también CRM.')
    monkeypatch.setattr(server_module, 'NVIDIA_API_KEY', 'k')
    monkeypatch.setattr(server_module, '_chat_call', lambda m: reply)
    seen = {}
    real = server_module._run_explore
    monkeypatch.setattr(server_module, '_run_explore', lambda *a: seen.setdefault('r', real(*a)))
    out = strict(client.post('/api/chat', json={'message': 'Sugerime tickers nuevos', 'tickers': list(POSITIONS),
                                                'owned': list(POSITIONS) + ['CRM']}))
    assert out['reply'] == 'Busco empresas de sectores que no tenés.'
    assert out['omitted']['unknown_ticker'] == 1, 'el modelo no puede imponer tickers fuera de los datos'
    assert 'explore_spec' not in out and out['explore']['ok'] is True
    assert {c['ticker'] for c in out['explore']['candidates']} == {'HLTH', 'UTIL'}
    assert out['explore']['params']['sectors'] == ['Healthcare', 'Utilities']


def test_prompt_has_profile_and_preferences(client, screen, portfolio, monkeypatch, server_module):
    got = {}
    monkeypatch.setattr(server_module, 'NVIDIA_API_KEY', 'k')
    monkeypatch.setattr(server_module, '_chat_call', lambda m: got.setdefault('p', m[0]['content']) and 'ok')
    client.post('/api/chat', json={'message': 'hola', 'tickers': list(POSITIONS)})
    p = got['p']
    assert 'PROFILE (computed from positions)' in p and 'PREFERENCES (set by the user)' in p
    assert '[[EXPLORE intent=' in p and 'Healthcare' in p
    assert 'Strong Buy' not in p and 'analyst' not in p.lower()


@pytest.mark.parametrize('text,unknown', [
    ('El RSI y la EMA de ACME están bien en USD.', []),
    ('Mirá el ETF y el FCF del Q3 y FY2026.', []),
    ('Podrías sumar ORCL o JNJ.', ['ORCL', 'JNJ']),
    ('BRK-B tiene beta baja.', ['BRK-B']),
])
def test_unknown_ticker_detector(text, unknown):
    assert agent.unknown_tickers(text, {'ACME', 'SOL'}) == unknown
