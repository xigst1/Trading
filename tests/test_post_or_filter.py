import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from acd.post_or_filter import (ABOVE, ADDED_COLUMNS, BELOW, evaluate_holds, expected_starts, latest_bar_start,
                                window_bars)
from common.sessions import MARKET_TZ
from tests.synthetic import DATE

START = "09:55"
AS_OF = pd.Timestamp(f"{DATE} 10:05", tz=MARKET_TZ)  # the 09:55 and 10:00 bars are complete


def bars(rows, first="09:30"):
    """5-minute bars from ``first``; rows are (low, high) pairs, close = midpoint."""
    index = pd.date_range(pd.Timestamp(f"{DATE} {first}", tz=MARKET_TZ), periods=len(rows), freq="5min", name="timestamp")
    low, high = zip(*rows)
    frame = pd.DataFrame({"low": low, "high": high}, index=index)
    frame["open"] = frame["close"] = (frame["low"] + frame["high"]) / 2
    frame["volume"] = 1000.0
    return frame[["open", "high", "low", "close", "volume"]]


# 09:30-09:45 (4 bars) are the 20-minute OR. The 09:50 bar starts when the OR ends but is not
# tested; testing starts with the 09:55 bar. So five bars precede the bars under test.
OR_BARS = [(99.0, 101.0)] * 5


def scan(**levels):
    rows = [{"symbol": s, "name": f"{s} Inc", "sector": "Energy", "a_up": up, "a_down": down, "atr14": 2.0}
            for s, (up, down) in levels.items()]
    return pd.DataFrame(rows)


def run(bars_by_symbol, scan_df, as_of=AS_OF):
    """The original rule: every finished bar since START (last_bars=0, in-progress bar ignored)."""
    return evaluate_holds(bars_by_symbol, scan_df, DATE, START, 5, as_of, last_bars=0, include_partial=False)


def run_recent(bars_by_symbol, scan_df, as_of, **kwargs):
    """The default rule: the most recent 2 bars, including the one in progress."""
    return evaluate_holds(bars_by_symbol, scan_df, DATE, START, 5, as_of, **kwargs)


def at(hhmm):
    return pd.Timestamp(f"{DATE} {hhmm}", tz=MARKET_TZ)


def test_all_bars_above_a_up_is_kept_with_cushion():
    kept, no_data, gaps = run({"AAA": bars(OR_BARS + [(103.0, 104.0), (102.5, 105.0)])}, scan(AAA=(102.0, 98.0)))
    row = kept.iloc[0]
    assert (row["side"], row["bars_checked"], row["first_bar"], row["last_bar"]) == (ABOVE, 2, "09:55", "10:00")
    assert row["worst_extreme"] == 102.5 and row["cushion"] == pytest.approx(0.5)
    assert row["cushion_atr"] == pytest.approx(0.25) and row["last_close"] == pytest.approx(103.75)
    assert not no_data and not gaps


def test_all_bars_below_a_down_is_kept():
    kept, _, _ = run({"BBB": bars(OR_BARS + [(95.0, 96.0), (94.0, 97.0)])}, scan(BBB=(102.0, 98.0)))
    row = kept.iloc[0]
    assert row["side"] == BELOW and row["worst_extreme"] == 97.0 and row["cushion"] == pytest.approx(1.0)


def test_bar_touching_the_level_fails_the_entire_bar_rule():
    touch = bars(OR_BARS + [(103.0, 104.0), (102.0, 105.0)])   # low == a_up
    dip = bars(OR_BARS + [(103.0, 104.0), (101.9, 105.0)])     # low dips under a_up
    kept, _, _ = run({"AAA": touch, "CCC": dip}, scan(AAA=(102.0, 98.0), CCC=(102.0, 98.0)))
    assert kept.empty


