"""Yahoo Finance provider (via yfinance).

Yahoo only serves 1-minute bars for roughly the last 30 days, at most ~7 days per
request. Use scripts/download_data.py regularly to archive minute data locally.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List

import pandas as pd

from common.market_data.base import DataUnavailableError, MarketDataProvider
from common.sessions import MARKET_TZ, DateLike, parse_date, standardize_daily, standardize_ohlcv

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

    def get_daily_batch(self, tickers: List[str], start: DateLike, end: DateLike,
                        chunk_size: int = 100) -> Dict[str, pd.DataFrame]:
        """Daily bars for many tickers using yfinance's batched, threaded download.

        Returns {ticker: standard daily frame}; tickers with no data are omitted.
        """
        import yfinance as yf

        start_d, end_d = parse_date(start), parse_date(end)
        out: Dict[str, pd.DataFrame] = {}
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            raw = yf.download(chunk, start=start_d.isoformat(), end=(end_d + dt.timedelta(days=1)).isoformat(),
                              interval="1d", group_by="ticker", auto_adjust=False, actions=False,
                              threads=True, progress=False)
            if raw is None or raw.empty:
                continue
            for ticker in chunk:
                if isinstance(raw.columns, pd.MultiIndex):
                    if ticker not in raw.columns.get_level_values(0):
                        continue
                    sub = raw[ticker]
                else:
                    sub = raw
                sub = sub.dropna(subset=[c for c in ("Open", "High", "Low", "Close") if c in sub.columns])
                if not sub.empty:
                    out[ticker] = standardize_daily(sub)
        return out

    def get_intraday_batch(self, tickers: List[str], date: DateLike, interval: str = "5m",
                           retry_wait_seconds: int = 30) -> Dict[str, pd.DataFrame]:
        """One day of intraday bars for many tickers (yfinance fetches them in parallel,
        roughly one HTTP request per ticker). Retries once if the batch fails or comes back
        empty, which is how Yahoo throttling usually shows up.

        Returns {ticker: standard OHLCV frame}; tickers with no bars are omitted.
        """
        import yfinance as yf

        day = parse_date(date)

        def download():
            return yf.download(tickers, start=day.isoformat(), end=(day + dt.timedelta(days=1)).isoformat(),
                               interval=interval, group_by="ticker", auto_adjust=False, actions=False,
                               prepost=self.prepost, threads=True, progress=False)

        try:
            raw = download()
        except Exception:
            raw = None
        if raw is None or raw.empty:
            time.sleep(retry_wait_seconds)
            raw = download()
        if raw is None or raw.empty:
            return {}

        out: Dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                sub = raw[ticker]
            else:
                sub = raw
            sub = sub.dropna(subset=[c for c in ("Open", "High", "Low", "Close") if c in sub.columns])
            if not sub.empty:
                out[ticker] = standardize_ohlcv(sub)
        return out

    # Yahoo quote-summary field -> our column name.
    FUNDAMENTAL_FIELDS = {
        "marketCap": "market_cap",  # whole company, even for one share class (GOOGL and GOOG both)
        "sharesOutstanding": "shares_outstanding",  # this share class
        "impliedSharesOutstanding": "implied_shares_outstanding",  # all classes combined
        "floatShares": "float_shares",
        "averageVolume": "avg_volume_3m",
        "averageVolume10days": "avg_volume_10d",
        "regularMarketPrice": "price",
    }

    def get_fundamentals(self, tickers: List[str], workers: int = 8, retry_wait_seconds: float = 2.0) -> pd.DataFrame:
        """Latest market cap, shares and average volume per ticker from Yahoo (one request
        each, ``workers`` in parallel, one retry on failure). Missing values are NaN."""
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor

        def fetch(ticker: str) -> Dict:
            for attempt in range(2):
                try:
                    info = yf.Ticker(ticker).info or {}
                    if info.get("marketCap") is not None:
                        break
                except Exception:
                    info = {}
                if attempt == 0:
                    time.sleep(retry_wait_seconds)
            row = {"symbol": ticker}
            row.update({ours: info.get(theirs) for theirs, ours in self.FUNDAMENTAL_FIELDS.items()})
            return row

        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(fetch, tickers))
        df = pd.DataFrame(rows)
        numeric = list(self.FUNDAMENTAL_FIELDS.values())
        df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce")
        df["fundamentals_as_of"] = pd.Timestamp.now(MARKET_TZ).strftime("%Y-%m-%d %H:%M %Z")
        return df

    def get_daily_data(self, ticker: str, start: DateLike, end: DateLike) -> pd.DataFrame:
        start_d, end_d = parse_date(start), parse_date(end)
        raw = self._history(ticker, start_d, end_d + dt.timedelta(days=1), "1d")
        if raw.empty:
            raise DataUnavailableError(f"Yahoo returned no daily bars for {ticker} {start_d}..{end_d}")
        return standardize_daily(raw)
