"""Plotly building blocks shared by strategy charts and the Streamlit app."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Categorical slots in fixed order (validated default palette); candle up/down colors.
PALETTE: Dict[str, str] = {
    "blue": "#2a78d6",
    "orange": "#eb6834",
    "aqua": "#1baf7a",
    "yellow": "#eda100",
    "magenta": "#e87ba4",
    "green": "#008300",
    "violet": "#4a3aa7",
    "red": "#e34948",
    "muted": "#898781",
}
UP_COLOR = PALETTE["aqua"]
DOWN_COLOR = PALETTE["red"]


def _is_intraday(index: pd.DatetimeIndex) -> bool:
    return len(index) > 1 and (index[1:] - index[:-1]).min() < pd.Timedelta(days=1)


def candlestick_figure(df: pd.DataFrame, title: str = "", show_volume: bool = True, height: int = 620) -> go.Figure:
    """Candlesticks, with volume in its own panel below (shared time axis, separate y)."""
    rows = 2 if show_volume and "volume" in df.columns else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.78, 0.22] if rows == 2 else [1.0])
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="Price",
            increasing=dict(line=dict(color=UP_COLOR, width=1), fillcolor=UP_COLOR),
            decreasing=dict(line=dict(color=DOWN_COLOR, width=1), fillcolor=DOWN_COLOR),
        ),
        row=1, col=1,
    )
    if rows == 2:
        colors = [UP_COLOR if c >= o else DOWN_COLOR for o, c in zip(df["open"], df["close"])]
        fig.add_trace(go.Bar(x=df.index, y=df["volume"], marker_color=colors, name="Volume",
                             marker_line_width=0, opacity=0.6), row=2, col=1)
        fig.update_yaxes(title_text="Volume", row=2, col=1)

    breaks = [dict(bounds=["sat", "mon"])]
    if _is_intraday(df.index) and len(set(df.index.date)) > 1:
        breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
    fig.update_xaxes(rangebreaks=breaks, showgrid=False)
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_layout(
        title=dict(text=title, y=0.985, yanchor="top"), height=height, template="plotly_white",
        xaxis_rangeslider_visible=False, hovermode="x unified",
        margin=dict(l=10, r=10, t=80 if title else 40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
    )
    return fig


def add_level(fig: go.Figure, y: float, label: str, color: str, dash: str = "solid",
              x0=None, x1=None, width: float = 1.5) -> None:
    """Horizontal price level with a right-edge label. Spans the whole chart unless x0/x1 given."""
    if x0 is None or x1 is None:
        fig.add_hline(y=y, line=dict(color=color, dash=dash, width=width), row=1, col=1,
                      annotation_text=f"{label} {y:,.2f}", annotation_position="top right",
                      annotation_font_size=11)
        return
    fig.add_trace(go.Scatter(x=[x0, x1], y=[y, y], mode="lines", name=label, showlegend=False,
                             line=dict(color=color, dash=dash, width=width),
                             hovertemplate=f"{label}: %{{y:,.2f}}<extra></extra>"), row=1, col=1)


def add_band(fig: go.Figure, y0: float, y1: float, color: str, label: str, x0=None, x1=None,
             opacity: float = 0.12) -> None:
    """Shaded price band; limited to [x0, x1] when given, otherwise full width."""
    if x0 is not None and x1 is not None:
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=color, opacity=opacity,
                      line_width=0, row=1, col=1)
        fig.add_annotation(x=x0, y=y1, text=label, showarrow=False, xanchor="left", yanchor="bottom",
                           font=dict(size=11), row=1, col=1)
    else:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=color, opacity=opacity, line_width=0, row=1, col=1,
                      annotation_text=label, annotation_position="top left", annotation_font_size=11)


def add_markers(fig: go.Figure, times: Sequence, prices: Sequence[float], label: str, color: str,
                symbol: str = "circle", texts: Optional[Sequence[str]] = None, size: int = 11) -> None:
    fig.add_trace(
        go.Scatter(
            x=list(times), y=list(prices), mode="markers+text" if texts else "markers", name=label,
            text=list(texts) if texts else None, textposition="top center", textfont=dict(size=10),
            marker=dict(color=color, symbol=symbol, size=size, line=dict(color="white", width=2)),
            hovertemplate=f"{label}<br>%{{x|%H:%M}}  %{{y:,.2f}}<extra></extra>",
        ),
        row=1, col=1,
    )


def line_figure(series: Dict[str, pd.Series], title: str = "", y_title: str = "", height: int = 320) -> go.Figure:
    """One or more series on a single y-axis (same unit). Colors follow insertion order."""
    order = ["blue", "orange", "aqua", "yellow", "magenta", "green", "violet", "red"]
    fig = go.Figure()
    for i, (name, s) in enumerate(series.items()):
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", name=name,
                                 line=dict(color=PALETTE[order[i % len(order)]], width=2)))
    fig.update_layout(title=title, height=height, hovermode="x unified", yaxis_title=y_title,
                      margin=dict(l=10, r=10, t=50 if title else 20, b=10),
                      showlegend=len(series) > 1)
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def price_sma_slope_stack(frame: pd.DataFrame, ticker: str, windows=(5, 20, 50),
                          use_pct: bool = False, height: Optional[int] = None) -> go.Figure:
    """One row per moving-average window: price and the SMA on the left axis, that SMA's
    day-over-day change on the right axis with a dotted zero line.

    Rows share the x-axis so they zoom together. Within a row the two y-axes are scaled
    independently, so the slope line's height relative to the price lines means nothing -
    read only its sign and shape. Above the zero line the average is rising, below it falling.

    ``frame`` comes from common.moving_averages.moving_average_frame: a date index with
    close, SMA_<n>, SMA_<n>_d1 and SMA_<n>_d1_pct columns.
    """
    price_color, sma_color, slope_color = "#4c5561", PALETTE["blue"], PALETTE["orange"]
    slope_units = "percent per day" if use_pct else "price per day"

    for window in windows:
        needed = [f"SMA_{window}", f"SMA_{window}_d1_pct" if use_pct else f"SMA_{window}_d1"]
        missing = [c for c in needed if c not in frame.columns]
        if missing:
            raise KeyError(f"{missing} not in frame; compute them before plotting")

    fig = make_subplots(rows=len(windows), cols=1, shared_xaxes=True, vertical_spacing=0.045,
                        specs=[[{"secondary_y": True}] for _ in windows],
                        subplot_titles=[f"SMA_{w}" for w in windows])

    for row, window in enumerate(windows, start=1):
        sma_col = f"SMA_{window}"
        slope_col = f"{sma_col}_d1_pct" if use_pct else f"{sma_col}_d1"
        first_row = row == 1

        fig.add_trace(go.Scatter(x=frame.index, y=frame["close"], mode="lines", name="Close",
                                 legendgroup="price", showlegend=first_row,
                                 line=dict(width=2, color=price_color),
                                 hovertemplate="Close: %{y:.2f}<extra></extra>"),
                      row=row, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=frame.index, y=frame[sma_col], mode="lines", name="SMA",
                                 legendgroup="sma", showlegend=first_row,
                                 line=dict(width=2, color=sma_color),
                                 hovertemplate=f"{sma_col}: %{{y:.2f}}<extra></extra>"),
                      row=row, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=frame.index, y=frame[slope_col], mode="lines",
                                 name="SMA daily change (right axis)", legendgroup="slope",
                                 showlegend=first_row, line=dict(width=2, color=slope_color),
                                 hovertemplate=f"{slope_col}: %{{y:.3f}}<extra></extra>"),
                      row=row, col=1, secondary_y=True)
        fig.add_hline(y=0, line=dict(width=1, color=slope_color, dash="dot"), opacity=0.5,
                      row=row, col=1, secondary_y=True)

        fig.update_yaxes(title_text="Price", title_font_color=sma_color, tickfont_color=sma_color,
                         row=row, col=1, secondary_y=False)
        fig.update_yaxes(title_text=f"d1 ({slope_units})", title_font_color=slope_color,
                         tickfont_color=slope_color, showgrid=False,
                         row=row, col=1, secondary_y=True)

    last_date = frame.index[-1]
    last_date = last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date)
    fig.update_xaxes(title_text="Date", rangebreaks=[dict(bounds=["sat", "mon"])],
                     row=len(windows), col=1)
    fig.update_layout(title=f"{ticker} price, moving averages and their slopes ({last_date})",
                      template="plotly_white", hovermode="x unified",
                      margin=dict(l=70, r=80, t=110, b=50),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    if height is not None:
        fig.update_layout(height=height)
    return fig


def write_full_height_html(fig: go.Figure, path, title: str = "") -> None:
    """Standalone page at 100vh with scrolling off, so stacked rows fill one screen."""
    div = fig.to_html(include_plotlyjs="cdn", full_html=False, default_height="100vh",
                      config={"responsive": True})
    page = ("<!doctype html>\n<html><head><meta charset='utf-8'>\n"
            f"<title>{title or 'chart'}</title>\n"
            "<style>html,body{margin:0;padding:0;overflow:hidden;background:#fff}</style>\n"
            "</head><body>\n" + div + "\n</body></html>\n")
    Path(path).write_text(page)