def test_default_start_is_0955_and_the_0950_bar_is_not_tested():
    failing_0950 = [(99.0, 101.0)] * 4 + [(90.0, 103.0)]  # the 09:50 bar would fail if it were tested
    data = {"AAA": bars(failing_0950 + [(103.0, 104.0), (103.0, 104.0)])}
    kept, _, _ = evaluate_holds(data, scan(AAA=(102.0, 98.0)), DATE, as_of=AS_OF,  # default start
                                last_bars=0, include_partial=False)
    row = kept.iloc[0]
    assert (row["first_bar"], row["bars_checked"]) == ("09:55", 2)


def test_unfinished_bar_is_ignored_and_later_runs_check_more_bars():
    data = bars(OR_BARS + [(103.0, 104.0), (103.0, 104.0), (90.0, 104.0)])  # the 10:05 bar fails
    levels = scan(AAA=(102.0, 98.0))
    at_1005, _, _ = run({"AAA": data}, levels, as_of=AS_OF)
    at_1007, _, _ = run({"AAA": data}, levels, as_of=AS_OF + pd.Timedelta(minutes=2))
    at_1010, _, _ = run({"AAA": data}, levels, as_of=AS_OF + pd.Timedelta(minutes=5))
    assert len(at_1005) == 1 and len(at_1007) == 1  # the 10:05 bar does not finish until 10:10
    assert at_1010.empty


def test_no_bar_complete_yet_raises():
    with pytest.raises(ValueError):
        run({"AAA": bars(OR_BARS)}, scan(AAA=(102.0, 98.0)), as_of=pd.Timestamp(f"{DATE} 09:59", tz=MARKET_TZ))


def test_one_completed_bar_at_1000():
    kept, _, _ = run({"AAA": bars(OR_BARS + [(103.0, 104.0), (90.0, 104.0)])}, scan(AAA=(102.0, 98.0)),
                     as_of=pd.Timestamp(f"{DATE} 10:00", tz=MARKET_TZ))
    row = kept.iloc[0]
    assert (row["bars_checked"], row["first_bar"], row["last_bar"]) == (1, "09:55", "09:55")


def test_missing_bar_is_reported_as_gap_not_kept():
    data = bars(OR_BARS + [(103.0, 104.0), (103.0, 104.0)]).drop(pd.Timestamp(f"{DATE} 09:55", tz=MARKET_TZ))
    kept, no_data, gaps = run({"AAA": data}, scan(AAA=(102.0, 98.0)))
    assert kept.empty and gaps == ["AAA"] and not no_data


def test_symbol_without_bars_in_window_is_no_data():
    kept, no_data, _ = run({"AAA": bars(OR_BARS)}, scan(AAA=(102.0, 98.0), ZZZ=(102.0, 98.0)))
    assert kept.empty and no_data == ["AAA", "ZZZ"]


def test_scan_columns_survive_and_order_is_above_then_below_by_cushion():
    data = {
        "A1": bars(OR_BARS + [(103.0, 104.0)] * 2),   # cushion 1.0 -> 0.5 ATR
        "A2": bars(OR_BARS + [(105.0, 106.0)] * 2),   # cushion 3.0 -> 1.5 ATR
        "B1": bars(OR_BARS + [(94.0, 96.0)] * 2),     # cushion 2.0 -> 1.0 ATR
    }
    kept, _, _ = run(data, scan(A1=(102.0, 98.0), A2=(102.0, 98.0), B1=(102.0, 98.0)))
    assert list(kept["symbol"]) == ["A2", "A1", "B1"]
    assert list(kept.columns) == list(scan(A1=(1, 0)).columns) + ADDED_COLUMNS
    assert set(kept["name"]) == {"A2 Inc", "A1 Inc", "B1 Inc"}


def test_missing_levels_never_qualify():
    frame = scan(AAA=(float("nan"), float("nan")))
    kept, _, _ = run({"AAA": bars(OR_BARS + [(103.0, 104.0)] * 2)}, frame)
    assert kept.empty


