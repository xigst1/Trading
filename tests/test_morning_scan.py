import datetime as dt

import pandas as pd
import pytest

from acd.levels import OpeningRange, calculate_daily_pivot_range, calculate_opening_range
from acd.morning_scan import add_size_columns, build_or_table, sort_scan
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


def test_size_columns_joined_in_billions_and_millions():
    table, _ = build_or_table({"AAA": FIVE, "BBB": FIVE}, pivots(["AAA", "BBB"]), D)
    universe = pd.DataFrame({"symbol": ["AAA"], "market_cap_rank": [7], "market_cap": [2.5e12],
                             "shares_outstanding": [1.5e9], "avg_volume_3m": [4.2e7], "avg_volume_10d": [3.9e7]})
    out = add_size_columns(table, universe)
    cols = list(out.columns)
    assert cols[cols.index("sector") + 1: cols.index("sector") + 6] == [
        "market_cap_rank", "mkt_cap_b", "shares_out_m", "avg_vol_3m_m", "avg_vol_10d_m"]
    a = out.set_index("symbol").loc["AAA"]
    assert (a["market_cap_rank"], a["mkt_cap_b"], a["shares_out_m"]) == (7, 2500.0, 1500.0)
    assert (a["avg_vol_3m_m"], a["avg_vol_10d_m"]) == (42.0, 39.0)
    assert out.set_index("symbol").loc["BBB", ["mkt_cap_b", "avg_vol_3m_m"]].isna().all()  # not in universe
    assert len(out) == len(table)  # no rows lost


def test_size_columns_empty_when_universe_has_no_market_cap():
    table, _ = build_or_table({"AAA": FIVE}, pivots(["AAA"]), D)
    out = add_size_columns(table, pd.DataFrame({"symbol": ["AAA"], "name": ["A"]}))
    assert out[["market_cap_rank", "mkt_cap_b", "shares_out_m"]].isna().all().all()


def test_script_output_columns_exist_in_scan_table():
    """Guards the hand-written column order in scripts/acd_morning_or_scan.py against typos."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "acd_morning_or_scan.py"
    spec = importlib.util.spec_from_file_location("acd_morning_or_scan", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    table, _ = build_or_table({"AAA": FIVE}, pivots(["AAA"]), D)
    table = add_size_columns(sort_scan(table), pd.DataFrame({"symbol": ["AAA"]}))
    assert set(script.OUTPUT_COLUMNS) <= set(table.columns)
    assert len(script.OUTPUT_COLUMNS) == len(set(script.OUTPUT_COLUMNS)) == 25
    cols = script.OUTPUT_COLUMNS
    assert cols[cols.index("date") + 1: cols.index("or_outside_pr")] == ["a_up", "a_down", "c_up", "c_down"]


def test_interval_must_divide_or():
    with pytest.raises(ValueError):
        build_or_table({}, pivots([]), D, or_minutes=20, interval_minutes=15)
