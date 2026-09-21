"""Chart of one ACD day: candles, OR, pivot range, A/C levels and simulator events."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from acd.simulation.simulator import ACDDayResult
from common.charts import PALETTE, add_band, add_level, add_markers, candlestick_figure
from common.sessions import filter_regular_session, standardize_ohlcv

TOUCH_EVENTS = {"A_UP_TOUCHED", "A_DOWN_TOUCHED", "C_UP_TOUCHED", "C_DOWN_TOUCHED"}
CONFIRM_EVENTS = {"A_UP_CONFIRMED", "A_DOWN_CONFIRMED", "C_UP_CONFIRMED", "C_DOWN_CONFIRMED"}


def plot_acd_day(minute_data: pd.DataFrame, result: ACDDayResult, height: int = 680) -> go.Figure:
    df = filter_regular_session(standardize_ohlcv(minute_data))
    df = df[df.index.date == result.date]
    fig = candlestick_figure(df, f"{result.ticker} {result.date} - ACD", show_volume=True, height=height)
    start, end = df.index[0], df.index[-1]

    add_band(fig, result.pivot_low, result.pivot_high, PALETTE["muted"], "Pivot range", opacity=0.18)
    if result.opening_range is not None:
        orng = result.opening_range
        add_band(fig, orng.low, orng.high, PALETTE["blue"], "Opening range",
                 x0=orng.start, x1=orng.end - pd.Timedelta(minutes=1), opacity=0.15)

    add_level(fig, result.or_high, "OR high", PALETTE["blue"])
    add_level(fig, result.or_low, "OR low", PALETTE["blue"])
    add_level(fig, result.a_up, "A up", PALETTE["orange"], dash="dash")
    add_level(fig, result.a_down, "A down", PALETTE["orange"], dash="dash")
    add_level(fig, result.c_up, "C up", PALETTE["violet"], dash="dot")
    add_level(fig, result.c_down, "C down", PALETTE["violet"], dash="dot")
    add_level(fig, result.pivot, "Pivot", PALETTE["muted"], dash="dot", width=1)

    events = pd.DataFrame(result.events)
    if not events.empty:
        touches = events[events["event"].isin(TOUCH_EVENTS)]
        if not touches.empty:
            add_markers(fig, touches["time"], touches["price"], "Touch", PALETTE["yellow"], "circle-open", size=9)
        confirms = events[events["event"].isin(CONFIRM_EVENTS)]
        if not confirms.empty:
            add_markers(fig, confirms["time"], confirms["price"], "Confirmed", PALETTE["orange"], "star",
                        texts=[e.replace("_CONFIRMED", "") for e in confirms["event"]])
        b = events[events["event"] == "B_REACHED"]
        if not b.empty:
            add_markers(fig, b["time"], b["price"], "B reached", PALETTE["magenta"], "x")

    for t in result.trades:
        up = t.direction == "LONG"
        add_markers(fig, [t.entry_time], [t.entry_price], f"{t.setup} {t.direction.lower()} entry",
                    PALETTE["green"] if up else PALETTE["red"], "triangle-up" if up else "triangle-down", size=13)
        if t.exit_time is not None:
            add_markers(fig, [t.exit_time], [t.exit_price], f"Exit ({t.exit_reason})", PALETTE["muted"], "square",
                        size=10)
    fig.update_xaxes(range=[start - pd.Timedelta(minutes=5), end + pd.Timedelta(minutes=25)])
    return fig
