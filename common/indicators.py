"""Price indicators shared across strategies.

All functions take a standard OHLCV frame (see common.sessions) and return Series or
frames aligned to its index. Values at row ``t`` only use rows ``<= t`` unless a
function explicitly says otherwise.
"""

from __future__ import annotations

import pandas as pd

from common.sessions import DateLike, parse_date


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range = max(high - low, |high - prev close|, |low - prev close|).

    The first row has no previous close, so it falls back to high - low.
    """
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1).rename("tr")


def atr(df: pd.DataFrame, period: int = 14, method: str = "wilder") -> pd.Series:
    """Average True Range.

    method="wilder": Wilder's smoothing, seeded with the simple mean of the first
                     ``period`` TR values (the classic definition; Turtle "N" uses it).
    method="sma":    simple rolling mean of TR.
    Rows before ``period`` TR values exist are NaN.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    tr = true_range(df)
    if method == "sma":
        return tr.rolling(period, min_periods=period).mean().rename("atr")
    if method != "wilder":
        raise ValueError(f"Unknown ATR method: {method!r}")

    values = tr.to_numpy(dtype=float)
    out = pd.Series(float("nan"), index=tr.index, name="atr")
    if len(values) < period:
        return out
    current = values[:period].mean()
    out.iloc[period - 1] = current
    for i in range(period, len(values)):
        current = (current * (period - 1) + values[i]) / period
        out.iloc[i] = current
    return out


def atr_as_of(daily: pd.DataFrame, date: DateLike, period: int = 14, method: str = "wilder",
              include_date: bool = False) -> float:
    """ATR known before trading on ``date`` (uses bars strictly before it by default).

    Set ``include_date=True`` only when ``date``'s daily bar is complete and you want
    it included (e.g. an end-of-day report).
    """
    cutoff = pd.Timestamp(parse_date(date))
    history = daily[daily.index <= cutoff] if include_date else daily[daily.index < cutoff]
    series = atr(history, period, method).dropna()
    if series.empty:
        raise ValueError(f"Not enough daily history for ATR({period}) as of {parse_date(date)}")
    return float(series.iloc[-1])


def donchian_channel(df: pd.DataFrame, period: int, exclude_current: bool = True) -> pd.DataFrame:
    """Highest high / lowest low over ``period`` bars (used by breakout systems like Turtle).

    With ``exclude_current`` (default) the channel at row t covers rows t-period..t-1,
    so comparing today's price against it has no look-ahead.
    """
    high = df["high"].rolling(period, min_periods=period).max()
    low = df["low"].rolling(period, min_periods=period).min()
    if exclude_current:
        high, low = high.shift(1), low.shift(1)
    return pd.DataFrame({"upper": high, "lower": low, "mid": (high + low) / 2}, index=df.index)
