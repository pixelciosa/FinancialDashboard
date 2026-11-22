"""Portfolio Lab ledger: investments ledger parser (spreadsheet-export format), history file,
loader, excluded platforms and reconciliation. Synthetic fixtures; the last tests read the
FICTIONAL demo files shipped in sample_data/ (ledger.csv + investments_ledger.csv)."""
import json
import os

import pytest
from flask import Flask

from ._helpers import load_lab

M = load_lab()
lab_api, L = M['api'], M['ledger']

# --- Investments ledger (spreadsheet export: ';', d/m/yy, Spanish-locale numbers) -------------
HEADER = ('Fecha;Activo;Símbolo;Tipo de Operación;Tipo de Activo;Cantidad;Plataforma;'
          'Precio en USD (MEP);Precio en moneda de cambio;Moneda de cambio;'
          'Inversión en dólares;Ultimo valor de mercado reportado;Variación;Valor;Var2;Ratio CEDEARS')
INCLUDED = ('BrokerA', 'ExchangeB')
EXCLUDED = ('BankC',)


def _row(fecha='5/3/26', activo='Foo', sim='FOO', op='Compra', tipo='Acción', cant='2,5',
         plat='BrokerA', precio='US$\xa0100,00', pc='100', mon='USD', inv='US$\t250,00', extra=';;;;'):
    return f'{fecha};{activo};{sim};{op};{tipo};{cant};{plat};{precio};{pc};{mon};{inv}{extra}'


def _parse(*rows, header=HEADER, titular='Holder A', bom=True, included=INCLUDED, excluded=EXCLUDED):
    text = ('﻿' if bom else '') + header + '\n' + '\n'.join(rows) + '\n'
    return L.parse_ledger_text(text, titular, excluded, included)


def test_bom_separator_and_date():
    r = _parse(_row())
    assert r['errors'] == []
    o = r['ops'][0]
    assert o['fecha'] == '2026-03-05' and o['ticker'] == 'FOO' and o['tipo'] == 'compra'
    assert o['cantidad'] == 2.5 and o['monto_usd'] == 250.0 and o['titular'] == 'Holder A'
    assert o['plataforma'] == 'BrokerA'


def test_number_formats():
    assert L.parse_number_es('US$\xa01.234,56') == 1234.56
    assert L.parse_number_es('US$\t(1.234,50)') == -1234.5
    assert L.parse_number_es('-0,25') == -0.25
    assert L.parse_number_es('1.500') == 1500
    assert L.parse_number_es('') is None
    with pytest.raises(ValueError):
        L.parse_number_es('abc')


def test_date_formats():
    assert L.parse_date_dmy('13/1/26') == '2026-01-13'
    assert L.parse_date_dmy('3/11/2025') == '2025-11-03'
    with pytest.raises(ValueError):
        L.parse_date_dmy('31/2/26')


def test_multiline_quoted_name_uses_real_csv_parser():
    r = _parse(_row(activo='"Vanguard Total\nStock Market ETF"', sim='VTI'), _row(sim='BAR'))
    assert r['errors'] == []
    assert [o['ticker'] for o in r['ops']] == ['VTI', 'BAR']


def test_header_without_ratio_column_and_blank_rows():
    header = ';'.join(HEADER.split(';')[:-1])
    r = _parse(_row(extra=';;;'), ';;;;;;;;;;;;;;', ';;;;;;;;;;;;;;', header=header)
    assert len(r['ops']) == 1 and r['excluded']['vacias'] == 2 and r['errors'] == []


def test_symbols_normalized_without_usd_suffix():
    r = _parse(*[_row(sim=s, tipo='Criptomoneda', plat='ExchangeB') for s in ('SOL-USD', 'ada-usd', 'DOGE')])
    assert [o['ticker'] for o in r['ops']] == ['SOL', 'ADA', 'DOGE']


