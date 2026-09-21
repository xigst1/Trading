"""Nightly pivot-range table for a universe of stocks.

For each ticker, take the last *completed* daily bar on or before ``as_of`` and compute
the pivot range that applies to the NEXT trading session. The result is keyed by
``source_date`` (the session the H/L/C came from), so no holiday calendar is needed:
the morning scan uses the newest file whose source_date is before its trading date.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

import pandas as pd

from acd.levels import calculate_daily_pivot_range
from acd.signals import pivot_range_relationship, price_vs_pivot_range
from common.indicators import atr
from common.sessions import MARKET_TZ

COLUMNS = [
    "symbol", "name", "sector", "source_date", "prev_high", "prev_low", "prev_close",
    "pivot", "bc", "tc", "pr_low", "pr_high", "pr_width", "pr_width_pct",
    "atr5", "atr10", "atr14", "atr20",
    "close_vs_pr", "pr_vs_previous_pr", "stale",
]

# Wilder ATRs saved per ticker (as of the source session's close).
ATR_PERIODS = (5, 10, 14, 20)

# Yahoo's daily bar is final a little after the 16:00 close.
DAILY_BAR_FINAL_AFTER = dt.time(16, 15)


def last_completed_session_cutoff(now: Optional[pd.Timestamp] = None) -> dt.date:
    """Latest calendar date whose daily bar can be treated as complete right now.

    After 16:15 ET that is today; before it, yesterday (weekends and holidays simply
    have no bar, so the last bar on or before the cutoff is the last session).
    """
    now = (now or pd.Timestamp.now(MARKET_TZ)).tz_convert(MARKET_TZ)
    if now.time() >= DAILY_BAR_FINAL_AFTER:
        return now.date()
    return now.date() - dt.timedelta(days=1)


def pivot_row(symbol: str, daily: pd.DataFrame, as_of: dt.date) -> Dict:
    history = daily[daily.index <= pd.Timestamp(as_of)]
    if history.empty:
        raise ValueError(f"no daily bars on or before {as_of}")
    last = history.iloc[-1]
    h, l, c = float(last["high"]), float(last["low"]), float(last["close"])
    pr = calculate_daily_pivot_range(h, l, c)

    # NaN when there are not enough bars for that period.
    atrs = {f"atr{p}": float(atr(history, p).iloc[-1]) for p in ATR_PERIODS}

    relationship = None
    if len(history) >= 2:
        p = history.iloc[-2]
        prev_pr = calculate_daily_pivot_range(float(p["high"]), float(p["low"]), float(p["close"]))
        relationship = pivot_range_relationship(prev_pr, pr)

    return {
        "symbol": symbol, "source_date": history.index[-1].date().isoformat(),
        "prev_high": h, "prev_low": l, "prev_close": c,
        "pivot": pr.pivot, "bc": pr.bc, "tc": pr.tc, "pr_low": pr.low, "pr_high": pr.high,
        "pr_width": pr.width, "pr_width_pct": 100 * pr.width / c,
        **atrs,
        "close_vs_pr": price_vs_pivot_range(c, pr), "pr_vs_previous_pr": relationship,
    }


def build_pivot_table(
    daily_by_symbol: Dict[str, pd.DataFrame],
    as_of: dt.date,
    universe: Optional[pd.DataFrame] = None,
) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Returns (table, errors). ``stale`` marks tickers whose last bar is older than
    the most common source_date (halted, delisted, or missing data)."""
    rows: List[Dict] = []
    errors: Dict[str, str] = {}
    for symbol, daily in daily_by_symbol.items():
        try:
            rows.append(pivot_row(symbol, daily, as_of))
        except Exception as exc:
            errors[symbol] = str(exc)
    table = pd.DataFrame(rows)
    if table.empty:
        return pd.DataFrame(columns=COLUMNS), errors

    session = table["source_date"].mode().iloc[0]
    table["stale"] = table["source_date"] != session
    if universe is not None:
        meta = universe[[c for c in ("symbol", "name", "sector") if c in universe.columns]]
        table = table.merge(meta, on="symbol", how="left")
    for col in COLUMNS:
        if col not in table.columns:
            table[col] = None
    return table[COLUMNS].sort_values("symbol").reset_index(drop=True), errors
