"""Scenario tests for the one-day ACD simulator (plan section 18) and anti-lookahead checks.

Synthetic day: OR high 101, OR low 99. With A = 0.5 and C = 0.75:
A-Up 101.5, A-Down 98.5, C-Up 101.75, C-Down 98.25.
"""

import warnings

import pandas as pd
import pytest

from acd.levels import InsufficientDataError
from acd.simulation import LONG, SHORT, ACDConfig, simulate_acd_day
from common.sessions import DataQualityWarning, DataValidationError
from tests.synthetic import DATE, day

PREV = dict(previous_high=102.0, previous_low=98.0, previous_close=101.0)


def cfg(**kw):
    base = dict(a_value=0.5, c_value=0.75, confirmation_minutes=15)
    base.update(kw)
    return ACDConfig(**base)


def run(df, **kw):
    return simulate_acd_day("TEST", DATE, df, config=cfg(**kw), **PREV)


def event_names(result):
    return [e["event"] for e in result.events]


def states(result):
    return [e["detail"].split(" -> ")[1].split(" ")[0] for e in result.events if e["event"] == "STATE_CHANGE"]


def ts(hhmm):
    return pd.Timestamp(f"{DATE} {hhmm}", tz="America/New_York")


# Price paths (minute offsets from 09:30).
BULL = [(30, 100.0), (40, 102.0), (60, 102.5), (389, 103.0)]
BEAR = [(30, 100.0), (40, 98.0), (60, 97.5), (389, 97.0)]


def test_levels_and_pivot():
    r = run(day([(389, 100.0)]))
    assert (r.or_high, r.or_low, r.or_mid) == (101.0, 99.0, 100.0)
    assert (r.a_up, r.a_down, r.c_up, r.c_down) == (101.5, 98.5, 101.75, 98.25)
    assert r.pivot == pytest.approx((102 + 98 + 101) / 3)
    assert r.pivot_low == pytest.approx(100.0)
    assert r.pivot_high == pytest.approx(2 * (301 / 3) - 100)


def test_1_no_trade():
    r = run(day([(30, 100.0), (100, 100.4), (200, 99.6), (389, 100.0)]))
    assert not r.trade_taken
    assert r.pnl == 0 and r.direction is None
    assert states(r) == ["WAITING_FOR_A", "DONE"]


def test_2_successful_a_up():
    r = run(day(BULL))
    assert r.trade_taken and r.direction == LONG
    assert r.entry_price == 101.5
    touch = next(e for e in r.events if e["event"] == "A_UP_TOUCHED")["time"]
    # Confirmation completes 15 minutes after the touch bar started.
    assert r.entry_time == touch + pd.Timedelta(minutes=14)
    assert r.exit_reason == "END_OF_DAY" and r.exit_price == 103.0
    assert r.pnl == pytest.approx(1.5)
    assert r.pnl > 0


def test_3_successful_a_down():
    r = run(day(BEAR))
    assert r.direction == SHORT and r.entry_price == 98.5
    assert r.pnl == pytest.approx(1.5)


def test_4_failed_a_up_confirmation():
    # Pokes above A-Up at 10:10, closes back below on the next bar, drifts at 100.
    r = run(day([(30, 100.0), (40, 101.6), (45, 100.0), (389, 100.0)]))
    names = event_names(r)
    assert "A_UP_TOUCHED" in names and "A_UP_FAILED" in names
    assert "A_UP_CONFIRMED" not in names
    assert not r.trade_taken


def test_5_a_up_then_b_then_c_down():
    path = [(30, 100.0), (40, 102.0), (70, 102.0), (100, 98.0), (130, 97.5), (389, 97.5)]
    r = run(day(path))
    seq = states(r)
    expected = ["WAITING_FOR_A", "A_UP_CANDIDATE", "LONG_A", "WAITING_FOR_C_DOWN", "C_DOWN_CANDIDATE", "SHORT_C", "DONE"]
    assert seq == expected
    long_a, short_c = r.trades
    assert (long_a.setup, long_a.direction, long_a.exit_reason) == ("A", LONG, "B_REACHED")
    assert long_a.exit_price == 99.0 and long_a.pnl == pytest.approx(-2.5)
    assert (short_c.setup, short_c.direction, short_c.entry_price) == ("C", SHORT, 98.25)
    assert short_c.pnl == pytest.approx(98.25 - 97.5)
    assert r.pnl == pytest.approx(-2.5 + 0.75)


def test_6_a_down_then_b_then_c_up():
    path = [(30, 100.0), (40, 98.0), (70, 98.0), (100, 102.0), (130, 102.5), (389, 102.5)]
    r = run(day(path))
    expected = ["WAITING_FOR_A", "A_DOWN_CANDIDATE", "SHORT_A", "WAITING_FOR_C_UP", "C_UP_CANDIDATE", "LONG_C", "DONE"]
    assert states(r) == expected
    short_a, long_c = r.trades
    assert short_a.exit_price == 101.0 and short_a.pnl == pytest.approx(-2.5)
    assert long_c.entry_price == 101.75 and long_c.pnl == pytest.approx(0.75)


