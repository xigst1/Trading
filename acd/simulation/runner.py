"""Glue between a MarketDataProvider and the pure ACD simulator.

This is the only ACD module that knows about data providers; the simulator itself
takes a minute DataFrame plus previous-day H/L/C.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from acd.levels import (ACDLevels, OpeningRange, PivotRange, calculate_acd_levels, calculate_daily_pivot_range,
                        calculate_opening_range, value_from_atr)
from acd.signals import pivot_range_relationship, price_vs_pivot_range
from acd.simulation.config import ACDConfig
from acd.simulation.simulator import ACDDayResult, simulate_acd_day
from common.indicators import atr_as_of
from common.market_data import MarketDataProvider, PreviousDay
from common.sessions import REGULAR_OPEN, DateLike, filter_regular_session, market_timestamp, parse_date


@dataclass
class ACDInputs:
    minute_data: pd.DataFrame
    previous_day: PreviousDay
    daily: pd.DataFrame  # daily history strictly before the target date
    atr: Optional[float]


def load_acd_inputs(provider: MarketDataProvider, ticker: str, date: DateLike, atr_period: int = 14,
                    history_days: int = 120) -> ACDInputs:
    target = parse_date(date)
    minute = provider.get_minute_data(ticker, target)
    daily = provider.get_daily_data(ticker, target - dt.timedelta(days=history_days), target)
    daily = daily[daily.index < pd.Timestamp(target)]
    if daily.empty:
        raise ValueError(f"No daily history for {ticker} before {target}")
    last = daily.iloc[-1]
    prev = PreviousDay(date=last.name.date(), high=float(last["high"]), low=float(last["low"]),
                       close=float(last["close"]))
    try:
        atr_value = atr_as_of(daily, target, atr_period)
    except ValueError:
        atr_value = None
    return ACDInputs(minute_data=minute, previous_day=prev, daily=daily, atr=atr_value)


def resolve_value(fixed: Optional[float], atr_multiple: Optional[float], atr_value: Optional[float], name: str) -> float:
    """Either an explicit value or ``atr_multiple * ATR`` (ATR known before the open)."""
    if fixed is not None:
        return float(fixed)
    if atr_multiple is None:
        raise ValueError(f"Provide either {name}_value or {name}_atr_multiple")
    if atr_value is None:
        raise ValueError(f"Not enough daily history to compute ATR for {name}_atr_multiple")
    return value_from_atr(atr_value, atr_multiple)


def run_acd_day(
    provider: MarketDataProvider,
    ticker: str,
    date: DateLike,
    confirmation_minutes: int,
    a_value: Optional[float] = None,
    c_value: Optional[float] = None,
    a_atr_multiple: Optional[float] = None,
    c_atr_multiple: Optional[float] = None,
    atr_period: int = 14,
    **config_kwargs,
) -> tuple:
    """Load data, resolve A/C and simulate. Returns (ACDDayResult, ACDInputs, ACDConfig)."""
    inputs = load_acd_inputs(provider, ticker, date, atr_period)
    config = ACDConfig(
        a_value=resolve_value(a_value, a_atr_multiple, inputs.atr, "a"),
        c_value=resolve_value(c_value, c_atr_multiple, inputs.atr, "c"),
        confirmation_minutes=confirmation_minutes,
        **config_kwargs,
    )
    prev = inputs.previous_day
    result: ACDDayResult = simulate_acd_day(ticker, date, inputs.minute_data, prev.high, prev.low, prev.close, config)
    return result, inputs, config


@dataclass
class DayLevels:
    ticker: str
    date: dt.date
    previous_day: PreviousDay
    pivot_range: PivotRange
    previous_pivot_range: Optional[PivotRange]
    opening_range: Optional[OpeningRange]
    levels: Optional[ACDLevels]
    atr: Optional[float]
    open_price: Optional[float]

    def summary(self) -> dict:
        pr = self.pivot_range
        out = {
            "ticker": self.ticker, "date": self.date.isoformat(),
            "previous_day": {"date": self.previous_day.date.isoformat(), "high": self.previous_day.high,
                             "low": self.previous_day.low, "close": self.previous_day.close},
            "pivot_range": {"pivot": round(pr.pivot, 4), "low": round(pr.low, 4), "high": round(pr.high, 4),
                            "width": round(pr.width, 4)},
            "atr": None if self.atr is None else round(self.atr, 4),
        }
        if self.previous_pivot_range is not None:
            out["pivot_vs_previous_pivot"] = pivot_range_relationship(self.previous_pivot_range, pr)
        if self.open_price is not None:
            out["open"] = self.open_price
            out["open_vs_pivot_range"] = price_vs_pivot_range(self.open_price, pr)
        if self.opening_range is not None:
            orng = self.opening_range
            out["opening_range"] = {"high": orng.high, "low": orng.low, "mid": round(orng.mid, 4),
                                    "size": round(orng.size, 4),
                                    "bars": f"{orng.bar_count}/{orng.expected_bars}"}
        if self.levels is not None:
            out["acd_levels"] = {k: round(v, 4) for k, v in self.levels.as_dict().items()}
        return out


def compute_day_levels(
    provider: MarketDataProvider,
    ticker: str,
    date: DateLike,
    opening_range_minutes: int = 30,
    a_value: Optional[float] = None,
    c_value: Optional[float] = None,
    a_atr_multiple: Optional[float] = None,
    c_atr_multiple: Optional[float] = None,
    atr_period: int = 14,
) -> DayLevels:
    """Pivot range (known pre-market) plus OR and A/C levels once the OR is complete.

    Works intraday: if minute data is unavailable or the OR is not finished yet,
    only the pre-market levels are returned.
    """
    target = parse_date(date)
    daily = provider.get_daily_data(ticker, target - dt.timedelta(days=120), target)
    history = daily[daily.index < pd.Timestamp(target)]
    if history.empty:
        raise ValueError(f"No daily history for {ticker} before {target}")
    last = history.iloc[-1]
    prev = PreviousDay(last.name.date(), float(last["high"]), float(last["low"]), float(last["close"]))
    pivot = calculate_daily_pivot_range(prev.high, prev.low, prev.close)
    prev_pivot = None
    if len(history) >= 2:
        p2 = history.iloc[-2]
        prev_pivot = calculate_daily_pivot_range(float(p2["high"]), float(p2["low"]), float(p2["close"]))
    try:
        atr_value = atr_as_of(history, target, atr_period)
    except ValueError:
        atr_value = None

    opening_range = levels = open_price = None
    try:
        minute = filter_regular_session(provider.get_minute_data(ticker, target))
    except Exception:
        minute = None
    if minute is not None and not minute.empty:
        open_price = float(minute["open"].iloc[0])
        or_end = market_timestamp(target, REGULAR_OPEN) + pd.Timedelta(minutes=opening_range_minutes)
        if minute.index[-1] >= or_end - pd.Timedelta(minutes=1):  # last OR bar has printed
            opening_range = calculate_opening_range(minute, target, opening_range_minutes)
            if (a_value is not None or a_atr_multiple is not None) and (c_value is not None or c_atr_multiple is not None):
                levels = calculate_acd_levels(
                    opening_range,
                    resolve_value(a_value, a_atr_multiple, atr_value, "a"),
                    resolve_value(c_value, c_atr_multiple, atr_value, "c"),
                )
    return DayLevels(ticker, target, prev, pivot, prev_pivot, opening_range, levels, atr_value, open_price)
