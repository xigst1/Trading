"""Tools the agent can call. Each returns JSON-able content for the model plus
figures/tables for the Streamlit UI (the model never sees the charts themselves)."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

import pandas as pd

from acd.charts import plot_acd_day
from acd.simulation.runner import compute_day_levels, run_acd_day
from common.charts import candlestick_figure, line_figure
from common.indicators import atr, true_range
from common.market_data import MarketDataProvider
from common.sessions import filter_regular_session, parse_date

MAX_MINUTE_DAYS = 5


@dataclass
class ToolOutput:
    content: Dict[str, Any]
    figures: List[Any] = field(default_factory=list)
    tables: List[Tuple[str, pd.DataFrame]] = field(default_factory=list)
    is_error: bool = False

    def to_model_text(self) -> str:
        return json.dumps(self.content, default=str)


_DATE = {"type": "string", "description": "Date as YYYY-MM-DD (US/Eastern trading date)."}
_TICKER = {"type": "string", "description": "Ticker symbol, e.g. SPY, AAPL."}
_ACD_PARAMS = {
    "opening_range_minutes": {"type": "integer", "description": "Opening range length in minutes. Default 30."},
    "a_value": {"type": "number", "description": "A distance in price units. Give this or a_atr_multiple."},
    "a_atr_multiple": {"type": "number", "description": "A as a multiple of the 14-day ATR known before the open, e.g. 0.1."},
    "c_value": {"type": "number", "description": "C distance in price units. Give this or c_atr_multiple."},
    "c_atr_multiple": {"type": "number", "description": "C as a multiple of the 14-day ATR, e.g. 0.15."},
}

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "get_price_history",
        "description": (
            "Load OHLCV price history for a ticker and show a candlestick chart in the UI. Returns summary "
            "statistics and the most recent bars. interval '1d' for daily bars (any range) or '1m' for "
            f"1-minute bars (at most {MAX_MINUTE_DAYS} days, only about the last 30 days are available)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": _TICKER,
                "start_date": _DATE,
                "end_date": _DATE,
                "interval": {"type": "string", "enum": ["1d", "1m"]},
            },
            "required": ["ticker", "start_date", "end_date", "interval"],
        },
    },
    {
        "name": "get_volatility",
        "description": (
            "Daily True Range and Average True Range (Wilder) for a ticker, as known at the close of "
            "as_of_date (defaults to the latest bar). Shows an ATR chart in the UI."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": _TICKER,
                "as_of_date": _DATE,
                "period": {"type": "integer", "description": "ATR period in days. Default 14."},
                "lookback_days": {"type": "integer", "description": "Calendar days of history to chart. Default 180."},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_acd_levels",
        "description": (
            "Mark Fisher ACD reference levels for one ticker and date: previous-day H/L/C, daily pivot range "
            "(and its relation to the prior day's), 14-day ATR, and - once the opening range is complete - "
            "the opening range and, if A and C are given, A-up/A-down/C-up/C-down levels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": _TICKER, "date": _DATE, **_ACD_PARAMS},
            "required": ["ticker", "date"],
        },
    },
    {
        "name": "simulate_acd_day",
        "description": (
            "Run the one-day ACD simulator (state machine) for a ticker and date using 1-minute bars. Returns "
            "levels, trades (entry/exit/P&L per share) and the event log, and shows an annotated chart in the "
            "UI. Requires A, C (fixed or ATR multiple) and confirmation_minutes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": _TICKER,
                "date": _DATE,
                "confirmation_minutes": {"type": "integer", "description": "Minutes price must hold beyond A/C to confirm."},
                **_ACD_PARAMS,
            },
            "required": ["ticker", "date", "confirmation_minutes"],
        },
    },
]


class TradingTools:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider
        self._handlers: Dict[str, Callable[..., ToolOutput]] = {
            "get_price_history": self.get_price_history,
            "get_volatility": self.get_volatility,
            "get_acd_levels": self.get_acd_levels,
            "simulate_acd_day": self.simulate_acd_day,
        }

    definitions = TOOL_DEFINITIONS

    def run(self, name: str, tool_input: Dict[str, Any]) -> ToolOutput:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutput({"error": f"Unknown tool {name}"}, is_error=True)
        try:
            return handler(**tool_input)
        except TypeError as exc:
            return ToolOutput({"error": f"Invalid arguments for {name}: {exc}"}, is_error=True)
        except Exception as exc:  # surface data problems to the model instead of crashing the chat
            return ToolOutput({"error": f"{type(exc).__name__}: {exc}"}, is_error=True)

    # --- tools -----------------------------------------------------------------------
    def get_price_history(self, ticker: str, start_date: str, end_date: str, interval: str = "1d") -> ToolOutput:
        start, end = parse_date(start_date), parse_date(end_date)
        ticker = ticker.upper()
        if interval == "1m":
            days = pd.bdate_range(start, end)
            if len(days) > MAX_MINUTE_DAYS:
                return ToolOutput({"error": f"1m interval is limited to {MAX_MINUTE_DAYS} trading days"}, is_error=True)
            frames = []
            for d in days:
                try:
                    frames.append(filter_regular_session(self.provider.get_minute_data(ticker, d)))
                except Exception:
                    continue  # holidays / unavailable days
            if not frames:
                return ToolOutput({"error": f"No 1m data for {ticker} {start}..{end}"}, is_error=True)
            df = pd.concat(frames)
        else:
            df = self.provider.get_daily_data(ticker, start, end)
        if df.empty:
            return ToolOutput({"error": f"No data for {ticker} {start}..{end}"}, is_error=True)

        first, last = df.iloc[0], df.iloc[-1]
        content = {
            "ticker": ticker, "interval": interval, "bars": len(df),
            "first_bar": str(df.index[0]), "last_bar": str(df.index[-1]),
            "first_open": round(float(first["open"]), 4), "last_close": round(float(last["close"]), 4),
            "change_pct": round(100 * (float(last["close"]) / float(first["open"]) - 1), 3),
            "period_high": round(float(df["high"].max()), 4), "period_low": round(float(df["low"].min()), 4),
            "average_volume": round(float(df["volume"].mean()), 0),
            "recent_bars": _records(df.tail(10)),
        }
        fig = candlestick_figure(df, f"{ticker} {interval} {start} to {end}")
        return ToolOutput(content, figures=[fig], tables=[(f"{ticker} bars", df.tail(50))])

    def get_volatility(self, ticker: str, as_of_date: str = None, period: int = 14, lookback_days: int = 180) -> ToolOutput:
        ticker = ticker.upper()
        end = parse_date(as_of_date) if as_of_date else dt.date.today()
        daily = self.provider.get_daily_data(ticker, end - dt.timedelta(days=lookback_days + 3 * period), end)
        if len(daily) <= period:
            return ToolOutput({"error": f"Not enough daily bars for ATR({period})"}, is_error=True)
        tr, atr_series = true_range(daily), atr(daily, period)
        last_close = float(daily["close"].iloc[-1])
        content = {
            "ticker": ticker, "as_of": str(daily.index[-1].date()), "period": period,
            "true_range_latest": round(float(tr.iloc[-1]), 4),
            f"atr_{period}": round(float(atr_series.iloc[-1]), 4),
            "atr_pct_of_close": round(100 * float(atr_series.iloc[-1]) / last_close, 3),
            "last_close": last_close,
            "recent": [
                {"date": str(d.date()), "tr": round(float(t), 4), "atr": round(float(a), 4)}
                for d, t, a in zip(daily.index[-10:], tr.iloc[-10:], atr_series.iloc[-10:])
            ],
        }
        window = daily.index >= pd.Timestamp(end - dt.timedelta(days=lookback_days))
        fig = line_figure({f"ATR({period})": atr_series[window], "True range": tr[window]},
                          f"{ticker} true range and ATR({period})", "Price units")
        table = pd.DataFrame({"close": daily["close"], "tr": tr, f"atr_{period}": atr_series})[window]
        return ToolOutput(content, figures=[fig], tables=[(f"{ticker} TR / ATR", table.tail(30))])

    def get_acd_levels(self, ticker: str, date: str, opening_range_minutes: int = 30, a_value: float = None,
                       a_atr_multiple: float = None, c_value: float = None, c_atr_multiple: float = None) -> ToolOutput:
        levels = compute_day_levels(self.provider, ticker.upper(), date, opening_range_minutes, a_value, c_value,
                                    a_atr_multiple, c_atr_multiple)
        return ToolOutput(levels.summary(), tables=[(f"{ticker.upper()} {date} ACD levels",
                                                     _flatten_levels(levels.summary()))])

    def simulate_acd_day(self, ticker: str, date: str, confirmation_minutes: int, opening_range_minutes: int = 30,
                         a_value: float = None, a_atr_multiple: float = None, c_value: float = None,
                         c_atr_multiple: float = None) -> ToolOutput:
        result, inputs, config = run_acd_day(
            self.provider, ticker.upper(), date, confirmation_minutes,
            a_value=a_value, c_value=c_value, a_atr_multiple=a_atr_multiple, c_atr_multiple=c_atr_multiple,
            opening_range_minutes=opening_range_minutes,
        )
        content = result.summary()
        content["parameters"] = {"a_value": round(config.a_value, 4), "c_value": round(config.c_value, 4),
                                 "confirmation_minutes": confirmation_minutes,
                                 "opening_range_minutes": opening_range_minutes,
                                 "atr_14": None if inputs.atr is None else round(inputs.atr, 4)}
        events = result.events_frame()
        content["events"] = events[events["event"] != "STATE_CHANGE"].to_dict("records")
        fig = plot_acd_day(inputs.minute_data, result)
        tables = [("Event log", events)]
        if result.trades:
            tables.insert(0, ("Trades", result.trades_frame()))
        return ToolOutput(content, figures=[fig], tables=tables)


def _records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = df.reset_index().round(4)
    out.iloc[:, 0] = out.iloc[:, 0].astype(str)
    return out.to_dict("records")


def _flatten_levels(summary: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for key, value in summary.items():
        if isinstance(value, dict):
            rows.extend((f"{key}.{k}", v) for k, v in value.items())
        elif key not in ("ticker", "date"):
            rows.append((key, value))
    return pd.DataFrame(rows, columns=["item", "value"]).astype({"value": str})