def test_5b_c_reversal_without_exit_on_b_closes_long_at_c():
    path = [(30, 100.0), (40, 102.0), (70, 102.0), (100, 98.0), (130, 97.5), (389, 97.5)]
    r = run(day(path), exit_on_b=False)
    long_a, short_c = r.trades
    assert long_a.exit_reason == "REVERSED_AT_C" and long_a.exit_price == 98.25
    assert long_a.exit_time == short_c.entry_time


def test_no_c_reversal_goes_done_after_b():
    path = [(30, 100.0), (40, 102.0), (70, 102.0), (100, 98.0), (389, 97.5)]
    r = run(day(path), allow_c_reversal=False)
    assert len(r.trades) == 1 and r.final_state == "DONE"
    assert "C_DOWN_TOUCHED" not in event_names(r)


def test_7_missing_minute_bars_warn():
    df = day(BULL)
    df = df.drop(df.index[120:126])  # 11:30-11:35 missing
    with pytest.warns(DataQualityWarning, match="6 missing minute bars"):
        r = run(df)
    assert r.warnings and "11:30-11:35" in r.warnings[0]
    assert "DATA_WARNING" in event_names(r)


def test_7b_opening_range_too_sparse_raises():
    df = day(BULL)
    df = df.drop(df.index[0:10])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DataQualityWarning)
        with pytest.raises(InsufficientDataError):
            run(df)


def test_8_end_of_day_forced_close_exact_pnl_with_costs():
    r = run(day(BULL), commission=0.01, slippage=0.02)
    assert r.entry_price == pytest.approx(101.52)
    assert r.exit_time == ts("15:59") and r.exit_price == pytest.approx(102.98)
    assert r.pnl == pytest.approx(102.98 - 101.52 - 0.02)
    assert r.return_pct == pytest.approx(r.pnl / 101.52)


def test_no_forced_close_leaves_position_open():
    r = run(day(BULL), force_close_at_market_close=False)
    assert r.exit_time is None and "POSITION_OPEN_AT_CLOSE" in event_names(r)


@pytest.mark.parametrize("mutate, error", [
    (lambda df: df.iloc[::-1], "not sorted"),
    (lambda df: pd.concat([df, df.iloc[[50]]]).sort_index(), "Duplicate"),
    (lambda df: df.drop(columns=["low"]), "Missing required"),
])
def test_validation_errors(mutate, error):
    with pytest.raises(DataValidationError, match=error):
        run(mutate(day(BULL)))


def test_wrong_date_rejected():
    with pytest.raises(DataValidationError, match="other dates"):
        simulate_acd_day("TEST", "2026-09-17", day(BULL), config=cfg(), **PREV)


def test_naive_timestamp_column_input_is_accepted():
    df = day(BULL)
    flat = df.reset_index()
    flat["timestamp"] = flat["timestamp"].dt.tz_localize(None)
    assert run(flat).pnl == pytest.approx(run(df).pnl)


def test_extended_hours_bars_are_ignored():
    df = day(BULL)
    pre = df.iloc[:5].copy()
    pre.index = pre.index - pd.Timedelta(hours=1)
    pre[["open", "high", "low", "close"]] = 500.0  # would wreck the OR if it leaked in
    r = run(pd.concat([pre, df]))
    assert r.or_high == 101.0


# --- Anti-lookahead ---------------------------------------------------------

def _events_until(result, cutoff):
    return [e for e in result.events if e["time"] <= cutoff and e["event"] not in ("DATA_WARNING",)]


def test_future_bars_do_not_change_past_decisions():
    base = day(BULL)
    original = run(base)
    cutoff = original.entry_time

    crashed = base.copy()
    after = crashed.index > cutoff
    crashed.loc[after, ["open", "high", "low", "close"]] = 90.0  # daily low/close now wildly different
    altered = run(crashed)

    assert _events_until(altered, cutoff) == _events_until(original, cutoff)
    assert altered.entry_time == original.entry_time and altered.entry_price == original.entry_price


def test_truncated_day_reproduces_decisions_so_far():
    """Replaying only the bars up to time t (as in live trading) gives the same decisions."""
    base = day(BULL)
    original = run(base)
    cutoff = original.entry_time
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DataQualityWarning)  # truncated day => missing minutes
        partial = run(base[base.index <= cutoff])
    trading_events = lambda r: [e for e in _events_until(r, cutoff)
                                if e["event"] not in ("STATE_CHANGE", "LONG_EXIT", "SHORT_EXIT")]
    assert trading_events(partial) == trading_events(original)


def test_confirmation_is_not_earlier_than_configured():
    for minutes in (1, 5, 15, 30):
        r = run(day(BULL), confirmation_minutes=minutes)
        touch = next(e for e in r.events if e["event"] == "A_UP_TOUCHED")["time"]
        assert r.entry_time == touch + pd.Timedelta(minutes=minutes - 1)


def test_or_uses_only_or_window():
    base = day(BULL)
    r1 = run(base)
    spiky = base.copy()
    spiky.loc[spiky.index >= ts("10:00"), "high"] += 5  # later highs must not move the OR
    assert run(spiky).or_high == r1.or_high
