"""Synthetic intraday data for deterministic tests."""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from common.sessions import MARKET_TZ

DATE = "2026-09-18"
SESSION_MINUTES = 390  # 09:30 .. 15:59


def price_path(points: Sequence[Tuple[int, float]], length: int = SESSION_MINUTES) -> List[float]:
    """Piecewise-linear close prices through (minute_offset, price) anchors; flat after the last."""
    xs, ys = zip(*points)
    return list(np.interp(np.arange(length), xs, ys))


def bars_from_closes(closes: Sequence[float], date: str = DATE, wick: float = 0.0) -> pd.DataFrame:
    """1-minute bars from 09:30. open = previous close; high/low = max/min(open, close) +/- wick."""
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    index = pd.date_range(pd.Timestamp(f"{date} 09:30", tz=MARKET_TZ), periods=len(closes), freq="1min",
                          name="timestamp")
    return pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) + wick, "low": np.minimum(opens, closes) - wick,
         "close": closes, "volume": 1000.0},
        index=index,
    )


# Opening range 09:30-09:59 spans exactly 99..101 (OR high 101, OR low 99).
OR_POINTS = [(0, 100.0), (10, 101.0), (20, 99.0), (29, 100.0)]


def day(points_after_or: Sequence[Tuple[int, float]]) -> pd.DataFrame:
    return bars_from_closes(price_path(OR_POINTS + list(points_after_or)))
