import pandas as pd
import pytest

from acd.levels import (InsufficientDataError, calculate_acd_levels, calculate_daily_pivot_range,
                        calculate_opening_range)
from acd.signals import (CONFIRMED, DOWN, FAILED, PENDING, UP, confirm_level_hold, detect_level_touch,
                         pivot_range_relationship, price_vs_pivot_range)
from tests.synthetic import DATE, bars_from_closes, day


def test_opening_range_30_minutes_uses_0930_to_0959():
    df = day([(389, 100.0)])
    orng = calculate_opening_range(df, DATE, 30)
    assert (orng.high, orng.low, orng.mid, orng.size) == (101.0, 99.0, 100.0, 2.0)
    assert orng.start.strftime("%H:%M") == "09:30" and orng.end.strftime("%H:%M") == "10:00"
    assert orng.bar_count == 30


def test_opening_range_other_length():
    df = day([(389, 100.0)])
    orng = calculate_opening_range(df, DATE, 10)  # 09:30-09:39 rises 100 -> 100.9
    assert orng.low == 100.0 and orng.high == pytest.approx(100.9)


def test_opening_range_missing_bars():
    df = day([(389, 100.0)]).iloc[8:]
    with pytest.raises(InsufficientDataError):
        calculate_opening_range(df, DATE, 30, max_missing_minutes=5)
    assert calculate_opening_range(df, DATE, 30).missing_bars == 8


def test_pivot_range_orders_bc_tc():
    # Close near the high: tc above bc.
    pr = calculate_daily_pivot_range(110, 100, 109)
    assert pr.pivot == pytest.approx(106.3333, abs=1e-4)
    assert pr.bc == 105 and pr.tc == pytest.approx(107.6667, abs=1e-4)
    assert (pr.low, pr.high) == (pr.bc, pr.tc)
    # Close near the low: tc falls below bc, low/high still sorted.
    pr2 = calculate_daily_pivot_range(110, 100, 101)
    assert pr2.tc < pr2.bc and pr2.low == pr2.tc and pr2.high == pr2.bc


def test_acd_levels_and_custom_c_formula():
    orng = calculate_opening_range(day([(389, 100.0)]), DATE, 30)
    lv = calculate_acd_levels(orng, 0.5, 0.75)
    assert (lv.a_up, lv.a_down, lv.c_up, lv.c_down) == (101.5, 98.5, 101.75, 98.25)
    assert (lv.b_for_long, lv.b_for_short) == (99.0, 101.0)
    custom = calculate_acd_levels(orng, 0.5, 0.75, c_levels_fn=lambda o, c: (o.mid + c, o.mid - c))
    assert (custom.c_up, custom.c_down) == (100.75, 99.25)


def test_detect_touch():
    assert detect_level_touch(high=10.0, low=9.0, level=10.0, direction=UP)
    assert not detect_level_touch(high=9.99, low=9.0, level=10.0, direction=UP)
    assert detect_level_touch(high=10.0, low=9.0, level=9.0, direction=DOWN)


def test_confirm_level_hold_states():
    bars = bars_from_closes([10.1, 10.2, 10.3, 9.9])
    assert confirm_level_hold(bars.iloc[:2], 10.0, UP, 3).status == PENDING
    ok = confirm_level_hold(bars.iloc[:3], 10.0, UP, 3)
    assert ok.status == CONFIRMED and ok.confirmation_time == bars.index[2] and ok.minutes_held == 3
    bad = confirm_level_hold(bars, 10.0, UP, 5)
    assert bad.status == FAILED and bad.failure_time == bars.index[3]


def test_confirm_counts_elapsed_time_across_missing_bars():
    full = bars_from_closes([10.1] * 6)
    bars = full.drop(index=full.index[2:4])  # 09:32 and 09:33 missing
    result = confirm_level_hold(bars, 10.0, UP, 5)
    # 09:30 bar start -> end of the 09:34 bar is 5 minutes, even though only 3 bars exist.
    assert result.confirmed and result.confirmation_time == full.index[4] and result.minutes_held == 5


def test_confirm_full_bar_is_stricter_than_close():
    bars = bars_from_closes([9.95, 10.1, 10.2])  # bar 1 opens at 9.95 (below level) but closes above
    window = bars.iloc[1:]
    assert confirm_level_hold(window, 10.0, UP, 2, price="close").confirmed
    assert confirm_level_hold(window, 10.0, UP, 2, price="full_bar").failed


def test_context_signals():
    prev = calculate_daily_pivot_range(110, 100, 105)
    higher = calculate_daily_pivot_range(120, 112, 118)
    assert pivot_range_relationship(prev, higher) == "HIGHER"
    assert pivot_range_relationship(higher, prev) == "LOWER"
    assert price_vs_pivot_range(200, prev) == "ABOVE"
    assert price_vs_pivot_range(prev.pivot, prev) == "INSIDE"