def test_platforms_included_canonical_and_excluded_remembered():
    r = _parse(_row(plat='BrokerA'), _row(plat='exchangeb'), _row(plat='BankC', sim='NUX', cant='3'),
               _row(plat='bankc', sim='NUX', op='Venta', cant='1'))
    assert [o['plataforma'] for o in r['ops']] == ['BrokerA', 'ExchangeB']   # spelling from config
    assert r['excluded']['plataforma_excluida'] == 2
    assert r['excluded_ops'] == [{'ticker': 'NUX', 'cantidad': 3.0, 'plataforma': 'BankC'},
                                 {'ticker': 'NUX', 'cantidad': -1.0, 'plataforma': 'bankc'}]


def test_unknown_platform_is_row_error_only_with_an_included_list():
    r = _parse(_row(plat='Typo'), _row())
    assert len(r['ops']) == 1 and 'unknown platform' in r['errors'][0]['error']
    r = _parse(_row(plat='AnyBroker'), included=())
    assert r['errors'] == [] and r['ops'][0]['plataforma'] == 'AnyBroker'


def test_stable_assets_excluded():
    r = _parse(_row(sim='USDT', tipo='Stable'), _row())
    assert len(r['ops']) == 1 and r['excluded']['stable'] == 1


def test_sale_has_negative_quantity_and_absolute_amount():
    r = _parse(_row(op='Venta', cant='-1,5', inv='US$\t(150,00)'))
    o = r['ops'][0]
    assert o['tipo'] == 'venta' and o['cantidad'] == -1.5 and o['monto_usd'] == 150.0


def test_dividend_in_units_is_zero_cost():
    r = _parse(_row(op='Dividendos', cant='0,03', precio='', pc='', mon='', inv=''))
    o = r['ops'][0]
    assert o['tipo'] == 'dividendo_unidades' and o['cantidad'] == 0.03
    assert o['monto_usd'] == 0.0 and o['precio_usd'] == 0.0


def test_cash_dividend_is_income_not_flow():
    r = _parse(_row(op='Dividendos', cant='', precio='', pc='', mon='USD', inv='US$\t(0,37)'))
    o = r['ops'][0]
    assert o['tipo'] == 'dividendo_efectivo' and o['monto_usd'] == 0.37
    assert o['cantidad'] is None and o['precio_usd'] is None  # None, never 0


def test_swap_generates_sale_leg():
    r = _parse(_row(sim='SOL-USD', tipo='Criptomoneda', cant='2', plat='ExchangeB',
                    pc='150', mon='ADA', inv='US$\t400,00'))
    buy, sell = r['ops']
    assert buy['tipo'] == 'compra' and buy['ticker'] == 'SOL' and buy['cantidad'] == 2
    assert sell['tipo'] == 'canje' and sell['ticker'] == 'ADA'
    assert sell['cantidad'] == pytest.approx(-300) and sell['monto_usd'] == 400.0
    assert sell['plataforma'] == 'ExchangeB'


def test_swap_without_exchange_price_is_row_error():
    r = _parse(_row(mon='ADA', pc=''))
    assert r['ops'] == [] and 'swap' in r['errors'][0]['error']


def test_only_columns_up_to_investment_are_read():
    r = _parse(_row(extra=';basura;(zzz);###;nope'))
    assert r['errors'] == [] and len(r['ops']) == 1


def test_row_errors_reported_without_aborting():
    r = _parse(_row(cant='xx'), _row(fecha='31/2/26'), _row(op='Regalo'), _row(inv=''),
               'short;row', _row(sim='OK'))
    assert len(r['ops']) == 1 and r['ops'][0]['ticker'] == 'OK'
    assert len(r['errors']) == 5 and all('fila' in e and e['error'] for e in r['errors'])
    assert set(o['tipo'] for o in r['ops']) <= set(L.TIPOS)


def test_titular_from_filename():
    assert L.titular_from_filename('investments 26-HolderA.csv') == 'HolderA'
    assert L.titular_from_filename('/x/y/ledger-B.csv') == 'B'
    assert L.titular_from_filename('investments_ledger.csv') is None


# --- History file (opening positions, clean format) ------------------------------------
HIST_HEADER = 'fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen;plataforma'


def _hrow(fecha='2026-03-05', ticker='FOO', cant='2.5', precio='100', monto='250', origen='compra',
          plat='BrokerA'):
    return f'{fecha};{ticker};{cant};{precio};{monto};{origen};{plat}'


