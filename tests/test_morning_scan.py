import datetime as dt

import pandas as pd
import pytest

from acd.levels import OpeningRange, calculate_daily_pivot_range, calculate_opening_range
from acd.morning_scan import build_or_table, sort_scan
from acd.signals import or_vs_pivot_range
from tests.synthetic import DATE, day

D = dt.date.fromisoformat(DATE)
# Synthetic day (tests/synthetic.py): the 20-min OR (09:30-09:49) spans 99.2 .. 101.0.


def to_5m(minute: pd.DataFrame) -> pd.DataFrame:
    return minute.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def pivots(symbols, prev=(98.0, 96.0, 97.0), atr=2.0):
    h, l, c = prev
    return pd.DataFrame([{
        "symbol": s, "name": f"{s} Inc", "sector": "Energy", "source_date": "2026-09-17",
        "prev_high": h, "prev_low": l, "prev_close": c, "atr5": atr, "atr10": atr, "atr14": atr, "atr20": atr,
    } for s in symbols])


MINUTE = day([(389, 100.0)])
FIVE = to_5m(MINUTE)


def test_5m_or_equals_1m_or():
    table, missing = build_or_table({"AAA": FIVE}, pivots(["AAA"]), D, or_minutes=20)
    one_min = calculate_opening_range(MINUTE, D, 20)
    row = table.iloc[0]
    assert not missing
    assert (row["or_high"], row["or_low"]) == (one_min.high, one_min.low)
    assert row["or_bars"] == 4 and not row["incomplete_or"]


def test_forming_bar_after_or_end_is_ignored():
    spiky = FIVE.copy()
    spiky.loc[spiky.index[4], "high"] = 200.0  # the 09:50 bar, not part of a 20-min OR
    row = build_or_table({"AAA": spiky}, pivots(["AAA"]), D)[0].iloc[0]
    assert row["or_high"] == pytest.approx(101.0)


def test_missing_bar_flags_incomplete_or():
    gappy = FIVE.drop(FIVE.index[1])
    row = build_or_table({"AAA": gappy}, pivots(["AAA"]), D)[0].iloc[0]
    assert row["incomplete_or"] and row["or_bars"] == 3


def test_side_distance_and_levels():
    # PR from prev (98, 96, 97) is exactly 97 -> the OR (low 99.2) is ABOVE it.
    table, missing = build_or_table({"AAA": FIVE}, pivots(["AAA", "NONE"]), D, a_atr=0.25, c_atr=0.5)
    row = table.iloc[0]
    orng = calculate_opening_range(MINUTE, D, 20)
    assert missing == ["NONE"]
    assert row["or_vs_pr"] == "ABOVE"
    assert row["distance_from_pr"] == pytest.approx(orng.low - 97.0)
    assert row["distance_atr"] == pytest.approx((orng.low - 97.0) / 2.0)
    assert row["a_up"] == pytest.approx(orng.high + 0.5) and row["a_down"] == pytest.approx(orng.low - 0.5)
    assert row["c_up"] == pytest.approx(orng.high + 1.0) and row["c_down"] == pytest.approx(orng.low - 1.0)


def test_all_rows_kept_with_outside_flag():
    bars = {"MID": FIVE, "UP": FIVE, "DOWN": FIVE}
    piv = pd.concat([pivots(["MID"], prev=(101.0, 99.0, 100.0)), pivots(["UP"]),
                     pivots(["DOWN"], prev=(104.0, 102.0, 103.0))])
    table = sort_scan(build_or_table(bars, piv, D)[0])
    assert table["symbol"].tolist() == ["UP", "DOWN", "MID"]  # nothing dropped
    assert table["or_vs_pr"].tolist() == ["ABOVE", "BELOW", "OVERLAPPING"]
    assert table["or_outside_pr"].tolist() == [True, True, False]
    assert table["or_outside_pr"].dtype == bool


def test_or_vs_pivot_range_edges():
    pr = calculate_daily_pivot_range(101, 99, 100)  # 100..100
    ts = pd.Timestamp("2026-09-18 09:30", tz="America/New_York")
    mk = lambda lo, hi: OpeningRange(D, ts, ts, hi, lo, 1, 1)
    assert or_vs_pivot_range(mk(100.5, 102), pr) == "ABOVE"
    assert or_vs_pivot_range(mk(98, 99.5), pr) == "BELOW"
    assert or_vs_pivot_range(mk(100, 102), pr) == "OVERLAPPING"  # touching counts as overlap


def test_interval_must_divide_or():
    with pytest.raises(ValueError):
        build_or_table({}, pivots([]), D, or_minutes=20, interval_minutes=15)
