"""Market-time utilities: timezone handling, OHLCV standardization, session filters
and intraday data validation.

Standard OHLCV frame used throughout the project:
    index   : DatetimeIndex named "timestamp", tz-aware US/Eastern (bar *start* time)
    columns : open, high, low, close, volume  (lower case, float)
"""

from __future__ import annotations

import datetime as dt
import warnings
from typing import List, Optional, Union

import pandas as pd

MARKET_TZ = "America/New_York"
REGULAR_OPEN = "09:30"
REGULAR_CLOSE = "16:00"

PRICE_COLUMNS = ["open", "high", "low", "close"]
OHLCV_COLUMNS = PRICE_COLUMNS + ["volume"]

DateLike = Union[str, dt.date, dt.datetime, pd.Timestamp]
TimeLike = Union[str, dt.time]


class DataQualityWarning(UserWarning):
    """Recoverable data issue (e.g. missing minutes) that should not be silently ignored."""


class DataValidationError(ValueError):
    """Intraday data is unusable (unsorted, duplicated, wrong date, missing columns)."""


def parse_date(value: DateLike) -> dt.date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return pd.Timestamp(value).date()


def parse_time(value: TimeLike) -> dt.time:
    if isinstance(value, dt.time):
        return value
    hour, minute = value.split(":")
    return dt.time(int(hour), int(minute))


def market_timestamp(date: DateLike, time: TimeLike) -> pd.Timestamp:
    """Tz-aware Eastern timestamp for a calendar date and wall-clock time."""
    return pd.Timestamp(dt.datetime.combine(parse_date(date), parse_time(time))).tz_localize(MARKET_TZ)


