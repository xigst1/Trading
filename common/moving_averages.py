"""Simple moving averages and their day-over-day slopes.

The slope (`SMA_n_d1`) is what makes these charts useful: it says whether an average
is still rising, has flattened, or has turned down, which the price line alone hides.
`SMA_n_d1_pct` expresses the same slope as a percent of the average's own level, so it
can be compared across stocks trading at very different prices.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

DEFAULT_WINDOWS = (5, 20, 50)


def moving_average_frame(close: pd.Series, windows: Sequence[int] = DEFAULT_WINDOWS) -> pd.DataFrame:
    """Close plus SMA, slope and percent slope for each window.

    Columns: close, SMA_<n>, SMA_<n>_d1, SMA_<n>_d1_pct for every n in ``windows``.
    Each derivative is one row "deeper" in NaNs than its SMA, so SMA_50_d1 needs 51 rows.
    """
    if close.empty:
        raise ValueError("no price data")
    close = close.sort_index()
    out = pd.DataFrame({"close": close.astype(float)})
    for window in windows:
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        sma = close.rolling(window=window).mean()
        out[f"SMA_{window}"] = sma
        out[f"SMA_{window}_d1"] = sma.diff()
        out[f"SMA_{window}_d1_pct"] = 100 * sma.diff() / sma
    return out


def slope_state(frame: pd.DataFrame, window: int) -> str:
    """RISING / FALLING / FLAT for the latest bar, or UNKNOWN without enough history."""
    series = frame[f"SMA_{window}_d1"].dropna()
    if series.empty:
        return "UNKNOWN"
    last = float(series.iloc[-1])
    if last > 0:
        return "RISING"
    return "FALLING" if last < 0 else "FLAT"


def recent_slope_flip(frame: pd.DataFrame, window: int, lookback: int = 2) -> str:
    """Did this SMA's slope change sign within the last ``lookback`` bars?

    Returns "uptrend" (negative -> positive), "downtrend" (positive -> negative) or "".
    Exact zeros count as no sign, so they neither start nor end a flip. The most recent
    flip wins when a window turned twice inside the lookback.
    """
    series = frame[f"SMA_{window}_d1"].dropna().tail(lookback + 1)
    if len(series) < 2:
        return ""
    signs = [(0 if value == 0 else (1 if value > 0 else -1)) for value in series]
    for i in range(len(signs) - 1, 0, -1):
        if signs[i - 1] < 0 < signs[i]:
            return "uptrend"
        if signs[i] < 0 < signs[i - 1]:
            return "downtrend"
    return ""
