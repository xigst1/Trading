"""Fundamentals checklist inspired by Stephanie Link's publicly stated buy/sell criteria.

Link (Chief Investment Strategist, Hightower Advisors) talks about her trades on CNBC's Halftime
Report. This module computes the numbers she cites most; it is an inference from public statements,
not her actual process, and it has no thresholds she has not stated. Recurring themes:

- "I buy quality on sale": a leader that has pulled back (``price_position``).
- Forward P/E against growth (``valuation``).
- Direction of operating margins: rising margins are her most common buy reason, and falling margins
  or stagnant earnings were her stated reason for selling half of Meta (``margin_trend``).
- Revenue and earnings growth, year over year (``yoy_growth``).

Pure functions over yfinance-style inputs (a quarterly income statement with rows such as
"Total Revenue" and dates as columns, the ``info`` dict, and the standard daily OHLCV frame); no
data calls. Every function returns None / "unknown" rather than raising when data is missing.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, Optional, Sequence

import pandas as pd

FLAT_MARGIN_PP = 0.5       # operating-margin change under this (percentage points) counts as flat
PULLBACK_FLAG_PCT = 10.0   # "on sale": at least this far below the 52-week high
YEAR_AGO_DAYS = (345, 385)  # quarters about a year apart (Yahoo often skips quarters)


def _num(value: Any, digits: int = 2) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else round(value, digits)


def statement_row(stmt: Optional[pd.DataFrame], *labels: str) -> Optional[pd.Series]:
    """First matching row of a yfinance statement as a float Series sorted oldest to newest."""
    if stmt is None or stmt.empty:
        return None
    for label in labels:
        if label in stmt.index:
            row = pd.to_numeric(stmt.loc[label], errors="coerce").dropna()
            row.index = pd.to_datetime(row.index)
            return row.sort_index() if not row.empty else None
    return None


def _year_ago_value(series: pd.Series, when: pd.Timestamp) -> Optional[float]:
    lo, hi = (when - dt.timedelta(days=YEAR_AGO_DAYS[1]), when - dt.timedelta(days=YEAR_AGO_DAYS[0]))
    prior = series[(series.index >= lo) & (series.index <= hi)]
    return None if prior.empty else float(prior.iloc[-1])


def yoy_growth(series: Optional[pd.Series]) -> Optional[Dict[str, Any]]:
    """Latest quarter against the quarter about a year earlier, in percent.

    Growth off a negative base uses the absolute value of the old figure, so -0.34 -> -0.09 is
    +73.5% (a smaller loss). Returns None when there is no comparable quarter.
    """
    if series is None or series.empty:
        return None
    when = series.index[-1]
    old = _year_ago_value(series, when)
    if old is None or old == 0:
        return None
    new = float(series.iloc[-1])
    return {"period": when.date().isoformat(), "value": _num(new, 4), "year_ago": _num(old, 4),
            "growth_pct": _num(100 * (new - old) / abs(old), 1)}


def margin_trend(operating_income: Optional[pd.Series], revenue: Optional[pd.Series],
                 flat_pp: float = FLAT_MARGIN_PP) -> Dict[str, Any]:
    """Operating margin by quarter and its direction.

    The label compares the latest quarter with the same quarter a year ago (``basis`` "yoy"); when
    Yahoo has no year-ago quarter it falls back to the previous quarter ("qoq"). A change within
    ``flat_pp`` percentage points is "flat". "unknown" when there is not enough data.
    """
    if operating_income is None or revenue is None:
        return {"label": "unknown"}
    margin = (100 * operating_income / revenue).replace([float("inf"), float("-inf")], float("nan")).dropna().sort_index()
    if len(margin) < 2:
        return {"label": "unknown"}
    latest = float(margin.iloc[-1])
    year_ago = _year_ago_value(margin, margin.index[-1])
    previous = float(margin.iloc[-2])
    basis, reference = ("yoy", year_ago) if year_ago is not None else ("qoq", previous)
    change = latest - reference
    label = "rising" if change > flat_pp else "falling" if change < -flat_pp else "flat"
    return {
        "label": label, "basis": basis, "change_pp": _num(change, 1),
        "latest_pct": _num(latest, 1), "previous_quarter_pct": _num(previous, 1),
        "year_ago_pct": _num(year_ago, 1),
        "history": [{"period": d.date().isoformat(), "margin_pct": _num(v, 1)} for d, v in margin.tail(5).items()],
    }


def valuation(info: Dict[str, Any], growth_pct: Optional[float]) -> Dict[str, Any]:
    """Forward and trailing P/E, forward P/E per point of growth, and upside to the mean target."""
    forward_pe, trailing_pe = _num(info.get("forwardPE")), _num(info.get("trailingPE"))
    price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    target = _num(info.get("targetMeanPrice"))
    ratio = _num(forward_pe / growth_pct) if forward_pe and growth_pct and growth_pct > 0 else None
    return {
        "forward_pe": forward_pe, "trailing_pe": trailing_pe,
        "growth_used_pct": growth_pct, "forward_pe_per_growth_point": ratio,
        "mean_target": target, "target_upside_pct": _num(100 * (target / price - 1), 1) if target and price else None,
        "analysts": info.get("numberOfAnalystOpinions"),
    }


def price_position(daily: pd.DataFrame, lookback: int = 252) -> Dict[str, Any]:
    """How far the stock has pulled back from its high, and where it sits against its averages."""
    window = daily.tail(lookback)
    close, high = float(window["close"].iloc[-1]), float(window["high"].max())
    out: Dict[str, Any] = {
        "close": _num(close), "high_52w": _num(high), "bars": len(window),
        "pullback_from_high_pct": _num(100 * (1 - close / high), 1),
    }
    for period in (50, 200):
        ma = daily["close"].rolling(period, min_periods=period).mean().iloc[-1]
        out[f"pct_vs_ma{period}"] = None if pd.isna(ma) else _num(100 * (close / ma - 1), 1)
    return out


def link_view(daily: pd.DataFrame, info: Dict[str, Any], quarterly: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Everything above plus plain-language flags the agent can reason from."""
    revenue = statement_row(quarterly, "Total Revenue", "Operating Revenue")
    operating = statement_row(quarterly, "Operating Income", "Total Operating Income As Reported")
    eps = statement_row(quarterly, "Diluted EPS", "Basic EPS")
    revenue_growth, eps_growth = yoy_growth(revenue), yoy_growth(eps)
    margins = margin_trend(operating, revenue)
    position = price_position(daily)
    growth = revenue_growth["growth_pct"] if revenue_growth else _num(100 * info["revenueGrowth"]) if info.get("revenueGrowth") is not None else None
    latest_operating = _num(operating.iloc[-1], 0) if operating is not None else None
    flags = {
        "margins_rising": margins["label"] == "rising",
        "margins_falling": margins["label"] == "falling",
        "profitable_latest_quarter": None if latest_operating is None else latest_operating > 0,
        "earnings_not_growing": None if eps_growth is None else eps_growth["growth_pct"] <= 0,
        "pulled_back": position["pullback_from_high_pct"] >= PULLBACK_FLAG_PCT,
    }
    return {
        "as_of": daily.index[-1].date().isoformat(),
        "revenue_growth": revenue_growth, "eps_growth": eps_growth,
        "operating_margin": margins, "latest_operating_income": latest_operating,
        "valuation": valuation(info, growth), "price": position,
        "free_cash_flow": _num(info.get("freeCashflow"), 0), "market_cap": _num(info.get("marketCap"), 0),
        "flags": flags,
    }
