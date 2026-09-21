"""ACD signal primitives: level touches, confirmation rules and context signals.

Confirmation functions receive only the bars from the touch bar through the *current*
bar, so they cannot see the future. Swap the rule here (not in the simulator loop)
once Fisher's exact definition is pinned down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from acd.levels import PivotRange

UP = "UP"
DOWN = "DOWN"

PENDING = "PENDING"
CONFIRMED = "CONFIRMED"
FAILED = "FAILED"

# Which price must stay beyond the level for a bar to count as "holding":
#   "close"    - the bar closes at/beyond the level (default)
#   "full_bar" - the whole bar is beyond it (low for UP, high for DOWN)
CONFIRMATION_PRICES = ("close", "full_bar")


@dataclass(frozen=True)
class ConfirmationResult:
    status: str  # PENDING | CONFIRMED | FAILED
    level: float
    direction: str
    touch_time: pd.Timestamp
    confirmation_time: Optional[pd.Timestamp] = None
    failure_time: Optional[pd.Timestamp] = None
    minutes_held: int = 0

    @property
    def confirmed(self) -> bool:
        return self.status == CONFIRMED

    @property
    def failed(self) -> bool:
        return self.status == FAILED


def detect_level_touch(high: float, low: float, level: float, direction: str) -> bool:
    """UP: bar high reaches/crosses the level. DOWN: bar low reaches/crosses it."""
    if direction == UP:
        return high >= level
    if direction == DOWN:
        return low <= level
    raise ValueError(f"direction must be {UP} or {DOWN}")


def _holding(bar, level: float, direction: str, price: str) -> bool:
    if price == "close":
        value = bar.close
    elif price == "full_bar":
        value = bar.low if direction == UP else bar.high
    else:
        raise ValueError(f"confirmation price must be one of {CONFIRMATION_PRICES}")
    return value >= level if direction == UP else value <= level


def confirm_level_hold(
    bars_since_touch: pd.DataFrame,
    level: float,
    direction: str,
    confirmation_minutes: int,
    price: str = "close",
) -> ConfirmationResult:
    """Time-based confirmation: price must hold beyond ``level`` for ``confirmation_minutes``.

    ``bars_since_touch`` starts at the touch bar and ends at the current bar. Each bar
    must be "holding" (see CONFIRMATION_PRICES); the first bar that is not fails the
    candidate. Confirmation happens on the bar that completes ``confirmation_minutes``
    of elapsed time since the touch bar started (touch bar included), so a missing bar
    does not stretch the window. ``confirmation_minutes <= 0`` confirms on the touch itself.
    """
    if bars_since_touch.empty:
        raise ValueError("bars_since_touch must include at least the touch bar")
    touch_time = bars_since_touch.index[0]
    if confirmation_minutes <= 0:
        return ConfirmationResult(CONFIRMED, level, direction, touch_time, confirmation_time=touch_time)

    for bar in bars_since_touch.itertuples():
        ts = bar.Index
        if not _holding(bar, level, direction, price):
            return ConfirmationResult(FAILED, level, direction, touch_time, failure_time=ts,
                                      minutes_held=_elapsed_minutes(touch_time, ts) - 1)
        held = _elapsed_minutes(touch_time, ts)
        if held >= confirmation_minutes:
            return ConfirmationResult(CONFIRMED, level, direction, touch_time, confirmation_time=ts,
                                      minutes_held=held)
    last = bars_since_touch.index[-1]
    return ConfirmationResult(PENDING, level, direction, touch_time, minutes_held=_elapsed_minutes(touch_time, last))


def _elapsed_minutes(touch_time: pd.Timestamp, bar_time: pd.Timestamp) -> int:
    """Minutes from the touch bar's start to the *end* of ``bar_time``'s bar."""
    return int((bar_time - touch_time) / pd.Timedelta(minutes=1)) + 1


def confirm_a_up(bars_since_touch, level, confirmation_minutes, price="close") -> ConfirmationResult:
    return confirm_level_hold(bars_since_touch, level, UP, confirmation_minutes, price)


def confirm_a_down(bars_since_touch, level, confirmation_minutes, price="close") -> ConfirmationResult:
    return confirm_level_hold(bars_since_touch, level, DOWN, confirmation_minutes, price)


def confirm_c_up(bars_since_touch, level, confirmation_minutes, price="close") -> ConfirmationResult:
    return confirm_level_hold(bars_since_touch, level, UP, confirmation_minutes, price)


def confirm_c_down(bars_since_touch, level, confirmation_minutes, price="close") -> ConfirmationResult:
    return confirm_level_hold(bars_since_touch, level, DOWN, confirmation_minutes, price)


# --- Context signals (informational only in V1; they never change trade decisions) ---

def price_vs_pivot_range(price: float, pivot_range: PivotRange) -> str:
    """ABOVE / INSIDE / BELOW the pivot range."""
    if price > pivot_range.high:
        return "ABOVE"
    if price < pivot_range.low:
        return "BELOW"
    return "INSIDE"


def pivot_range_relationship(previous: PivotRange, current: PivotRange) -> str:
    """How today's pivot range sits relative to yesterday's.

    HIGHER / LOWER: no overlap, shifted up/down. OVERLAPPING_HIGHER / OVERLAPPING_LOWER:
    overlap with a higher/lower midpoint. INSIDE: contained in yesterday's range.
    OUTSIDE: contains yesterday's range.
    """
    if current.low > previous.high:
        return "HIGHER"
    if current.high < previous.low:
        return "LOWER"
    if current.low >= previous.low and current.high <= previous.high:
        return "INSIDE"
    if current.low <= previous.low and current.high >= previous.high:
        return "OUTSIDE"
    return "OVERLAPPING_HIGHER" if current.pivot > previous.pivot else "OVERLAPPING_LOWER"
