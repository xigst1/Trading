"""Local CSV storage and a provider that reads from it.

Layout under the data root (default: <repo>/data):
    minute/<TICKER>/<YYYY-MM-DD>.csv   one file per trading day
    daily/<TICKER>.csv                 all daily bars, merged on every save
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from common.config import DATA_DIR
from common.market_data.base import DataUnavailableError, MarketDataProvider
from common.sessions import DateLike, parse_date, standardize_daily, standardize_ohlcv


class LocalStore:
    def __init__(self, root: Union[str, Path, None] = None):
        self.root = Path(root) if root is not None else DATA_DIR

    def minute_path(self, ticker: str, date: DateLike) -> Path:
        return self.root / "minute" / ticker.upper() / f"{parse_date(date).isoformat()}.csv"

    def daily_path(self, ticker: str) -> Path:
        return self.root / "daily" / f"{ticker.upper()}.csv"

    def save_minute(self, ticker: str, df: pd.DataFrame, overwrite_smaller: bool = True) -> List[Path]:
        """Split an intraday frame by date and write one CSV per day.

        An existing file is only replaced when the new data has at least as many bars,
        so re-downloading a partially available day never loses bars.
        """
        written = []
        for date, day in df.groupby(df.index.date):
            path = self.minute_path(ticker, date)
            if path.exists() and overwrite_smaller:
                existing = pd.read_csv(path)
                if len(existing) > len(day):
                    continue
            path.parent.mkdir(parents=True, exist_ok=True)
            day.to_csv(path, index_label="timestamp")
            written.append(path)
        return written

    def save_daily(self, ticker: str, df: pd.DataFrame) -> Path:
        path = self.daily_path(ticker)
        merged = df
        if path.exists():
            merged = pd.concat([self.load_daily(ticker), df])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(path, index_label="date")
        return path

    def load_minute(self, ticker: str, date: DateLike) -> Optional[pd.DataFrame]:
        path = self.minute_path(ticker, date)
        if not path.exists():
            return None
        return standardize_ohlcv(pd.read_csv(path, parse_dates=["timestamp"]))

    def load_daily(self, ticker: str) -> Optional[pd.DataFrame]:
        path = self.daily_path(ticker)
        if not path.exists():
            return None
        return standardize_daily(pd.read_csv(path, parse_dates=["date"]))

    def minute_dates(self, ticker: str) -> List[str]:
        folder = self.root / "minute" / ticker.upper()
        return sorted(p.stem for p in folder.glob("*.csv")) if folder.exists() else []

    def tickers(self) -> List[str]:
        names = set()
        for sub in ("minute", "daily"):
            folder = self.root / sub
            if folder.exists():
                names.update(p.stem if p.is_file() else p.name for p in folder.iterdir()
                             if not p.name.startswith("."))
        return sorted(names)


class CSVProvider(MarketDataProvider):
    """Reads previously downloaded data from a LocalStore. Never touches the network."""

    name = "csv"

    def __init__(self, root: Union[str, Path, None] = None):
        self.store = LocalStore(root)

    def get_minute_data(self, ticker: str, date: DateLike) -> pd.DataFrame:
        df = self.store.load_minute(ticker, date)
        if df is None:
            raise DataUnavailableError(f"No local minute file {self.store.minute_path(ticker, date)}")
        return df

    def get_daily_data(self, ticker: str, start: DateLike, end: DateLike) -> pd.DataFrame:
        df = self.store.load_daily(ticker)
        if df is None:
            raise DataUnavailableError(f"No local daily file {self.store.daily_path(ticker)}")
        start_ts, end_ts = pd.Timestamp(parse_date(start)), pd.Timestamp(parse_date(end))
        return df[(df.index >= start_ts) & (df.index <= end_ts)]
