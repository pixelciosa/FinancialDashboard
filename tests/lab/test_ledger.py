"""Portfolio Lab ledger: clean CSV parser, loader, excluded platforms and reconciliation.
Synthetic fixtures; the last test reads the fictional demo ledger in sample_data/."""
import json
import os

import pytest
from flask import Flask

from ._helpers import load_lab

M = load_lab()
lab_api, L = M['api'], M['ledger']

HEADER = 'fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen;plataforma'


def _row(fecha='2026-03-05', ticker='FOO', cant='2.5', precio='100', monto='250', origen='compra',
         plat='BrokerA'):
    return f'{fecha};{ticker};{cant};{precio};{monto};{origen};{plat}'


def _parse(*rows, header=HEADER, bom=True, excluded=('BankC',)):
    text = ('﻿' if bom else '') + header + '\n' + '\n'.join(rows) + '\n'
    return L.parse_ledger_text(text, excluded)


# --- Parser ------------------------------------------------------------------------
def test_bom_separator_and_date():
    r = _parse(_row())
    assert r['errors'] == []
    o = r['ops'][0]
    assert o['fecha'] == '2026-03-05' and o['ticker'] == 'FOO' and o['tipo'] == 'compra'
    assert o['cantidad'] == 2.5 and o['monto_usd'] == 250.0 and o['plataforma'] == 'BrokerA'


def test_number_formats():
    assert L.parse_number('1234.56') == 1234.56
    assert L.parse_number('US$ 1,234.56') == 1234.56
    assert L.parse_number('(1,234.50)') == -1234.5
    assert L.parse_number('-0.25') == -0.25
    assert L.parse_number('') is None
    with pytest.raises(ValueError):
        L.parse_number('abc')


def test_platform_column_is_optional():
    header = 'fecha;ticker;cantidad;precio_unitario_usd;monto_usd;origen'
    r = _parse('2026-01-02;FOO;1;10;10;compra', header=header)
    assert r['errors'] == [] and r['ops'][0]['plataforma'] is None


def test_missing_required_column_is_reported():
    r = _parse('2026-01-02;FOO;1', header='fecha;ticker;cantidad')
    assert r['ops'] == [] and 'missing columns' in r['errors'][0]['error']


def test_blank_rows_are_counted_not_errors():
    r = _parse(_row(), ';;;;;;', ';;;;;;')
    assert len(r['ops']) == 1 and r['excluded']['vacias'] == 2 and r['errors'] == []


def test_symbols_normalized_without_usd_suffix():
    r = _parse(*[_row(ticker=s) for s in ('SOL-USD', 'ada-usd', 'DOGE')])
    assert [o['ticker'] for o in r['ops']] == ['SOL', 'ADA', 'DOGE']


def test_excluded_platform_rows_are_left_out_but_remembered():
    r = _parse(_row(plat='BrokerA'), _row(plat='ExchangeB'), _row(ticker='NUX', cant='3', plat='bankc'),
               _row(ticker='NUX', cant='1', origen='venta', plat='BankC'))
    assert [o['plataforma'] for o in r['ops']] == ['BrokerA', 'ExchangeB']
    assert r['excluded']['plataforma_excluida'] == 2
    assert r['excluded_ops'] == [{'ticker': 'NUX', 'cantidad': 3.0, 'plataforma': 'bankc'},
                                 {'ticker': 'NUX', 'cantidad': -1.0, 'plataforma': 'BankC'}]


def test_sale_has_negative_quantity_and_absolute_amount():
    r = _parse(_row(origen='venta', cant='1.5', monto='-150'))
    o = r['ops'][0]
    assert o['tipo'] == 'venta' and o['cantidad'] == -1.5 and o['monto_usd'] == 150.0


def test_amount_derived_from_price_when_missing():
    o = _parse(_row(cant='4', precio='12.5', monto=''))['ops'][0]
    assert o['monto_usd'] == 50.0


def test_opening_position_has_no_price_or_amount():
    o = _parse(_row(origen='apertura', precio='', monto='', plat=''))['ops'][0]
    assert o['tipo'] == 'apertura' and o['precio_usd'] is None and o['monto_usd'] is None


def test_dividend_in_units_is_zero_cost():
    o = _parse(_row(origen='dividendo', cant='0.03', precio='', monto=''))['ops'][0]
    assert o['tipo'] == 'dividendo_unidades' and o['cantidad'] == 0.03
    assert o['monto_usd'] == 0.0 and o['precio_usd'] == 0.0


