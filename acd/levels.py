"""ACD reference levels: opening range, daily pivot range, A and C levels.

Pure calculations - no state, no look-ahead. The opening range only uses bars inside
the OR window; the pivot range only uses the previous session's H/L/C.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Callable, Optional, Tuple

import pandas as pd

from common.sessions import REGULAR_OPEN, DateLike, TimeLike, market_timestamp, parse_date


class InsufficientDataError(ValueError):
    """Not enough bars to build a level (e.g. opening range) reliably."""


@dataclass(frozen=True)
class OpeningRange:
    date: dt.date
    start: pd.Timestamp  # first bar start (inclusive)
    end: pd.Timestamp  # first bar start *after* the OR (exclusive) - trading may begin here
    high: float
    low: float
    bar_count: int
    expected_bars: int

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2

    @property
    def size(self) -> float:
        return self.high - self.low

    @property
    def missing_bars(self) -> int:
        return self.expected_bars - self.bar_count


@dataclass(frozen=True)
class PivotRange:
    """Fisher's daily pivot range from the previous session's high, low and close."""

    pivot: float
    bc: float  # "bottom central" = (H + L) / 2 (may be above tc)
    tc: float  # "top central"    = 2 * pivot - bc

    @property
    def low(self) -> float:
        return min(self.bc, self.tc)

    @property
    def high(self) -> float:
        return max(self.bc, self.tc)

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class ACDLevels:
    or_high: float
    or_low: float
    a_value: float
    c_value: float
    a_up: float
    a_down: float
    c_up: float
    c_down: float

    @property
    def b_for_long(self) -> float:
        """Point B after a confirmed A-Up: the OR low."""
        return self.or_low

    @property
    def b_for_short(self) -> float:
        """Point B after a confirmed A-Down: the OR high."""
        return self.or_high

    def as_dict(self) -> dict:
        return asdict(self)


# (opening_range, c_value) -> (c_up, c_down). Swap in a different formula once the
# exact Fisher definition is validated.
CLevelsFn = Callable[[OpeningRange, float], Tuple[float, float]]


def default_c_levels(opening_range: OpeningRange, c_value: float) -> Tuple[float, float]:
    """V1 placeholder: C_UP = OR_HIGH + C, C_DOWN = OR_LOW - C."""
    return opening_range.high + c_value, opening_range.low - c_value


def calculate_opening_range(
    minute_df: pd.DataFrame,
    target_date: DateLike,
    minutes: int = 30,
    market_open: TimeLike = REGULAR_OPEN,
    max_missing_minutes: Optional[int] = None,
) -> OpeningRange:
    """High/low of the bars starting in ``[open, open + minutes)``.

    For a 30-minute OR that is the 09:30 through 09:59 bars.
    """
    if minutes < 1:
        raise ValueError("opening range minutes must be >= 1")
    date = parse_date(target_date)
    start = market_timestamp(date, market_open)
    end = start + pd.Timedelta(minutes=minutes)
    bars = minute_df[(minute_df.index >= start) & (minute_df.index < end)]
    if bars.empty:
        raise InsufficientDataError(f"No bars inside the opening range {start:%H:%M}-{end:%H:%M} on {date}")
    missing = minutes - len(bars)
    if max_missing_minutes is not None and missing > max_missing_minutes:
        raise InsufficientDataError(
            f"Opening range is missing {missing} of {minutes} bars (max allowed {max_missing_minutes})"
        )
    return OpeningRange(date=date, start=start, end=end, high=float(bars["high"].max()),
                        low=float(bars["low"].min()), bar_count=len(bars), expected_bars=minutes)


def calculate_daily_pivot_range(previous_high: float, previous_low: float, previous_close: float) -> PivotRange:
    pivot = (previous_high + previous_low + previous_close) / 3
    bc = (previous_high + previous_low) / 2
    tc = 2 * pivot - bc
    return PivotRange(pivot=pivot, bc=bc, tc=tc)


def calculate_acd_levels(
    opening_range: OpeningRange,
    a_value: float,
    c_value: float,
    c_levels_fn: Optional[CLevelsFn] = None,
) -> ACDLevels:
    if a_value < 0 or c_value < 0:
        raise ValueError("A and C values must be non-negative")
    c_up, c_down = (c_levels_fn or default_c_levels)(opening_range, c_value)
    return ACDLevels(
        or_high=opening_range.high, or_low=opening_range.low, a_value=a_value, c_value=c_value,
        a_up=opening_range.high + a_value, a_down=opening_range.low - a_value, c_up=c_up, c_down=c_down,
    )


def value_from_atr(atr_value: float, multiplier: float) -> float:
    """Helper for calibrating A/C as a fraction of ATR (e.g. A = 0.10 * ATR14)."""
    return atr_value * multiplier