def _hparse(*rows, header=HIST_HEADER, excluded=EXCLUDED):
    return L.parse_history_text('﻿' + header + '\n' + '\n'.join(rows) + '\n', excluded)


def test_history_mapping():
    r = _hparse(_hrow(origen='apertura', precio='', monto='', plat=''),
                _hrow(cant='4', precio='12.5', monto=''),
                _hrow(origen='venta', cant='1.5', monto='-150'),
                _hrow(origen='dividendo', cant='0.03', precio='', monto=''),
                _hrow(origen='dividendo_efectivo', cant='', precio='', monto='0.37'))
    assert r['errors'] == []
    a, b, c, d, e = r['ops']
    assert a['tipo'] == 'apertura' and a['precio_usd'] is None and a['monto_usd'] is None
    assert a['plataforma'] is None and a['titular'] is None
    assert b['monto_usd'] == 50.0                            # derived from the price
    assert c['tipo'] == 'venta' and c['cantidad'] == -1.5 and c['monto_usd'] == 150.0
    assert d['tipo'] == 'dividendo_unidades' and d['monto_usd'] == 0.0
    assert e['tipo'] == 'dividendo_efectivo' and e['cantidad'] is None
    assert set(o['tipo'] for o in r['ops']) <= set(L.TIPOS)


def test_history_number_format_and_optional_platform():
    assert L.parse_number('US$ 1,234.56') == 1234.56 and L.parse_number('(1,234.50)') == -1234.5
    r = _hparse('2026-01-02;FOO;1;10;10;compra', header='fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen')
    assert r['errors'] == [] and r['ops'][0]['plataforma'] is None


def test_history_errors_and_missing_columns():
    r = _hparse(_hrow(cant='xx'), _hrow(fecha='2026-02-31'), _hrow(origen='regalo'), _hrow(ticker='OK'))
    assert len(r['ops']) == 1 and len(r['errors']) == 3
    r = _hparse('2026-01-02;FOO;1', header='fecha;ticker;cantidad')
    assert r['ops'] == [] and 'missing columns' in r['errors'][0]['error']


# --- Loading, routes and reconciliation ---------------------------------------------
def _make_files(tmp_path):
    d = tmp_path / 'books'
    d.mkdir()
    (d / 'investments 26-HolderA.csv').write_text(
        '﻿' + HEADER + '\n' + _row(sim='AAA') + '\n' +
        _row(op='Dividendos', cant='', precio='', pc='', mon='', inv='US$\t(1,00)') + '\n' +
        _row(sim='ZZZ', cant='5', plat='BankC') + '\n', encoding='utf-8')
    (d / 'investments 26-HolderB.csv').write_text(
        '﻿' + ';'.join(HEADER.split(';')[:-1]) + '\n' + _row(sim='ZZZ', extra=';;;') + '\n',
        encoding='utf-8')
    h = tmp_path / 'hist.csv'
    h.write_text(HIST_HEADER + '\n' + _hrow(fecha='2025-01-02', ticker='AAA', cant='1.5', precio='',
                                            monto='', origen='apertura', plat='') + '\n', encoding='utf-8')
    cfg = {'ledger_dir': str(d), 'included_platforms': list(INCLUDED), 'excluded_platforms': list(EXCLUDED),
           'ledger_files': ['investments 26-HolderA.csv', {'path': 'investments 26-HolderB.csv', 'titular': 'B'}]}
    return cfg, str(h)


def test_load_ledger_history_plus_files_with_holders_and_counts(tmp_path, monkeypatch):
    monkeypatch.delenv(L.INVESTMENTS_ENV, raising=False)
    cfg, h = _make_files(tmp_path)
    r = L.load_ledger(cfg, history_path=h)
    assert r['errors'] == []
    c = r['counts']
    assert c['historial'] == 1 and c['total'] == 4
    assert c['ingresos_efectivo'] == 1 and c['operaciones'] == 3
    assert c['excluidas']['plataforma_excluida'] == 1
    assert c['por_archivo'] == {'investments 26-HolderA.csv': 2, 'investments 26-HolderB.csv': 1}
    tit = {(o['ticker'], o['titular']) for o in r['ops'] if o['origen'] == 'libro'}
    assert ('AAA', 'HolderA') in tit and ('ZZZ', 'B') in tit
    assert r['ops'][0]['tipo'] == 'apertura'                  # sorted by date
    assert r['excluded_ops'] == [{'ticker': 'ZZZ', 'cantidad': 5.0, 'plataforma': 'BankC'}]