def test_expected_starts_and_window_helpers():
    finished = expected_starts(DATE, START, 5, AS_OF, include_partial=False)
    assert [t.strftime("%H:%M") for t in finished] == ["09:55", "10:00"]
    assert expected_starts(DATE, START, 5, AS_OF - pd.Timedelta(minutes=6), include_partial=False).empty  # 09:59
    assert [t.strftime("%H:%M") for t in expected_starts(DATE, START, 5, AS_OF)] == ["09:55", "10:00", "10:05"]
    assert [t.strftime("%H:%M") for t in expected_starts(DATE, START, 5, at("11:36"), last_bars=2)] == ["11:30", "11:35"]
    assert expected_starts(DATE, START, 5, at("09:57"), last_bars=2).empty
    window = window_bars(bars(OR_BARS + [(103.0, 104.0)] * 3), DATE, finished)
    assert [t.strftime("%H:%M") for t in window.index] == ["09:55", "10:00"]
    assert latest_bar_start({"AAA": bars(OR_BARS + [(103.0, 104.0)] * 3)}, DATE).strftime("%H:%M") == "10:05"
    assert latest_bar_start({}, DATE) is None


WEAK = (95.0, 104.0)    # straddles both A levels: neither above A-Up nor below A-Down
LEVELS = scan(AAA=(102.0, 98.0))


def morning_with_strong_close(strong_rows):
    """09:30-09:50 OR, weak bars 09:55-11:25, then ``strong_rows`` for 11:30 onward."""
    return bars(OR_BARS + [WEAK] * 19 + strong_rows)


def test_only_the_most_recent_two_bars_decide_and_earlier_weakness_is_ignored():
    data = {"AAA": morning_with_strong_close([(103.0, 104.0), (103.5, 105.0)])}  # 11:30 and 11:35 above A-Up
    kept, _, gaps = run_recent(data, LEVELS, at("11:36"))                         # 11:35 is 1 minute old
    row = kept.iloc[0]
    assert (row["side"], row["bars_checked"], row["first_bar"], row["last_bar"]) == (ABOVE, 2, "11:30", "11:35")
    assert bool(row["last_bar_partial"]) is True and row["worst_extreme"] == 103.0 and not gaps
    assert run_recent(data, LEVELS, at("11:36"), last_bars=0)[0].empty            # every bar since 09:55 fails
    assert run_recent(data, LEVELS, at("11:36"), include_partial=False)[0].empty  # 11:25 + 11:30: 11:25 is weak


def test_finished_bars_only_flag_changes_which_bars_are_tested():
    data = {"AAA": morning_with_strong_close([(103.0, 104.0), (103.5, 105.0)])}
    kept, _, _ = run_recent(data, LEVELS, at("11:40"), include_partial=False)     # 11:30 and 11:35 both finished
    row = kept.iloc[0]
    assert (row["first_bar"], row["last_bar"], bool(row["last_bar_partial"])) == ("11:30", "11:35", False)


def test_the_two_recent_bars_must_be_on_the_same_side():
    data = {"AAA": bars(OR_BARS + [(103.0, 104.0), (94.0, 96.0)])}               # 09:55 above, 10:00 below
    assert run_recent(data, LEVELS, at("10:01"))[0].empty


def test_not_enough_bars_yet_raises_and_two_bars_are_enough_at_1000():
    data = {"AAA": bars(OR_BARS + [(103.0, 104.0), (103.0, 104.0)])}
    with pytest.raises(ValueError):
        run_recent(data, LEVELS, at("09:57"))
    kept, _, _ = run_recent(data, LEVELS, at("10:00"))
    assert list(kept["first_bar"]) == ["09:55"] and list(kept["last_bar"]) == ["10:00"]


def test_a_missing_bar_matters_only_if_it_is_one_of_the_tested_bars():
    data = bars(OR_BARS + [(103.0, 104.0)] * 4)                                    # 09:55, 10:00, 10:05, 10:10
    early_gap = data.drop(at("10:00"))
    recent_gap = data.drop(at("10:05"))
    kept, _, gaps = run_recent({"AAA": early_gap}, LEVELS, at("10:12"))            # tests 10:05 and 10:10
    assert list(kept["symbol"]) == ["AAA"] and not gaps
    kept, _, gaps = run_recent({"AAA": recent_gap}, LEVELS, at("10:12"))
    assert kept.empty and gaps == ["AAA"]


