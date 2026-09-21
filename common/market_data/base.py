"""Provider interface. Strategies depend on this, never on a concrete data vendor."""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from common.sessions import DateLike, parse_date, previous_row_before


class DataUnavailableError(RuntimeError):
    """The provider has no data for the requested ticker/date."""


@dataclass(frozen=True)
class PreviousDay:
    date: dt.date
    high: float
    low: float
    close: float


class MarketDataProvider(ABC):
    """Returns data in the standard layouts defined in common.sessions."""

    name = "base"

    @abstractmethod
    def get_minute_data(self, ticker: str, date: DateLike) -> pd.DataFrame:
        """1-minute bars for one trading date (standard OHLCV, Eastern tz index)."""

    @abstractmethod
    def get_daily_data(self, ticker: str, start: DateLike, end: DateLike) -> pd.DataFrame:
        """Daily bars with ``start <= date <= end`` (standard daily layout)."""

    def get_previous_day(self, ticker: str, date: DateLike, lookback_days: int = 14) -> PreviousDay:
        """High/low/close of the last completed session strictly before ``date``."""
        target = parse_date(date)
        daily = self.get_daily_data(ticker, target - dt.timedelta(days=lookback_days), target)
        row = previous_row_before(daily, target)
        if row is None:
            raise DataUnavailableError(f"No daily bar for {ticker} before {target}")
        return PreviousDay(date=row.name.date(), high=float(row["high"]), low=float(row["low"]),
                           close=float(row["close"]))
