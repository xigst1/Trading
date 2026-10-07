import numpy as np
import pandas as pd
import pytest

from common.sessions import standardize_daily
from link_style.signals import link_view, margin_trend, price_position, statement_row, valuation, yoy_growth

Q = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


def statement(**rows):
    """yfinance-style quarterly statement: row labels as index, quarter-end dates as columns (newest first)."""
    frame = pd.DataFrame(rows, index=pd.to_datetime(Q)).T
    return frame[sorted(frame.columns, reverse=True)]


def series(values, dates=Q):
    return pd.Series(values, index=pd.to_datetime(dates), dtype=float)


def daily_from_closes(closes):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2025-01-02", periods=len(closes), freq="B")
    frame = pd.DataFrame({"open": closes, "high": closes + 1, "low": closes - 1, "close": closes,
                          "volume": 1_000_000.0}, index=idx)
    return standardize_daily(frame)


def test_statement_row_sorts_oldest_first_and_falls_back_to_second_label():
    stmt = statement(**{"Total Revenue": [1, 2, 3, 4, 5, 6]})
    row = statement_row(stmt, "Operating Revenue", "Total Revenue")
    assert row.index.is_monotonic_increasing and row.iloc[-1] == 6
    assert statement_row(stmt, "Nope") is None and statement_row(None, "Total Revenue") is None


def test_yoy_growth_matches_the_quarter_a_year_earlier():
    revenue = series([100, 110, 120, 130, 140, 150])
    growth = yoy_growth(revenue)
    assert growth["period"] == "2026-06-30" and growth["year_ago"] == 110 and growth["growth_pct"] == pytest.approx(36.4)


def test_yoy_growth_from_a_loss_uses_the_absolute_base_and_handles_missing_data():
    assert yoy_growth(series([-0.05, -0.34, 0, 0, -0.41, -0.09]))["growth_pct"] == pytest.approx(73.5)
    assert yoy_growth(series([1.0, 2.0], dates=["2026-03-31", "2026-06-30"])) is None  # no year-ago quarter
    assert yoy_growth(series([0.0, 5.0], dates=["2025-06-30", "2026-06-30"])) is None  # zero base
    assert yoy_growth(None) is None


def test_margin_trend_rising_falling_and_flat_against_the_year_ago_quarter():
    revenue = series([100] * 6)
    rising = margin_trend(series([10, 10, 10, 10, 10, 15]), revenue)
    falling = margin_trend(series([12, 12, 12, 12, 12, 8]), revenue)
    flat = margin_trend(series([10, 10, 10, 10, 10, 10.3]), revenue)
    assert (rising["label"], rising["basis"], rising["change_pp"]) == ("rising", "yoy", 5.0)
    assert (falling["label"], falling["change_pp"]) == ("falling", -4.0)
    assert flat["label"] == "flat"
    assert margin_trend(series([10] * 6), revenue)["history"][-1] == {"period": "2026-06-30", "margin_pct": 10.0}


def test_margin_trend_falls_back_to_the_previous_quarter_without_a_year_ago_quarter():
    dates = ["2026-03-31", "2026-06-30"]
    result = margin_trend(series([10, 6], dates), series([100, 100], dates))
    assert (result["label"], result["basis"], result["change_pp"]) == ("falling", "qoq", -4.0)


def test_margin_trend_unknown_when_data_is_missing_or_too_short():
    assert margin_trend(None, series([100] * 6)) == {"label": "unknown"}
    assert margin_trend(series([5.0], ["2026-06-30"]), series([100.0], ["2026-06-30"])) == {"label": "unknown"}
    zero_revenue = margin_trend(series([5, 5], ["2026-03-31", "2026-06-30"]), series([0, 0], ["2026-03-31", "2026-06-30"]))
    assert zero_revenue == {"label": "unknown"}


def test_valuation_forward_pe_per_growth_point_and_target_upside():
    info = {"forwardPE": 24.0, "trailingPE": 30.0, "currentPrice": 100.0, "targetMeanPrice": 125.0,
            "numberOfAnalystOpinions": 20}
    result = valuation(info, 12.0)
    assert result["forward_pe_per_growth_point"] == pytest.approx(2.0) and result["target_upside_pct"] == 25.0
    assert valuation(info, -3.0)["forward_pe_per_growth_point"] is None      # shrinking: no ratio
    assert valuation({}, None)["forward_pe"] is None                          # missing fields are null, never an error
    assert valuation({"forwardPE": float("nan")}, 10.0)["forward_pe"] is None


def test_price_position_pullback_and_moving_averages():
    closes = list(np.linspace(100, 200, 200)) + list(np.linspace(200, 185, 50))
    position = price_position(daily_from_closes(closes))
    assert position["high_52w"] == 201.0 and position["pullback_from_high_pct"] == pytest.approx(8.0, abs=0.1)
    assert position["pct_vs_ma50"] < 0 < position["pct_vs_ma200"]  # below the 50-day, still above the 200-day
    short = price_position(daily_from_closes([100.0] * 30))
    assert short["pct_vs_ma50"] is None and short["pct_vs_ma200"] is None


def test_link_view_flags_for_a_pulled_back_name_with_rising_margins():
    stmt = statement(**{"Total Revenue": [100, 105, 110, 120, 130, 140], "Operating Income": [10, 10, 11, 13, 15, 21],
                        "Diluted EPS": [1.0, 1.0, 1.1, 1.2, 1.3, 1.5]})
    daily = daily_from_closes(list(np.linspace(100, 200, 200)) + list(np.linspace(200, 170, 50)))
    view = link_view(daily, {"forwardPE": 20.0, "currentPrice": 170.0, "targetMeanPrice": 200.0}, stmt)
    assert view["revenue_growth"]["growth_pct"] == pytest.approx(33.3, abs=0.1)
    assert view["eps_growth"]["growth_pct"] == pytest.approx(50.0)
    assert view["valuation"]["forward_pe_per_growth_point"] == pytest.approx(0.6, abs=0.01)
    flags = view["flags"]
    assert flags["margins_rising"] and not flags["margins_falling"] and flags["profitable_latest_quarter"]
    assert flags["earnings_not_growing"] is False and flags["pulled_back"] is True


def test_link_view_flags_falling_margins_and_stagnant_earnings():
    stmt = statement(**{"Total Revenue": [100] * 6, "Operating Income": [20, 20, 20, 20, 20, 12],
                        "Diluted EPS": [1.0, 1.0, 1.0, 1.0, 1.0, 0.9]})
    view = link_view(daily_from_closes(np.linspace(100, 102, 250)), {}, stmt)
    assert view["flags"]["margins_falling"] and view["flags"]["earnings_not_growing"] is True
    assert view["flags"]["pulled_back"] is False


def test_link_view_survives_missing_statement_and_info():
    view = link_view(daily_from_closes(np.linspace(100, 110, 60)), {}, None)
    assert view["operating_margin"] == {"label": "unknown"}
    assert view["revenue_growth"] is None and view["eps_growth"] is None
    assert view["flags"]["profitable_latest_quarter"] is None and view["flags"]["earnings_not_growing"] is None
    assert view["valuation"]["forward_pe"] is None
