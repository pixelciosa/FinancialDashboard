"""Fase 7: lector de bloques de riesgo (config/lab/blocks.json). Offline."""
import copy
import json

import pytest

from ._helpers import load_lab

M = load_lab()
blocks, prices = M['blocks'], M['prices']

BASE = {'bloques': [
    {'id': 'cripto', 'nombre': 'Cripto', 'tickers': ['SOL', 'ADA-USD'], 'indice_proxy': 'SOL-USD', 'bandas': {'referencia_pct': 40.0}},
    {'id': 'tech', 'nombre': 'Tech', 'tickers': ['JPM', 'ORCL'], 'indice_proxy': 'QQQ', 'bandas': {'referencia_pct': 40.0}},
    {'id': 'resto', 'nombre': 'Resto', 'tickers': [], 'indice_proxy': 'SPY', 'bandas': {'referencia_pct': 20.0}},
]}


def _load(data, tmp_path):
    p = tmp_path / 'b.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    return blocks.load_blocks(str(p))


def test_real_blocks_json_is_valid_and_matches_plan():
    """config/lab/blocks.json of the demo. Reference weights were regenerated from the FICTIONAL
    portfolio (sample_data/ledger.csv + synthetic price fixture) by scripts/generate_demo_data.py."""
    cfg = blocks.load_blocks()
    ids = [b['id'] for b in cfg['blocks']]
    assert ids == ['nucleo_etf', 'tecnologia', 'consumo_salud', 'financieras', 'cripto', 'resto']
    assert cfg['proxies'] == {'nucleo_etf': 'SPY', 'tecnologia': 'QQQ', 'consumo_salud': 'XLP',
                              'financieras': 'SPY', 'cripto': 'BTC-USD', 'resto': 'SPY'}
    assert cfg['names']['nucleo_etf'] == 'Core ETFs' and cfg['names']['cripto'] == 'Crypto'
    refs = {b['id']: b['referencia_pct'] for b in cfg['blocks']}
    assert refs == {'nucleo_etf': 24.6, 'tecnologia': 28.3, 'consumo_salud': 14.0, 'financieras': 29.1,
                    'cripto': 4.0, 'resto': 0.0}
    assert sum(refs.values()) == pytest.approx(100.0, abs=0.2)
    assert [b['id'] for b in cfg['blocks'] if b['comodin']] == ['resto']
    assert cfg['rule'] == {'nombre': '5/25', 'absoluta_pp': 5.0, 'relativa': 0.25,
                           'fecha_referencia': '2026-11-13'}
    assert (cfg['cap']['aviso_pct'], cfg['cap']['alerta_pct']) == (15.0, 25.0)
    assert list(cfg['cap']['excepciones']) == ['VTI']
    assert cfg['meta']['modo'] == 'descriptivo' and cfg['meta']['fecha_referencia'] == '2026-11-13'
    assert cfg['warnings'] == []
    assert blocks.block_of('CRM', cfg['map']) == 'tecnologia'
    assert blocks.block_of('SOL-USD', cfg['map']) == 'cripto'
    assert blocks.block_of('USO', cfg['map']) == 'resto'     # not in any block


def test_rule_and_cap_defaults_and_validation(tmp_path):
    cfg = _load(BASE, tmp_path)
    assert cfg['rule']['absoluta_pp'] == 5.0 and cfg['cap']['excepciones'] == {}
    data = copy.deepcopy(BASE)
    data['regla_banda'] = {'nombre': '3/20', 'absoluta_pp': 3, 'relativa': 0.2, 'fecha_referencia': '2026-01-01'}
    data['tope_posicion'] = {'aviso_pct': 8, 'alerta_pct': 15, 'excepciones': ['sol'], 'nota': 'x'}
    cfg = _load(data, tmp_path)
    assert cfg['rule'] == {'nombre': '3/20', 'absoluta_pp': 3.0, 'relativa': 0.2, 'fecha_referencia': '2026-01-01'}
    assert cfg['cap']['excepciones'] == {'SOL-USD': 'x'}


def test_reference_is_optional_and_bandas_may_be_null(tmp_path):
    data = copy.deepcopy(BASE)
    data['bloques'][0]['bandas'] = None
    assert {b['id']: b['referencia_pct'] for b in _load(data, tmp_path)['blocks']}['cripto'] is None


def test_crypto_keys_with_and_without_usd_suffix_are_equivalent(tmp_path):
    cfg = _load(BASE, tmp_path)
    for t in ('SOL', 'SOL-USD', 'ADA', 'ADA-USD'):
        assert blocks.block_of(t, cfg['map']) == 'cripto'
    assert cfg['map'] == {'SOL-USD': 'cripto', 'ADA-USD': 'cripto', 'JPM': 'tech', 'ORCL': 'tech'}


def test_unlisted_ticker_goes_to_resto(tmp_path):
    cfg = _load(BASE, tmp_path)
    assert blocks.block_of('SE', cfg['map']) == 'resto'


def test_repeated_ticker_first_block_wins_with_warning(tmp_path):
    data = copy.deepcopy(BASE)
    data['bloques'][1]['tickers'].append('SOL-USD')   # SOL ya esta en cripto
    cfg = _load(data, tmp_path)
    assert blocks.block_of('SOL', cfg['map']) == 'cripto'
    assert cfg['warnings'] == [{'tipo': 'ticker_repetido', 'ticker': 'SOL-USD',
                                'bloques': ['cripto', 'tech'], 'usado': 'cripto'}]


@pytest.mark.parametrize('mutate, msg', [
    (lambda d: d['bloques'].pop(), 'resto'),
    (lambda d: d['bloques'][0].update(indice_proxy=''), 'indice_proxy'),
    (lambda d: d['bloques'][0].update(tickers='SOL'), 'tickers'),
    (lambda d: d['bloques'][0].update(bandas=[1]), 'bandas'),
    (lambda d: d['bloques'][0].update(bandas={'referencia_pct': 'x'}), 'referencia_pct'),
    (lambda d: d.update(regla_banda={'absoluta_pp': -1, 'relativa': 0.25}), 'absoluta_pp'),
    (lambda d: d.update(tope_posicion={'aviso_pct': 20, 'alerta_pct': 10}), 'alerta_pct'),
    (lambda d: d.update(tope_posicion={'aviso_pct': 10, 'alerta_pct': 20, 'excepciones': 'LINK'}), 'excepciones'),
    (lambda d: d['bloques'][1].update(id='cripto'), 'repetido'),
    (lambda d: d['bloques'][0].pop('nombre'), 'nombre'),
    (lambda d: d.update(bloques=[]), 'bloques'),
])
def test_invalid_blocks_raise(tmp_path, mutate, msg):
    data = copy.deepcopy(BASE)
    mutate(data)
    with pytest.raises(blocks.BlocksError, match=msg):
        _load(data, tmp_path)


def test_missing_or_broken_file_raises(tmp_path):
    with pytest.raises(blocks.BlocksError):
        blocks.load_blocks(str(tmp_path / 'no_existe.json'))
    bad = tmp_path / 'x.json'
    bad.write_text('{no es json', encoding='utf-8')
    with pytest.raises(blocks.BlocksError):
        blocks.load_blocks(str(bad))


def test_proxy_symbol_is_normalized(tmp_path):
    data = copy.deepcopy(BASE)
    data['bloques'][0]['indice_proxy'] = 'sol'
    cfg = _load(data, tmp_path)
    assert cfg['proxies']['cripto'] == prices.price_symbol('sol') == 'SOL-USD'
