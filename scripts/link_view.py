"""Stephanie Link-style fundamentals checklist for one or more tickers, as JSON. Used by the link-investor agent.

Examples (from the repo root):
    python scripts/link_view.py SPCX
    python scripts/link_view.py SPCX NVDA --out link.json

Yahoo fundamentals are best effort: a missing field is null and a failing ticker gets an "error"
entry, so one bad symbol never stops the run. See link_style/signals.py for what each field means.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.market_data import get_provider  # noqa: E402
from link_style.signals import link_view  # noqa: E402


def fundamentals(ticker: str):
    """(info dict, quarterly income statement or None) from Yahoo; either may be empty."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    try:
        quarterly = t.quarterly_income_stmt
    except Exception:
        quarterly = None
    return info, quarterly


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--days", type=int, default=420, help="Calendar days of daily history to load")
    parser.add_argument("--source", default="yahoo", help="Price data source: yahoo, yahoo-cached or csv")
    parser.add_argument("--out", help="Also write the JSON here")
    args = parser.parse_args(argv)

    provider = get_provider(args.source)
    end = dt.date.today()
    data = {}
    for ticker in (t.upper() for t in args.tickers):
        try:
            daily = provider.get_daily_data(ticker, end - dt.timedelta(days=args.days), end).dropna(subset=["close"])
            info, quarterly = fundamentals(ticker)
            data[ticker] = {"name": info.get("shortName") or info.get("longName"), **link_view(daily, info, quarterly)}
        except Exception as exc:
            data[ticker] = {"error": f"{type(exc).__name__}: {exc}"}

    payload = json.dumps(data, indent=2, default=str)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
