"""Yahoo Finance provider (via yfinance).

Yahoo only serves 1-minute bars for roughly the last 30 days, at most ~7 days per
request. Use scripts/download_data.py regularly to archive minute data locally.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from common.market_data.base import DataUnavailableError, MarketDataProvider
from common.sessions import DateLike, parse_date, standardize_daily, standardize_ohlcv

# Yahoo rejects 1m requests spanning more than 8 days; stay under it.
MAX_MINUTE_SPAN_DAYS = 7


class YFinanceProvider(MarketDataProvider):
    name = "yahoo"

    def __init__(self, prepost: bool = False):
        self.prepost = prepost

    def _history(self, ticker: str, start: dt.date, end_exclusive: dt.date, interval: str) -> pd.DataFrame:
        import yfinance as yf  # imported lazily so the rest of the project works without it

        return yf.Ticker(ticker).history(
            start=start.isoformat(), end=end_exclusive.isoformat(), interval=interval,
            auto_adjust=False, actions=False, prepost=self.prepost,
        )

    def get_intraday_range(self, ticker: str, start: DateLike, end: DateLike, interval: str = "1m") -> pd.DataFrame:
        """Intraday bars for ``start <= date <= end``, fetched in Yahoo-sized chunks."""
        start_d, end_d = parse_date(start), parse_date(end)
        frames = []
        chunk_start = start_d
        while chunk_start <= end_d:
            chunk_end = min(chunk_start + dt.timedelta(days=MAX_MINUTE_SPAN_DAYS - 1), end_d)
            raw = self._history(ticker, chunk_start, chunk_end + dt.timedelta(days=1), interval)
            if not raw.empty:
                frames.append(standardize_ohlcv(raw))
            chunk_start = chunk_end + dt.timedelta(days=1)
        if not frames:
            raise DataUnavailableError(
                f"Yahoo returned no {interval} bars for {ticker} {start_d}..{end_d} "
                "(1m history is limited to about the last 30 days)"
            )
        out = pd.concat(frames)
        return out[~out.index.duplicated(keep="last")].sort_index()

    def get_minute_data(self, ticker: str, date: DateLike) -> pd.DataFrame:
        return self.get_intraday_range(ticker, date, date, "1m")

    def get_daily_data(self, ticker: str, start: DateLike, end: DateLike) -> pd.DataFrame:
        start_d, end_d = parse_date(start), parse_date(end)
        raw = self._history(ticker, start_d, end_d + dt.timedelta(days=1), "1d")
        if raw.empty:
            raise DataUnavailableError(f"Yahoo returned no daily bars for {ticker} {start_d}..{end_d}")
        return standardize_daily(raw)