def test_ledger_files_relative_to_project_and_env_override(tmp_path, monkeypatch):
    monkeypatch.delenv(L.INVESTMENTS_ENV, raising=False)
    files = L.ledger_files({'ledger_files': ['sample_data/x.csv']})
    assert files[0]['path'] == os.path.join(L.ROOT, 'sample_data', 'x.csv')
    a, b = tmp_path / 'one-A.csv', tmp_path / 'two.csv'
    monkeypatch.setenv(L.INVESTMENTS_ENV, f'{a}{os.pathsep}{b}')
    files = L.ledger_files({'ledger_files': ['ignored.csv']})
    assert [f['path'] for f in files] == [str(a), str(b)] and files[0]['titular'] == 'A'


def test_load_ledger_missing_files_reported(tmp_path, monkeypatch):
    monkeypatch.delenv(L.INVESTMENTS_ENV, raising=False)
    cfg, h = _make_files(tmp_path)
    cfg['ledger_files'].append('nope.csv')
    r = L.load_ledger(cfg, history_path=str(tmp_path / 'no_history.csv'))
    errs = {e['archivo']: e['error'] for e in r['errors']}
    assert errs == {'no_history.csv': 'file not found', 'nope.csv': 'file not found'}
    assert r['counts']['por_archivo']['nope.csv'] is None


def test_reconcile_tolerance():
    rows = {r['ticker']: r for r in L.reconcile(
        {'A': 100.0, 'B': 100.0, 'C': 1.0}, {'A': 101.0, 'B': 110.0, 'D': 5.0})}
    assert rows['A']['estado'] == 'ok'
    assert rows['B']['estado'] == 'diff'
    assert rows['C']['estado'] == 'sin_portfolio' and rows['D']['estado'] == 'sin_historial'
    assert rows['D']['diferencia'] is None


def test_portfolio_holdings_reader():
    txt = ('ticker,name,type,quantity,cost_basis_usd\n'
           'DOGE,Dogecoin,Crypto,813.42,100\nSOL-USD,Solana,Crypto,1,50\nVTI,VTI,ETF,4,1000\n'
           ',,,,\nBAD,Bad,Stock,abc,1\n')
    assert L.parse_portfolio_holdings(txt) == {'DOGE': 813.42, 'SOL': 1.0, 'VTI': 4.0}


def test_endpoints_with_synthetic_files(tmp_path, monkeypatch):
    monkeypatch.delenv(L.INVESTMENTS_ENV, raising=False)
    cfg, h = _make_files(tmp_path)
    pf = tmp_path / 'pf.csv'
    pf.write_text('ticker,quantity\nAAA,4\nZZZ,7.5\n', encoding='utf-8')
    monkeypatch.setattr(L, 'load_config', lambda path=None: cfg)
    monkeypatch.setattr(L, 'PORTFOLIO_CSV', str(pf))
    orig = L.load_ledger
    monkeypatch.setattr(L, 'load_ledger', lambda config=None, history_path=None: orig(config, h))
    app = Flask(__name__)
    lab_api.register(app)
    c = app.test_client()
    j = c.get('/api/lab/ledger').get_json()
    assert j['ok'] and j['counts']['operaciones'] == 3 and j['errors'] == []
    assert 'NaN' not in json.dumps(j, allow_nan=False)
    rec = c.get('/api/lab/reconcile').get_json()
    est = {r['ticker']: r['estado'] for r in rec['rows']}
    assert est['AAA'] == 'ok' and est['ZZZ'] == 'esperada_excluida'   # 2.5 in the Lab + 5 on BankC


def _rec(lq, pq, xq=None):
    return {r['ticker']: r for r in L.reconcile(lq, pq, excluded_qty=xq)}


