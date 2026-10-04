"""Turtle (Richard Dennis) view of one or more tickers, as JSON. Used by the turtle-trader agent.

Examples (from the repo root):
    python scripts/turtle_view.py SPCX
    python scripts/turtle_view.py SPCX NVDA --equity 250000 --out turtle.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.market_data import get_provider  # noqa: E402
from turtle_trader.signals import turtle_view  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--equity", type=float, default=100_000.0, help="Account size for unit sizing")
    parser.add_argument("--days", type=int, default=400, help="Calendar days of history to load")
    parser.add_argument("--source", default="yahoo", help="Data source: yahoo, yahoo-cached or csv")
    parser.add_argument("--out", help="Also write the JSON here")
    args = parser.parse_args(argv)

    provider = get_provider(args.source)
    end = dt.date.today()
    data = {}
    for ticker in (t.upper() for t in args.tickers):
        try:
            daily = provider.get_daily_data(ticker, end - dt.timedelta(days=args.days), end)
            data[ticker] = turtle_view(daily.dropna(subset=["close"]), equity=args.equity)
        except Exception as exc:
            data[ticker] = {"error": f"{type(exc).__name__}: {exc}"}

    payload = json.dumps(data, indent=2)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
