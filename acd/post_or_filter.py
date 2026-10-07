"""Post-OR hold filter: which stocks are beyond their A level right now, and for how long?

Takes the morning scan table (symbol, a_up, a_down, ...) and 5-minute bars. Starting from the
latest bar and going back one bar at a time, it counts how many bars in a row are entirely beyond
the stock's A level on the same side:

    above:  the bar's low  >  a_up      (strict)
    below:  the bar's high <  a_down    (strict)

A stock is kept when that run is at least ``last_bars`` long (default 2), and ``bars_checked`` is
the run's actual length, so a stock beyond its level for 7 bars shows 7. Earlier weakness only
ends the run: a stock that was weak from 09:55 to 11:25 still qualifies at 11:36 if its 11:30 and
11:35 bars are beyond the level. Bars start no earlier than ``start`` (default 09:55 ET: the OR is
09:30-09:50). By default the bar still in progress at ``as_of`` counts, so its result can flip
before it closes (``last_bar_partial`` marks those rows); pass ``include_partial=False`` to use
finished bars only. ``last_bars=0`` requires the run to reach all the way back to ``start``. A
stock whose run stops at a missing bar before reaching ``last_bars`` cannot be confirmed, so it is
reported as a gap and not kept. Pure functions over standard OHLCV frames (see common.sessions);
no data calls.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from common.sessions import (MARKET_TZ, REGULAR_CLOSE, DateLike, TimeLike, filter_regular_session,
                             market_timestamp, parse_date)

ABOVE, BELOW = "above", "below"
DEFAULT_LAST_BARS = 2
ADDED_COLUMNS = ["side", "bars_checked", "first_bar", "last_bar", "last_bar_partial", "worst_extreme",
                 "cushion", "cushion_atr", "last_close"]
_ATR_COLUMN = re.compile(r"^atr\d+$")


def _as_of_timestamp(as_of: Optional[pd.Timestamp]) -> pd.Timestamp:
    ts = pd.Timestamp.now(MARKET_TZ) if as_of is None else pd.Timestamp(as_of)
    return ts.tz_localize(MARKET_TZ) if ts.tzinfo is None else ts.tz_convert(MARKET_TZ)


def expected_starts(date: DateLike, start: TimeLike, interval_minutes: int,
                    as_of: Optional[pd.Timestamp] = None, last_bars: Optional[int] = None,
                    include_partial: bool = True) -> pd.DatetimeIndex:
    """Start times of the bars to test at ``as_of``.

    Bars begin at ``start`` and run on the ``interval_minutes`` grid. With ``include_partial`` the
    bar in progress at ``as_of`` is included; otherwise only bars that have finished. ``last_bars=N``
    keeps the final N of them (``None`` or 0 keeps all). Returns an empty index when there are fewer
    than N bars so far, i.e. the filter is not ready yet.
    """
    step = pd.Timedelta(minutes=interval_minutes)
    first = market_timestamp(date, start)
    close = market_timestamp(date, REGULAR_CLOSE)
    now = _as_of_timestamp(as_of)
    if include_partial:
        if now < first:
            return pd.DatetimeIndex([], tz=MARKET_TZ)
        last = min(first + ((now - first) // step) * step, close - step)
    else:
        last = min(now, close) - step
    if last < first:
        return pd.DatetimeIndex([], tz=MARKET_TZ)
    starts = pd.date_range(first, last, freq=f"{interval_minutes}min")
    if last_bars:
        if len(starts) < last_bars:
            return pd.DatetimeIndex([], tz=MARKET_TZ)
        starts = starts[-last_bars:]
    return starts


def window_bars(bars: pd.DataFrame, date: DateLike, starts: pd.DatetimeIndex) -> pd.DataFrame:
    """Regular-session bars of ``date`` whose start time is in ``starts``."""
    session = filter_regular_session(bars)
    session = session[session.index.date == parse_date(date)]
    session = session[~session.index.duplicated(keep="last")]
    return session[session.index.isin(starts)]


def latest_bar_start(bars_by_symbol: Dict[str, pd.DataFrame], date: DateLike) -> Optional[pd.Timestamp]:
    """Newest bar start on ``date`` across all symbols (shows how fresh the data is)."""
    day = parse_date(date)
    newest = [b.index[b.index.date == day].max() for b in bars_by_symbol.values() if len(b)]
    newest = [t for t in newest if pd.notna(t)]
    return max(newest) if newest else None


def _atr_column(scan: pd.DataFrame) -> Optional[str]:
    return next((c for c in scan.columns if _ATR_COLUMN.match(c)), None)


def evaluate_holds(bars_by_symbol: Dict[str, pd.DataFrame], scan: pd.DataFrame, date: DateLike,
                   start: TimeLike = "09:55", interval_minutes: int = 5,
                   as_of: Optional[pd.Timestamp] = None, last_bars: Optional[int] = DEFAULT_LAST_BARS,
                   include_partial: bool = True) -> Tuple[pd.DataFrame, List[str], List[str]]:
    """Return ``(kept, no_data, gaps)``.

    A stock is kept when its latest bar is entirely beyond its A level and so are the bars before it,
    going back one bar at a time, for at least ``last_bars`` bars in a row (``last_bars=0``: all the
    way back to ``start``). The run stops at the first bar that is not beyond the level on the same
    side, or at ``start``.

    kept     scan rows (all scan columns) plus ADDED_COLUMNS. ``bars_checked`` is the length of that
             run (always >= ``last_bars``), ``first_bar``/``last_bar`` its first and latest bar, and
             ``worst_extreme``/``cushion`` are measured over the run. ABOVE first, then BELOW, each
             ordered by cushion_atr descending.
    no_data  symbols with none of the bars from ``start`` to now.
    gaps     symbols whose run stopped at a missing bar before reaching ``last_bars``, so the
             requirement cannot be confirmed.
    """
    as_of = _as_of_timestamp(as_of)
    expected = expected_starts(date, start, interval_minutes, as_of, None, include_partial)
    need = last_bars or len(expected)
    if expected.empty or len(expected) < need:
        raise ValueError(f"fewer than {max(need, 1)} {interval_minutes}-minute bar(s) from {start} at "
                         f"{as_of:%H:%M} ET yet")
    last_partial = bool(include_partial and expected[-1] + pd.Timedelta(minutes=interval_minutes) > as_of)
    atr_col = _atr_column(scan)

    rows, no_data, gaps = [], [], []
    for rec in scan.to_dict("records"):
        symbol = rec["symbol"]
        bars = bars_by_symbol.get(symbol)
        window = None if bars is None else window_bars(bars, date, expected)
        if window is None or window.empty:
            no_data.append(symbol)
            continue

        a_up, a_down = rec.get("a_up"), rec.get("a_down")
        side, run, hit_gap = None, [], False
        for bar_start in reversed(expected):  # latest bar first
            if bar_start not in window.index:
                hit_gap = True
                break
            bar = window.loc[bar_start]
            if side is None:
                if pd.notna(a_up) and bar["low"] > a_up:
                    side = ABOVE
                elif pd.notna(a_down) and bar["high"] < a_down:
                    side = BELOW
                else:
                    break
            elif (side == ABOVE and not bar["low"] > a_up) or (side == BELOW and not bar["high"] < a_down):
                break
            run.append(bar_start)
        if len(run) < need:
            if hit_gap:
                gaps.append(symbol)
            continue

        streak = window.loc[run[::-1]]
        if side == ABOVE:
            worst, cushion = streak["low"].min(), streak["low"].min() - a_up
        else:
            worst, cushion = streak["high"].max(), a_down - streak["high"].max()
        atr = rec.get(atr_col) if atr_col else None
        rows.append({
            **rec, "side": side, "bars_checked": len(streak),
            "first_bar": f"{streak.index[0]:%H:%M}", "last_bar": f"{streak.index[-1]:%H:%M}",
            "last_bar_partial": last_partial, "worst_extreme": worst, "cushion": cushion,
            "cushion_atr": cushion / atr if atr and pd.notna(atr) and atr > 0 else float("nan"),
            "last_close": streak["close"].iloc[-1],
        })

    kept = pd.DataFrame(rows, columns=list(scan.columns) + ADDED_COLUMNS)
    kept = pd.concat([
        kept[kept["side"] == side].sort_values("cushion_atr", ascending=False, na_position="last")
        for side in (ABOVE, BELOW)
    ], ignore_index=True)
    return kept, no_data, gaps