def test_bars_checked_is_the_length_of_the_run_back_from_the_latest_bar():
    strong = [(103.0, 104.0), (104.0, 105.0), (105.0, 106.0), (106.0, 107.0)]
    data = {"AAA": bars(OR_BARS + [WEAK] + strong)}          # 09:55 weak, then 10:00-10:15 above A-Up
    kept, _, _ = run_recent(data, LEVELS, at("10:17"))
    row = kept.iloc[0]
    assert (row["bars_checked"], row["first_bar"], row["last_bar"]) == (4, "10:00", "10:15")
    assert row["worst_extreme"] == 103.0 and row["cushion"] == pytest.approx(1.0)  # measured over the run only


def test_minimum_run_length_and_every_kept_row_meets_it():
    data = {
        "AAA": bars(OR_BARS + [WEAK, WEAK, (103.0, 104.0), (103.0, 104.0)]),                        # run of 2
        "BBB": bars(OR_BARS + [WEAK, (103.0, 104.0), (103.0, 104.0), (103.0, 104.0)]),              # run of 3
        "CCC": bars(OR_BARS + [(103.0, 104.0), (103.0, 104.0), (103.0, 104.0), (103.0, 104.0)]),    # run of 4
    }
    levels = scan(AAA=(102.0, 98.0), BBB=(102.0, 98.0), CCC=(102.0, 98.0))
    default, _, _ = run_recent(data, levels, at("10:12"))
    three, _, _ = run_recent(data, levels, at("10:12"), last_bars=3)
    assert dict(zip(default["symbol"], default["bars_checked"])) == {"AAA": 2, "BBB": 3, "CCC": 4}
    assert (default["bars_checked"] >= 2).all()
    assert sorted(three["symbol"]) == ["BBB", "CCC"] and (three["bars_checked"] >= 3).all()


def test_last_bars_zero_requires_the_run_to_reach_back_to_start():
    start_to_now = bars(OR_BARS + [(103.0, 104.0)] * 4)
    broken = bars(OR_BARS + [WEAK] + [(103.0, 104.0)] * 3)
    levels = scan(AAA=(102.0, 98.0), BBB=(102.0, 98.0))
    kept, _, _ = run_recent({"AAA": start_to_now, "BBB": broken}, levels, at("10:12"), last_bars=0)
    assert list(kept["symbol"]) == ["AAA"] and kept.iloc[0]["bars_checked"] == 4


def test_a_missing_bar_ends_the_run_and_the_stock_is_kept_only_if_the_run_is_long_enough():
    data = bars(OR_BARS + [(103.0, 104.0)] * 4).drop(at("10:05"))   # 09:55, 10:00, [10:05 missing], 10:10
    kept, _, gaps = run_recent({"AAA": data}, LEVELS, at("10:12"))
    assert kept.empty and gaps == ["AAA"]                           # run of 1 (10:10) < 2
    kept, _, gaps = run_recent({"AAA": data}, LEVELS, at("10:12"), last_bars=1)
    assert list(kept["symbol"]) == ["AAA"] and kept.iloc[0]["bars_checked"] == 1 and not gaps


def test_script_loads_and_columns_exist_in_result():
    path = Path(__file__).resolve().parent.parent / "scripts" / "acd_post_or_filter.py"
    spec = importlib.util.spec_from_file_location("acd_post_or_filter_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    kept, _, _ = run({"AAA": bars(OR_BARS + [(103.0, 104.0)] * 2)}, scan(AAA=(102.0, 98.0)))
    assert set(module.LEADING_COLUMNS) - {"market_cap_rank", "mkt_cap_b", "shares_out_m", "avg_vol_3m_m"} <= set(kept.columns)
    assert set(ADDED_COLUMNS) <= set(kept.columns)
