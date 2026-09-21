"""Order-execution models, kept separate from the strategy logic.

V1 fills at the theoretical level price. Replace ``LevelExecution`` with a model
using next-bar open, bid/ask or option prices without touching the state machine.
"""

from __future__ import annotations

from typing import Tuple

LONG = "LONG"
SHORT = "SHORT"


class ExecutionModel:
    def entry_price(self, direction: str, level: float, bar) -> float:
        raise NotImplementedError

    def exit_price(self, direction: str, reference_price: float, bar) -> float:
        raise NotImplementedError


class LevelExecution(ExecutionModel):
    """Fill exactly at the level (entries/stops) or reference price (EOD), minus adverse slippage."""

    def __init__(self, slippage: float = 0.0):
        self.slippage = slippage

    def entry_price(self, direction: str, level: float, bar) -> float:
        return level + self.slippage if direction == LONG else level - self.slippage

    def exit_price(self, direction: str, reference_price: float, bar) -> float:
        return reference_price - self.slippage if direction == LONG else reference_price + self.slippage


def calculate_pnl(direction: str, entry_price: float, exit_price: float, commission: float = 0.0) -> Tuple[float, float]:
    """Per-share P&L (after commission on both fills) and return as a fraction of entry."""
    gross = exit_price - entry_price if direction == LONG else entry_price - exit_price
    pnl = gross - 2 * commission
    return pnl, pnl / entry_price
