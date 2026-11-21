"""Fase 2: adaptador de precios (cache incremental, ffill, un solo simbolo). Sin red."""
import numpy as np
import pandas as pd
import pytest

from ._helpers import load_lab

P = load_lab()['prices']


def _multi(data):
    """data: {ticker: Series de cierres} -> DataFrame con la forma de yf.download(group_by='ticker')."""
    cols = {}
    for t, s in data.items():
        cols[(t, 'Open')] = s
        cols[(t, 'Close')] = s
    df = pd.DataFrame(cols)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


class FakeDownloader:
    def __init__(self, frame_fn):
        self.frame_fn, self.calls = frame_fn, []

    def __call__(self, tickers, start, end):
        self.calls.append((list(tickers), start, end))
        return self.frame_fn(list(tickers), start, end)


def _series(start, vals):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def test_price_symbol_crypto_suffix():
    assert P.price_symbol('SOL') == 'SOL-USD'
    assert P.price_symbol('sol-usd') == 'SOL-USD'
    assert P.price_symbol('SPY') == 'SPY'
    assert P.price_symbol('LINK') == 'LINK-USD'


def test_one_call_per_batch_and_cache(tmp_path):
    dl = FakeDownloader(lambda t, s, e: _multi({x: _series('2025-01-02', [10, 11, 12]) for x in t}))
    res = P.update_prices(['SPY', 'JPM', 'SOL-USD'], downloader=dl, cache_dir=str(tmp_path),
                          today=pd.Timestamp('2025-01-10').date())
    assert len(dl.calls) == 1 and dl.calls[0][1] == P.START
    assert res['ok'] and res['missing'] == [] and res['last_date'] == '2025-01-06'
    cache = P.read_cache(str(tmp_path))
    assert list(cache.columns) == ['SPY', 'JPM', 'SOL-USD']


def test_incremental_only_asks_from_last_cached_day(tmp_path):
    d1 = FakeDownloader(lambda t, s, e: _multi({x: _series('2025-01-02', [10, 11, 12]) for x in t}))
    P.update_prices(['SPY', 'JPM'], downloader=d1, cache_dir=str(tmp_path),
                    today=pd.Timestamp('2025-01-06').date())
    d2 = FakeDownloader(lambda t, s, e: _multi({x: _series('2025-01-06', [12, 13, 14]) for x in t}))
    P.update_prices(['SPY', 'JPM'], downloader=d2, cache_dir=str(tmp_path),
                    today=pd.Timestamp('2025-01-08').date())
    assert len(d2.calls) == 1 and d2.calls[0][1] == '2024-12-30'  # 06/01 menos 5 habiles
    cache = P.read_cache(str(tmp_path))
    assert cache['SPY'].tolist() == [10, 11, 12, 13, 14]


def test_up_to_date_cache_makes_no_call(tmp_path):
    dl = FakeDownloader(lambda t, s, e: _multi({x: _series('2025-01-02', [10, 11, 12]) for x in t}))
    P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path),
                    today=pd.Timestamp('2025-01-06').date())
    res = P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path),
                          today=pd.Timestamp('2025-01-06').date())
    assert len(dl.calls) == 1 and res['called'] is False


def test_new_ticker_in_existing_cache_fetches_from_start(tmp_path):
    dl = FakeDownloader(lambda t, s, e: _multi({x: _series('2025-01-02', [10, 11, 12]) for x in t}))
    P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path), today=pd.Timestamp('2025-01-06').date())
    P.update_prices(['SPY', 'QQQ'], downloader=dl, cache_dir=str(tmp_path),
                    today=pd.Timestamp('2025-01-06').date())
    assert dl.calls[1][1] == P.START and len(dl.calls) == 2


def test_adjusted_history_rescaled_after_new_dividend(tmp_path):
    P.write_cache(_series('2025-01-02', [10, 11, 12]).to_frame('SPY'), str(tmp_path))
    # nuevo ajuste: el 06/01 ahora vale 11,4 (x0,95) -> la historia vieja baja 5%
    dl = FakeDownloader(lambda t, s, e: _multi({'SPY': _series('2025-01-06', [11.4, 12.0])}))
    P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path), today=pd.Timestamp('2025-01-08').date())
    c = P.read_cache(str(tmp_path))['SPY'].tolist()
    assert c == pytest.approx([9.5, 10.45, 11.4, 12.0])


def _rescale_run(tmp_path, new_vals, new_start='2025-01-06'):
    P.write_cache(_series('2025-01-02', [10, 11, 12, 13]).to_frame('SPY'), str(tmp_path))
    dl = FakeDownloader(lambda t, s, e: _multi({'SPY': _series(new_start, new_vals)}))
    res = P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path), today=pd.Timestamp('2025-01-09').date())
    return res, P.read_cache(str(tmp_path))['SPY'].tolist()


def test_consistent_ratios_over_three_days_rescale(tmp_path):
    # cache 10,11,12,13 (02,03,06,07/01); nuevo desde 02/01 con todo x0,95
    res, c = _rescale_run(tmp_path, [9.5, 10.45, 11.4, 12.35, 13.0], new_start='2025-01-02')
    assert res['warnings'] == []
    assert c == pytest.approx([9.5, 10.45, 11.4, 12.35, 13.0])


