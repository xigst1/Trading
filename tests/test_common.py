import pandas as pd
import pytest

from common.indicators import atr, atr_as_of, donchian_channel, true_range
from common.market_data import CSVProvider, DataUnavailableError, LocalStore
from common.sessions import standardize_daily
from tests.synthetic import DATE, day


def daily_frame(rows):
    idx = pd.date_range("2026-09-01", periods=len(rows), freq="B")
    return standardize_daily(pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx))


DAILY = daily_frame([
    (10, 11, 9, 10),     # TR 2 (first row: high - low)
    (10, 12, 10, 11),    # TR max(2, 2, 0) = 2
    (13, 14, 12.5, 13),  # gap up: TR = 14 - 11 = 3
    (12, 13, 8, 9),      # TR = 5
    (9, 10, 8.5, 9.5),   # TR max(1.5, 1, 0.5) = 1.5
])


def test_true_range_uses_previous_close():
    assert true_range(DAILY).tolist() == [2, 2, 3, 5, 1.5]


def test_atr_wilder_and_sma():
    wilder = atr(DAILY, period=3)
    assert wilder.iloc[:2].isna().all()
    assert wilder.iloc[2] == pytest.approx((2 + 2 + 3) / 3)
    assert wilder.iloc[3] == pytest.approx((wilder.iloc[2] * 2 + 5) / 3)
    assert atr(DAILY, 3, method="sma").iloc[4] == pytest.approx((3 + 5 + 1.5) / 3)


def test_atr_as_of_excludes_target_date():
    last_date = DAILY.index[-1]
    assert atr_as_of(DAILY, last_date, 3) == pytest.approx(atr(DAILY, 3).iloc[-2])
    assert atr_as_of(DAILY, last_date, 3, include_date=True) == pytest.approx(atr(DAILY, 3).iloc[-1])
    with pytest.raises(ValueError):
        atr_as_of(DAILY, DAILY.index[1], 3)


def test_donchian_excludes_current_bar():
    ch = donchian_channel(DAILY, 2)
    assert ch["upper"].iloc[2] == 12 and ch["lower"].iloc[2] == 9
    assert ch["upper"].iloc[3] == 14


def test_local_store_round_trip_and_previous_day(tmp_path):
    store = LocalStore(tmp_path)
    minute = day([(389, 100.0)])
    store.save_minute("spy", minute)
    store.save_daily("SPY", DAILY)
    provider = CSVProvider(tmp_path)

    loaded = provider.get_minute_data("SPY", DATE)
    pd.testing.assert_frame_equal(loaded, minute, check_freq=False)

    prev = provider.get_previous_day("SPY", "2026-09-05")  # Fri 09-05 -> previous bar is Thu 09-04
    assert (prev.date.isoformat(), prev.high, prev.low, prev.close) == ("2026-09-04", 13, 8, 9)
    assert store.minute_dates("SPY") == [DATE] and store.tickers() == ["SPY"]

    with pytest.raises(DataUnavailableError):
        provider.get_minute_data("SPY", "2026-09-17")


def test_universe_symbols_and_cache(tmp_path):
    from common.universe import load_sp500, sp500_path, sp500_tickers, to_yahoo_symbol

    assert to_yahoo_symbol(" brk.b ") == "BRK-B"
    path = sp500_path(tmp_path)
    path.parent.mkdir(parents=True)
    pd.DataFrame({"symbol": ["AAPL", "XOM"], "sector": ["Information Technology", "Energy"]}).to_csv(path, index=False)
    assert load_sp500(tmp_path, refresh_if_missing=False)["symbol"].tolist() == ["AAPL", "XOM"]
    assert sp500_tickers(tmp_path, sector="energy") == ["XOM"]


def test_sp500_with_fundamentals_ranks_companies(monkeypatch):
    import common.universe as universe

    listing = pd.DataFrame({"symbol": ["AAPL", "GOOG", "GOOGL", "XYZ", "NEW"], "name": list("abcde"),
                            "sector": ["IT", "CS", "CS", "IT", "IT"], "cik": [1, 2, 2, 3, 4]})
    monkeypatch.setattr(universe, "fetch_sp500_list", lambda: listing)

    class FakeProvider:
        def get_fundamentals(self, tickers):
            caps = {"AAPL": 5e12, "GOOG": 4.24e12, "GOOGL": 4.30e12, "XYZ": 1e10, "NEW": None}
            return pd.DataFrame({"symbol": tickers, "market_cap": [caps[t] for t in tickers],
                                 "shares_outstanding": 1.0, "implied_shares_outstanding": 1.0, "float_shares": 1.0,
                                 "avg_volume_3m": 1000.0, "avg_volume_10d": 900.0, "price": 10.0})

    df = universe.fetch_sp500(provider=FakeProvider())
    ranks = dict(zip(df["symbol"], df["market_cap_rank"]))
    # Both Alphabet classes share rank 2; the next company is 3, not 4. No cap -> no rank, listed last.
    assert ranks["AAPL"] == 1 and ranks["GOOG"] == 2 and ranks["GOOGL"] == 2 and ranks["XYZ"] == 3
    assert pd.isna(ranks["NEW"]) and df["symbol"].iloc[-1] == "NEW"
    assert df["avg_dollar_volume_3m"].iloc[0] == 10_000.0
    assert list(df.columns[:4]) == ["symbol", "name", "sector", "market_cap_rank"]


def test_write_table_xlsx_has_filter_and_formats(tmp_path):
    from openpyxl import load_workbook

    from common.excel import write_table_xlsx

    df = pd.DataFrame({"symbol": ["AAA", "BBB"], "or_outside_pr": [True, False], "or_high": [101.256, 99.5]})
    path = write_table_xlsx(df, tmp_path / "scan.xlsx", sheet_name="scan", number_formats={"or_high": "#,##0.00"},
                            column_fills={"or_high": "DDEBF7"})
    ws = load_workbook(path)["scan"]
    assert ws.auto_filter.ref == "A1:C3" and ws.freeze_panes == "B2"
    assert [c.value for c in ws[2]] == ["AAA", True, 101.256]  # booleans stay real booleans for filtering
    assert ws["C2"].number_format == "#,##0.00"
    assert [ws[f"C{r}"].fill.start_color.rgb[-6:] for r in (1, 2, 3)] == ["DDEBF7"] * 3  # header + data
    assert ws["A2"].fill.fill_type is None  # uncolored columns untouched
    pd.testing.assert_frame_equal(pd.read_excel(path), df)


def test_save_minute_keeps_more_complete_file(tmp_path):
    store = LocalStore(tmp_path)
    full = day([(389, 100.0)])
    store.save_minute("SPY", full)
    store.save_minute("SPY", full.iloc[:100])
    assert len(store.load_minute("SPY", DATE)) == len(full)
