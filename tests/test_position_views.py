import numpy as np
import pandas as pd
import pytest

from canslim.signals import base_analysis, market_direction, relative_strength, weighted_performance
from common.sessions import standardize_daily
from turtle_trader.signals import system_state, turtle_view


def daily_from_closes(closes, spread=1.0, volume=1_000_000):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2025-01-02", periods=len(closes), freq="B")
    opens = np.r_[closes[0], closes[:-1]]
    frame = pd.DataFrame({
        "open": opens,
        "high": np.maximum(opens, closes) + spread / 2,
        "low": np.minimum(opens, closes) - spread / 2,
        "close": closes,
        "volume": np.full(len(closes), float(volume)),
    }, index=idx)
    return standardize_daily(frame)


FLAT_THEN_UP = daily_from_closes([100.0] * 60 + [100.0 + 2 * i for i in range(1, 21)])


def test_turtle_enters_long_on_breakout_and_sets_2n_stop():
    state = system_state(FLAT_THEN_UP, 20, 10)
    assert state["position"] == "long"
    # First up day breaks the flat 20-day high of 100.5 (high = 102 + 0.5).
    assert state["entry_date"] == FLAT_THEN_UP.index[60].date().isoformat()
    assert state["entry_price"] == pytest.approx(100.5)
    assert state["stop_2n"] == pytest.approx(state["entry_price"] - 2 * state["n_at_entry"], abs=0.01)
    assert state["add_levels"][0] == pytest.approx(state["entry_price"] + 0.5 * state["n_at_entry"], abs=0.01)


def test_turtle_exits_long_on_10_day_low():
    closes = [100.0] * 60 + [100.0 + 2 * i for i in range(1, 21)] + [140.0 - 3 * i for i in range(1, 11)]
    state = system_state(daily_from_closes(closes), 20, 10)
    # The drop also breaks the 20-day low, so the system may already be short again.
    assert state["position"] != "long"
    assert state["last_exit"]["side"] == "long"
    assert state["last_exit"]["price"] > state["last_exit"]["entry_price"]


def test_turtle_view_unit_size_is_one_percent_risk_per_n():
    view = turtle_view(FLAT_THEN_UP, equity=100_000)
    assert abs(view["unit_shares"] - 1_000 / view["n"]) < 1
    assert set(view["systems"]) == {"S1", "S2"}


def test_turtle_view_needs_enough_history():
    with pytest.raises(ValueError):
        turtle_view(daily_from_closes([100.0] * 30))


def test_base_too_deep_is_not_buyable():
    closes = list(np.linspace(100, 200, 40)) + list(np.linspace(200, 90, 30)) + list(np.linspace(90, 150, 30))
    base = base_analysis(daily_from_closes(closes))
    assert base["buy_point"] == pytest.approx(200.5)
    assert base["base_depth_pct"] > 50
    assert base["status"].startswith("repairing")


def test_sound_base_breakout_is_in_buy_zone():
    closes = list(np.linspace(80, 100, 40)) + list(np.linspace(100, 85, 20)) + list(np.linspace(85, 99, 20)) + [101.0]
    base = base_analysis(daily_from_closes(closes))
    assert base["base_flaws"] == []
    assert 0 <= base["pct_from_buy_point"] <= 5
    assert base["status"] == "in the buy zone"


def test_weighted_performance_reweights_short_history():
    close = pd.Series(np.linspace(100, 110, 64))  # exactly one quarter
    assert weighted_performance(close) == pytest.approx(10.0)
    assert weighted_performance(pd.Series([100.0] * 10)) is None


def test_relative_strength_flags_outperformance():
    stock = daily_from_closes(np.linspace(100, 150, 130))
    bench = daily_from_closes(np.linspace(100, 110, 130))
    rs = relative_strength(stock, bench)
    assert rs["outperforming"] is True
    assert rs["rs_line_at_new_high"] is True


def test_market_direction_counts_distribution_days():
    closes = list(np.linspace(100, 130, 230))
    volumes = [1_000_000.0] * len(closes)
    for i in range(-20, 0, 4):  # five down days on rising volume in the last 25 sessions
        closes[i] = closes[i - 1] * 0.99
        volumes[i] = volumes[i - 1] * 1.5
    frame = daily_from_closes(closes)
    frame["volume"] = volumes
    m = market_direction(frame)
    assert m["distribution_days"] == 5
    assert m["label"] == "uptrend under pressure"