def test_excluded_platform_closes_is_expected():
    r = _rec({'A': 1.0}, {'A': 3.0, 'B': 2.0}, {'A': 2.0, 'B': 2.0})
    assert r['A']['estado'] == 'esperada_excluida' and r['A']['motivo'] == L.EXCLUIDA_MOTIVO
    assert r['B']['estado'] == 'esperada_excluida'  # no ledger rows, only the excluded platform


def test_excluded_platform_that_does_not_close_is_real_diff():
    r = _rec({'A': 1.0}, {'A': 3.0}, {'A': 1.0})
    assert r['A']['estado'] == 'diff' and r['A']['motivo'] is None


def test_rounding_residue_counts_as_ok_only_when_portfolio_zero_or_absent():
    r = _rec({'X': -0.004, 'Y': 0.003, 'Z': -0.006}, {'X': 0.0, 'Z': -0.01})
    assert r['X']['estado'] == 'ok' and r['Y']['estado'] == 'ok'
    assert r['Z']['estado'] == 'diff'  # portfolio is not 0: the rule does not apply
    assert _rec({'W': 0.02}, {})['W']['estado'] == 'sin_portfolio'


def test_small_crypto_difference_stays_real():
    r = _rec({'SOL': 0.0075}, {'SOL': 0.0063})
    assert r['SOL']['estado'] == 'diff'


def test_ticker_without_history_is_informative_not_error():
    rows = L.reconcile({'A': 1.0, 'SOL': 0.0075}, {'A': 1.0, 'AVAX': 1.0, 'SOL': 0.0063})
    s = L.summarize_reconcile(rows)
    assert s['sin_historial'] == {'conteo': 1, 'tickers': ['AVAX']}
    assert s['diferencias_reales']['tickers'] == ['SOL']
    assert s['ok']['conteo'] == 1 and s['esperada_excluida']['conteo'] == 0


# --- Demo files (fictional, shipped in sample_data/) ------------------------------------
_DEMO_FILES = L.ledger_files(L.load_config()) if os.path.exists(L.CONFIG_PATH) else []
_DEMO_OK = os.path.exists(L.LEDGER_PATH) and bool(_DEMO_FILES) and all(os.path.exists(f['path']) for f in _DEMO_FILES)


@pytest.mark.skipif(not _DEMO_OK, reason='demo ledger files not available')
def test_demo_ledger_parses_and_reconciles_with_demo_portfolio(monkeypatch):
    """Replaces the private edition's count on the author's real files: every expected value
    comes from the FICTIONAL demo files written by scripts/generate_demo_data.py."""
    monkeypatch.delenv(L.INVESTMENTS_ENV, raising=False)
    assert [os.path.relpath(f['path'], L.ROOT) for f in _DEMO_FILES] == [
        os.path.join('sample_data', 'investments_ledger.csv')]
    r = L.load_ledger()
    assert r['errors'] == []
    c = r['counts']
    assert c['historial'] == 6 and c['por_archivo'] == {'investments_ledger.csv': 36}
    assert c['operaciones'] == 36 and c['ingresos_efectivo'] == 6
    assert c['excluidas'] == {'plataforma_excluida': 2, 'stable': 2, 'vacias': 1}
    tipos = {o['tipo'] for o in r['ops']}
    assert tipos == set(L.TIPOS)                     # the demo exercises every operation type
    assert {o['titular'] for o in r['ops'] if o['origen'] == 'libro'} == {'Holder A'}
    assert all(o['fecha'][:4] in ('2025', '2026') for o in r['ops'])
    with open(L.PORTFOLIO_CSV, encoding='utf-8-sig', newline='') as f:
        holdings = L.parse_portfolio_holdings(f.read())
    rows = L.reconcile(L.final_quantities(r['ops']), holdings,
                       excluded_qty=L.excluded_quantities(r['excluded_ops']))
    s = L.summarize_reconcile(rows)
    assert s['esperada_excluida']['tickers'] == ['NU']
    assert s['diferencias_reales']['conteo'] == 0 and s['sin_historial']['conteo'] == 0
    assert s['ok']['conteo'] == 12
