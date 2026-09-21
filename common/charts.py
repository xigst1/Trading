"""Plotly building blocks shared by strategy charts and the Streamlit app."""

from __future__ import annotations

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
