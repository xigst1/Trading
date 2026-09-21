"""Wraps a remote provider with the local CSV store.

Minute data: served from disk when present, otherwise fetched and archived.
Daily data: fetched fresh (recent bars change) and merged into the archive;
falls back to the archive when the remote provider fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import pandas as pd

from common.market_data.base import DataUnavailableError, MarketDataProvider
from common.market_data.csv_provider import LocalStore
from common.sessions import MARKET_TZ, DateLike, parse_date


class CachedProvider(MarketDataProvider):
    def __init__(self, remote: MarketDataProvider, root: Union[str, Path, None] = None):
        self.remote = remote
        self.store = LocalStore(root)
        self.name = f"{remote.name}+cache"

    def get_minute_data(self, ticker: str, date: DateLike) -> pd.DataFrame:
        cached = self.store.load_minute(ticker, date)
        if cached is not None and not _is_today(date):
            return cached
        df = self.remote.get_minute_data(ticker, date)
        self.store.save_minute(ticker, df)
        return df

    def get_daily_data(self, ticker: str, start: DateLike, end: DateLike) -> pd.DataFrame:
        try:
            df = self.remote.get_daily_data(ticker, start, end)
        except Exception as remote_error:
            cached = self.store.load_daily(ticker)
            if cached is None:
                raise
            start_ts, end_ts = pd.Timestamp(parse_date(start)), pd.Timestamp(parse_date(end))
            df = cached[(cached.index >= start_ts) & (cached.index <= end_ts)]
            if df.empty:
                raise DataUnavailableError(str(remote_error)) from remote_error
            return df
        # Today's daily bar is still forming; don't archive it.
        today = pd.Timestamp(pd.Timestamp.now(MARKET_TZ).date())
        self.store.save_daily(ticker, df[df.index < today])
        return df


def _is_today(date: DateLike) -> bool:
    return parse_date(date) == pd.Timestamp.now(MARKET_TZ).date()