def to_market_tz(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Naive timestamps are assumed to already be Eastern; aware ones are converted."""
    if index.tz is None:
        return index.tz_localize(MARKET_TZ)
    return index.tz_convert(MARKET_TZ)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        # yf.download returns (field, ticker) columns; keep the field level.
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    return out


def standardize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Convert an intraday frame from any provider into the standard OHLCV layout.

    Accepts either a DatetimeIndex or a ``timestamp``/``datetime`` column. Does NOT
    sort or de-duplicate, so ``validate_intraday_data`` can still detect those problems.
    """
    out = _normalize_columns(df)
    for col in ("timestamp", "datetime", "date", "time"):
        if col in out.columns and not isinstance(out.index, pd.DatetimeIndex):
            out = out.set_index(pd.DatetimeIndex(pd.to_datetime(out.pop(col))))
            break
    if not isinstance(out.index, pd.DatetimeIndex):
        raise DataValidationError("Intraday data needs a DatetimeIndex or a 'timestamp' column")

    missing = [c for c in PRICE_COLUMNS if c not in out.columns]
    if missing:
        raise DataValidationError(f"Missing required OHLC columns: {missing}")
    if "volume" not in out.columns:
        out["volume"] = 0.0

    out.index = to_market_tz(out.index)
    out.index.name = "timestamp"
    out[OHLCV_COLUMNS] = out[OHLCV_COLUMNS].astype(float)
    return out


def standardize_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Daily bars indexed by a tz-naive, midnight-normalized DatetimeIndex named ``date``."""
    out = _normalize_columns(df)
    if "date" in out.columns and not isinstance(out.index, pd.DatetimeIndex):
        out = out.set_index(pd.DatetimeIndex(pd.to_datetime(out.pop("date"))))
    if not isinstance(out.index, pd.DatetimeIndex):
        raise DataValidationError("Daily data needs a DatetimeIndex or a 'date' column")
    missing = [c for c in PRICE_COLUMNS if c not in out.columns]
    if missing:
        raise DataValidationError(f"Missing required OHLC columns: {missing}")
    if "volume" not in out.columns:
        out["volume"] = 0.0

    index = out.index
    if index.tz is not None:
        index = index.tz_convert(MARKET_TZ).tz_localize(None)
    out.index = index.normalize()
    out.index.name = "date"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out[OHLCV_COLUMNS] = out[OHLCV_COLUMNS].astype(float)
    return out


def filter_regular_session(
    df: pd.DataFrame, market_open: TimeLike = REGULAR_OPEN, market_close: TimeLike = REGULAR_CLOSE
) -> pd.DataFrame:
    """Keep bars with ``market_open <= bar start < market_close`` (Eastern)."""
    times = df.index.time
    mask = (times >= parse_time(market_open)) & (times < parse_time(market_close))
    return df[mask]


def expected_minutes(date: DateLike, start: TimeLike, end: TimeLike) -> pd.DatetimeIndex:
    """Every 1-minute bar start in ``[start, end)`` on ``date``."""
    return pd.date_range(
        market_timestamp(date, start), market_timestamp(date, end), freq="1min", inclusive="left"
    )


def find_missing_minutes(
    df: pd.DataFrame, date: DateLike, start: TimeLike = REGULAR_OPEN, end: TimeLike = REGULAR_CLOSE
) -> pd.DatetimeIndex:
    return expected_minutes(date, start, end).difference(df.index)


def validate_intraday_data(
    df: pd.DataFrame,
    target_date: DateLike,
    market_open: TimeLike = REGULAR_OPEN,
    market_close: TimeLike = REGULAR_CLOSE,
    warn: bool = True,
) -> List[str]:
    """Validate a standardized, session-filtered 1-minute frame for one day.

    Raises DataValidationError for unusable data. Returns a list of warning messages
    (also emitted as DataQualityWarning when ``warn``) for recoverable issues.
    """
    if df.empty:
        raise DataValidationError("No intraday bars in the regular session")
    missing_cols = [c for c in PRICE_COLUMNS if c not in df.columns]
    if missing_cols:
        raise DataValidationError(f"Missing required OHLC columns: {missing_cols}")
    if df.index.has_duplicates:
        dupes = df.index[df.index.duplicated()].unique()
        raise DataValidationError(f"Duplicate timestamps: {list(dupes[:5])}")
    if not df.index.is_monotonic_increasing:
        raise DataValidationError("Timestamps are not sorted ascending")

    date = parse_date(target_date)
    wrong = sorted({d for d in df.index.date if d != date})
    if wrong:
        raise DataValidationError(f"Bars from other dates present: {wrong[:5]} (target {date})")

    messages: List[str] = []
    if df[PRICE_COLUMNS].isna().any().any():
        messages.append("NaN prices present in OHLC columns")
    bad = df[(df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))]
    if not bad.empty:
        messages.append(f"{len(bad)} bars have high/low inconsistent with open/close")

    missing = find_missing_minutes(df, date, market_open, market_close)
    if len(missing):
        messages.append(f"{len(missing)} missing minute bars: {describe_gaps(missing)}")

    if warn:
        for msg in messages:
            warnings.warn(msg, DataQualityWarning, stacklevel=2)
    return messages


def describe_gaps(missing: pd.DatetimeIndex, limit: int = 5) -> str:
    """Compress missing minutes into 'HH:MM-HH:MM' runs for readable warnings."""
    if len(missing) == 0:
        return ""
    runs = []
    start = prev = missing[0]
    for ts in missing[1:]:
        if ts - prev != pd.Timedelta(minutes=1):
            runs.append((start, prev))
            start = ts
        prev = ts
    runs.append((start, prev))
    text = ", ".join(
        s.strftime("%H:%M") if s == e else f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in runs[:limit]
    )
    if len(runs) > limit:
        text += f" (+{len(runs) - limit} more gaps)"
    return text


def previous_row_before(daily: pd.DataFrame, date: DateLike) -> Optional[pd.Series]:
    """Last daily bar strictly before ``date`` (no look-ahead)."""
    prior = daily[daily.index < pd.Timestamp(parse_date(date))]
    if prior.empty:
        return None
    return prior.iloc[-1]
