"""Configuration for the one-day ACD simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from acd.levels import CLevelsFn
from acd.signals import CONFIRMATION_PRICES


@dataclass
class ACDConfig:
    # A and C are explicit numbers so alternative calibrations (fixed, % of ATR, ...)
    # can be tested; see acd.simulation.runner.resolve_a_c_values for ATR-based ones.
    a_value: float
    c_value: float
    # Minutes price must hold beyond A (and C, unless overridden) to confirm.
    confirmation_minutes: int

    opening_range_minutes: int = 30
    market_open: str = "09:30"
    market_close: str = "16:00"

    c_confirmation_minutes: Optional[int] = None  # None -> same as confirmation_minutes
    confirmation_price: str = "close"  # "close" or "full_bar", see acd.signals

    # Placeholders for rules still to be verified against The Logical Trader:
    exit_on_b: bool = True  # close the A position when price crosses point B
    allow_c_reversal: bool = True  # after B, look for the opposite C trade
    allow_multiple_trades: bool = False  # re-arm for a new A once a sequence ends before the close
    force_close_at_market_close: bool = True

    # Data requirements.
    max_missing_or_minutes: int = 5

    # Costs (per share). Commission is charged on entry and exit; slippage is adverse per fill.
    commission: float = 0.0
    slippage: float = 0.0

    c_levels_fn: Optional[CLevelsFn] = None  # None -> OR_HIGH + C / OR_LOW - C

    def __post_init__(self):
        if self.confirmation_price not in CONFIRMATION_PRICES:
            raise ValueError(f"confirmation_price must be one of {CONFIRMATION_PRICES}")
        if self.opening_range_minutes < 1:
            raise ValueError("opening_range_minutes must be >= 1")

    @property
    def effective_c_confirmation_minutes(self) -> int:
        return self.confirmation_minutes if self.c_confirmation_minutes is None else self.c_confirmation_minutes
