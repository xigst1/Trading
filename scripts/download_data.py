"""Download daily and/or 1-minute bars from Yahoo Finance into the local data folder.

Examples (from the repo root):
    python scripts/download_data.py SPY QQQ                   # last 7 days of 1m + 2 years of daily
    python scripts/download_data.py SPY --minute-days 29       # as much 1m history as Yahoo allows
    python scripts/download_data.py AAPL --no-minute --daily-start 2015-01-01

Yahoo keeps only ~30 days of 1-minute bars, so schedule this (e.g. weekly cron) to
build a longer local archive.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.market_data import LocalStore, YFinanceProvider  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--minute-days", type=int, default=7, help="Calendar days of 1m history (max ~29)")
    parser.add_argument("--no-minute", action="store_true")
    parser.add_argument("--no-daily", action="store_true")
    parser.add_argument("--daily-start", default=None, help="YYYY-MM-DD, default two years ago")
    parser.add_argument("--data-dir", default=None, help="Override the data folder")
    args = parser.parse_args(argv)

    provider = YFinanceProvider()
    store = LocalStore(args.data_dir)
    today = dt.date.today()
    failures = 0

    for ticker in (t.upper() for t in args.tickers):
        if not args.no_daily:
            start = dt.date.fromisoformat(args.daily_start) if args.daily_start else today - dt.timedelta(days=730)
            try:
                daily = provider.get_daily_data(ticker, start, today)
                # Don't archive today's still-forming bar.
                path = store.save_daily(ticker, daily[daily.index.date < today])
                print(f"{ticker}: {len(daily)} daily bars -> {path}")
            except Exception as exc:
                failures += 1
                print(f"{ticker}: daily download failed: {exc}", file=sys.stderr)
        if not args.no_minute:
            days = min(args.minute_days, 29)
            try:
                minute = provider.get_intraday_range(ticker, today - dt.timedelta(days=days), today, "1m")
                paths = store.save_minute(ticker, minute)
                print(f"{ticker}: {len(minute)} minute bars, {len(paths)} day files written under "
                      f"{store.root / 'minute' / ticker}")
            except Exception as exc:
                failures += 1
                print(f"{ticker}: minute download failed: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
