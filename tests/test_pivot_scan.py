import datetime as dt

import pandas as pd
import pytest

from acd.pivot_scan import build_pivot_table, last_completed_session_cutoff
from common.indicators import atr
from common.sessions import standardize_daily


def daily(end="2026-09-18", periods=30, base=100.0):
    idx = pd.bdate_range(end=end, periods=periods)
    rows = [(base + i, base + i + 2, base + i - 1, base + i + 1) for i in range(periods)]
    return standardize_daily(pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"]))


def test_pivot_values_come_from_last_bar_on_or_before_as_of():
    data = {"AAA": daily(end="2026-09-21")}  # has a Monday 09-21 bar
    table, errors = build_pivot_table(data, dt.date(2026, 9, 18))  # ...which must be ignored
    row = table.iloc[0]
    assert not errors and row["source_date"] == "2026-09-18"
    h, l, c = row["prev_high"], row["prev_low"], row["prev_close"]
    assert (h, l, c) == (130.0, 127.0, 129.0)  # the 09-18 bar (index 28 of 30)
    assert row["pivot"] == pytest.approx((h + l + c) / 3)
    assert row["bc"] == pytest.approx((h + l) / 2)
    assert row["pr_low"] <= row["pr_high"]
    assert row["pr_vs_previous_pr"] == "HIGHER"
    history = data["AAA"][data["AAA"].index <= "2026-09-18"]
    for p in (5, 10, 14, 20):
        assert row[f"atr{p}"] == pytest.approx(atr(history, p).iloc[-1])
    assert "pr_width_atr" not in table.columns


def test_atr_is_nan_when_history_too_short():
    table, _ = build_pivot_table({"NEW": daily(periods=8)}, dt.date(2026, 9, 18))
    row = table.iloc[0]
    assert row["atr5"] > 0
    assert pd.isna(row["atr10"]) and pd.isna(row["atr14"]) and pd.isna(row["atr20"])


def test_stale_flag_errors_and_metadata():
    data = {"AAA": daily(), "BBB": daily(), "OLD": daily(end="2026-09-10"), "EMPTY": daily().iloc[0:0]}
    universe = pd.DataFrame({"symbol": ["AAA", "BBB"], "name": ["A Co", "B Co"], "sector": ["Energy", "Energy"]})
    table, errors = build_pivot_table(data, dt.date(2026, 9, 18), universe)
    assert set(table["symbol"]) == {"AAA", "BBB", "OLD"} and "EMPTY" in errors
    assert table.set_index("symbol")["stale"].to_dict() == {"AAA": False, "BBB": False, "OLD": True}
    assert table.set_index("symbol").loc["AAA", "name"] == "A Co"


def test_session_cutoff_before_and_after_close():
    tz = "America/New_York"
    assert last_completed_session_cutoff(pd.Timestamp("2026-09-21 15:00", tz=tz)) == dt.date(2026, 9, 20)
    assert last_completed_session_cutoff(pd.Timestamp("2026-09-21 16:30", tz=tz)) == dt.date(2026, 9, 21)