def test_rounding_noise_below_threshold_does_not_rescale(tmp_path):
    res, c = _rescale_run(tmp_path, [13.0 * (1 + 5e-5), 14.0], new_start='2025-01-07')
    assert res['warnings'] == []
    assert c[:3] == [10, 11, 12]


def test_inconsistent_ratios_keep_cache_and_warn(tmp_path):
    # solapan 3 dias con ratios 1,0 / 1,0 / 0,5: no se mezcla nada
    res, c = _rescale_run(tmp_path, [11.0, 12.0, 6.5, 14.0], new_start='2025-01-03')
    assert c == [10, 11, 12, 13]
    assert len(res['warnings']) == 1 and res['warnings'][0]['ticker'] == 'SPY'
    assert res['ok'] is True


def test_single_symbol_without_multiindex(tmp_path):
    flat = lambda t, s, e: pd.DataFrame({'Open': _series('2025-01-02', [9, 10]),
                                         'Close': _series('2025-01-02', [10, 11])})
    dl = FakeDownloader(flat)
    res = P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path), today=pd.Timestamp('2025-01-06').date())
    assert res['ok'] and P.read_cache(str(tmp_path))['SPY'].tolist() == [10, 11]


def test_symbol_without_data_is_reported_missing(tmp_path):
    def fn(t, s, e):
        return _multi({'SPY': _series('2025-01-02', [10, 11]),
                       'AVAX-USD': pd.Series([np.nan, np.nan], index=_series('2025-01-02', [1, 1]).index)})
    res = P.update_prices(['SPY', 'AVAX-USD'], downloader=FakeDownloader(fn), cache_dir=str(tmp_path),
                          today=pd.Timestamp('2025-01-06').date())
    assert res['missing'] == ['AVAX-USD']


def test_failed_download_keeps_cache(tmp_path):
    P.write_cache(_series('2025-01-02', [10, 11]).to_frame('SPY'), str(tmp_path))
    def boom(t, s, e):
        raise RuntimeError('sin red')
    res = P.update_prices(['SPY'], downloader=boom, cache_dir=str(tmp_path), today=pd.Timestamp('2025-02-01').date())
    assert res['ok'] is False and 'sin red' in res['error']
    assert P.read_cache(str(tmp_path))['SPY'].tolist() == [10, 11]


def test_align_uses_spy_calendar_and_ffill():
    idx = pd.to_datetime(['2025-01-02', '2025-01-03', '2025-01-04', '2025-01-05', '2025-01-06', '2025-01-07'])
    df = pd.DataFrame({
        'SPY': [100, 101, np.nan, np.nan, 103, 104],           # sin fin de semana
        'SOL-USD': [1, 2, 3, 4, 5, 6],                          # cripto: todos los dias
        'XYZ': [np.nan, 50, np.nan, np.nan, np.nan, 52],        # arranca tarde y con huecos
    }, index=idx)
    dates, px = P.align(df)
    assert dates == ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07']
    assert px['SOL-USD'] == [1, 2, 5, 6]
    assert px['XYZ'] == [None, 50, 50, 52]  # sin dato inicial es None (nunca 0); luego arrastra


def test_align_without_spy_or_empty_is_empty():
    assert P.align(pd.DataFrame()) == ([], {})
    assert P.align(pd.DataFrame({'QQQ': [1.0]}, index=pd.to_datetime(['2025-01-02']))) == ([], {})


def test_tickers_for_includes_indices_and_ledger_crypto_suffix():
    ops = [{'ticker': 'SOL', 'cantidad': 1}, {'ticker': 'JPM', 'cantidad': 1},
           {'ticker': 'PG', 'cantidad': None}]
    t = P.tickers_for(ops)
    assert 'SOL-USD' in t and 'JPM' in t and 'PG' not in t
    assert len(t) == len(set(t)) and set(P.INDEX_TICKERS) <= set(t)


def test_no_overlap_keeps_last_close_and_warns(tmp_path):
    P.write_cache(_series('2025-01-02', [10, 11, 12]).to_frame('SPY'), str(tmp_path))  # hasta 06/01
    dl = FakeDownloader(lambda t, s, e: _multi({'SPY': _series('2025-01-07', [13.0, 14.0])}))
    res = P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path), today=pd.Timestamp('2025-01-09').date())
    assert P.read_cache(str(tmp_path))['SPY'].tolist() == [10, 11, 12, 13, 14]
    assert [w['code'] for w in res['warnings']] == ['no_overlap']
    assert res['warnings'][0]['ticker'] == 'SPY' and res['warnings'][0]['last_cached'] == '2025-01-06'


def test_request_starts_before_oldest_cached_day(tmp_path):
    P.write_cache(_series('2025-01-02', [10, 11, 12, 13, 14, 15, 16, 17]).to_frame('SPY'), str(tmp_path))  # hasta 13/01
    dl = FakeDownloader(lambda t, s, e: _multi({'SPY': _series('2025-01-08', [14.0, 15.0, 16.0, 17.0, 18.0])}))
    res = P.update_prices(['SPY'], downloader=dl, cache_dir=str(tmp_path), today=pd.Timestamp('2025-01-14').date())
    assert len(dl.calls) == 1 and dl.calls[0][1] == '2025-01-06'  # 13/01 menos 5 habiles
    assert res['warnings'] == []
    assert P.read_cache(str(tmp_path))['SPY'].tolist()[-2:] == [17.0, 18.0]
