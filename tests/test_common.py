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


def test_save_minute_keeps_more_complete_file(tmp_path):
    store = LocalStore(tmp_path)
    full = day([(389, 100.0)])
    store.save_minute("SPY", full)
    store.save_minute("SPY", full.iloc[:100])
    assert len(store.load_minute("SPY", DATE)) == len(full)
