"""Morning scan: opening range vs last night's pivot range, with A/C levels.

Input: intraday bars per symbol (any bar size that divides the OR length, e.g. 5m for a
20-min OR) and the nightly pivot table (acd.pivot_scan). Output: one row per symbol with
OR, pivot range, A/C levels and whether the whole OR sits ABOVE or BELOW the pivot range.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Tuple

import pandas as pd

from acd.levels import InsufficientDataError, calculate_acd_levels, calculate_daily_pivot_range, calculate_opening_range
from acd.signals import or_vs_pivot_range
from common.sessions import filter_regular_session

COLUMNS = [
    "symbol", "name", "sector", "date", "or_outside_pr", "or_vs_pr",
    "or_high", "or_low", "or_size", "or_bars", "expected_bars", "incomplete_or",
    "pr_low", "pr_high", "a_value", "c_value", "a_up", "a_down", "c_up", "c_down",
    "distance_from_pr", "distance_atr", "atr5", "atr10", "atr14", "atr20", "pivot_source_date",
]


def build_or_table(
    bars_by_symbol: Dict[str, pd.DataFrame],
    pivots: pd.DataFrame,
    date: dt.date,
    or_minutes: int = 20,
    interval_minutes: int = 5,
    a_atr: float = 0.10,
    c_atr: float = 0.15,
    atr_column: str = "atr14",
) -> Tuple[pd.DataFrame, List[str]]:
    """Returns (table for every symbol with an OR, symbols with no OR bars)."""
    if or_minutes % interval_minutes:
        raise ValueError(f"{interval_minutes}-minute bars cannot build a {or_minutes}-minute opening range")
    expected = or_minutes // interval_minutes
    rows, missing = [], []

    for p in pivots.itertuples(index=False):
        bars = bars_by_symbol.get(p.symbol)
        if bars is None or bars.empty:
            missing.append(p.symbol)
            continue
        session = filter_regular_session(bars)
        session = session[session.index.date == date]
        try:
            # Only bars starting before OR end are used, so a still-forming bar is ignored.
            orng = calculate_opening_range(session, date, or_minutes, max_missing_minutes=None)
        except InsufficientDataError:
            missing.append(p.symbol)
            continue
        or_bars = session[(session.index >= orng.start) & (session.index < orng.end)]

        pr = calculate_daily_pivot_range(p.prev_high, p.prev_low, p.prev_close)
        atr_value = float(getattr(p, atr_column))
        levels = calculate_acd_levels(orng, a_atr * atr_value, c_atr * atr_value)
        side = or_vs_pivot_range(orng, pr)
        distance = {"ABOVE": orng.low - pr.high, "BELOW": pr.low - orng.high}.get(side, float("nan"))

        rows.append({
            "symbol": p.symbol, "name": getattr(p, "name", None), "sector": getattr(p, "sector", None),
            "date": date.isoformat(), "or_outside_pr": side in ("ABOVE", "BELOW"), "or_vs_pr": side,
            "or_high": orng.high, "or_low": orng.low, "or_size": orng.size,
            "or_bars": len(or_bars), "expected_bars": expected, "incomplete_or": len(or_bars) < expected,
            "pr_low": pr.low, "pr_high": pr.high,
            "a_value": levels.a_value, "c_value": levels.c_value,
            "a_up": levels.a_up, "a_down": levels.a_down, "c_up": levels.c_up, "c_down": levels.c_down,
            "distance_from_pr": distance, "distance_atr": distance / atr_value if atr_value > 0 else float("nan"),
            "atr5": getattr(p, "atr5", None), "atr10": getattr(p, "atr10", None),
            "atr14": getattr(p, "atr14", None), "atr20": getattr(p, "atr20", None),
            "pivot_source_date": p.source_date,
        })

    table = pd.DataFrame(rows, columns=COLUMNS)
    return table, missing


# sp500.csv column -> (morning scan column, divisor). Values are shown in billions / millions.
SIZE_COLUMNS = {
    "market_cap_rank": ("market_cap_rank", 1),
    "market_cap": ("mkt_cap_b", 1e9),
    "shares_outstanding": ("shares_out_m", 1e6),
    "avg_volume_3m": ("avg_vol_3m_m", 1e6),
    "avg_volume_10d": ("avg_vol_10d_m", 1e6),
}


def add_size_columns(table: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Join market-cap rank, market cap (B), shares outstanding (M) and average daily volume
    (M shares) from the universe file on symbol; placed right after ``sector``. Columns the
    universe file lacks come out empty."""
    size = pd.DataFrame({"symbol": universe["symbol"]})
    for source, (target, divisor) in SIZE_COLUMNS.items():
        size[target] = universe[source] / divisor if source in universe.columns else float("nan")
    out = table.merge(size, on="symbol", how="left")
    targets = [target for target, _ in SIZE_COLUMNS.values()]
    base = [c for c in table.columns if c not in targets]
    at = base.index("sector") + 1
    out = out[base[:at] + targets + base[at:]]
    out["market_cap_rank"] = out["market_cap_rank"].astype("Int64")
    return out


def sort_scan(table: pd.DataFrame) -> pd.DataFrame:
    """All rows kept: ABOVE, then BELOW (each farthest from the PR first), then OVERLAPPING by symbol."""
    order = table["or_vs_pr"].map({"ABOVE": 0, "BELOW": 1, "OVERLAPPING": 2})
    return (table.assign(_order=order)
            .sort_values(["_order", "distance_atr", "symbol"], ascending=[True, False, True], na_position="last")
            .drop(columns="_order").reset_index(drop=True))