def test_cash_dividend_is_income_not_flow():
    o = _parse(_row(origen='dividendo_efectivo', cant='', precio='', monto='0.37'))['ops'][0]
    assert o['tipo'] == 'dividendo_efectivo' and o['monto_usd'] == 0.37
    assert o['cantidad'] is None and o['precio_usd'] is None  # None, never 0


def test_row_errors_reported_without_aborting():
    r = _parse(_row(cant='xx'), _row(fecha='2026-02-31'), _row(origen='regalo'),
               _row(precio='', monto=''), _row(ticker='OK'))
    assert len(r['ops']) == 1 and r['ops'][0]['ticker'] == 'OK'
    assert len(r['errors']) == 4 and all('fila' in e and e['error'] for e in r['errors'])
    assert set(o['tipo'] for o in r['ops']) <= set(L.TIPOS)


# --- Loading, routes and reconciliation ---------------------------------------------
def _ledger_file(tmp_path):
    p = tmp_path / 'ledger.csv'
    p.write_text('﻿' + HEADER + '\n' + '\n'.join([
        _row(fecha='2025-01-02', ticker='AAA', cant='4', precio='', monto='', origen='apertura', plat=''),
        _row(fecha='2025-02-01', ticker='ZZZ', cant='2'),
        _row(fecha='2025-03-01', ticker='ZZZ', cant='5', plat='BankC'),
        _row(fecha='2025-04-01', ticker='AAA', cant='', precio='', monto='1.00', origen='dividendo_efectivo'),
    ]) + '\n', encoding='utf-8')
    return str(p)


def test_load_ledger_counts_and_excluded(tmp_path):
    r = L.load_ledger({'excluded_platforms': ['BankC']}, ledger_path=_ledger_file(tmp_path))
    assert r['errors'] == []
    c = r['counts']
    assert c['total'] == 3 and c['ingresos_efectivo'] == 1 and c['operaciones'] == 2
    assert c['excluidas']['plataforma_excluida'] == 1
    assert r['excluded_ops'] == [{'ticker': 'ZZZ', 'cantidad': 5.0, 'plataforma': 'BankC'}]


def test_load_ledger_missing_file_reported(tmp_path):
    r = L.load_ledger({}, ledger_path=str(tmp_path / 'nope.csv'))
    assert r['ops'] == [] and r['errors'][0]['error'] == 'file not found'


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
    path = _ledger_file(tmp_path)
    pf = tmp_path / 'pf.csv'
    pf.write_text('ticker,quantity\nAAA,4\nZZZ,7\n', encoding='utf-8')
    monkeypatch.setattr(L, 'load_config', lambda path=None: {'excluded_platforms': ['BankC']})
    monkeypatch.setattr(L, 'PORTFOLIO_CSV', str(pf))
    orig = L.load_ledger
    monkeypatch.setattr(L, 'load_ledger', lambda config=None, ledger_path=None: orig(config, path))
    app = Flask(__name__)
    lab_api.register(app)
    c = app.test_client()
    j = c.get('/api/lab/ledger').get_json()
    assert j['ok'] and j['counts']['operaciones'] == 2 and j['errors'] == []
    assert 'NaN' not in json.dumps(j, allow_nan=False)
    rec = c.get('/api/lab/reconcile').get_json()
    est = {r['ticker']: r['estado'] for r in rec['rows']}
    assert est['AAA'] == 'ok' and est['ZZZ'] == 'esperada_excluida'


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
@pytest.mark.skipif(not os.path.exists(L.LEDGER_PATH), reason='demo ledger not available')
def test_demo_ledger_parses_and_reconciles_with_demo_portfolio():
    """Expected values come from the FICTIONAL demo ledger generated by scripts/generate_demo_data.py."""
    r = L.load_ledger()
    assert r['errors'] == []
    assert r['counts']['operaciones'] == 31 and r['counts']['ingresos_efectivo'] == 3
    with open(L.PORTFOLIO_CSV, encoding='utf-8-sig', newline='') as f:
        holdings = L.parse_portfolio_holdings(f.read())
    rows = L.reconcile(L.final_quantities(r['ops']), holdings,
                       excluded_qty=L.excluded_quantities(r['excluded_ops']))
    s = L.summarize_reconcile(rows)
    assert s['esperada_excluida']['tickers'] == ['NU']
    assert s['diferencias_reales']['conteo'] == 0 and s['sin_historial']['conteo'] == 0
    assert s['ok']['conteo'] == 12
