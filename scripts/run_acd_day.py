"""Run the ACD one-day simulator (or just print the day's levels) from the command line.

Examples (from the repo root):
    python scripts/run_acd_day.py SPY 2026-09-18 --a-atr 0.1 --c-atr 0.15 --confirm 15
    python scripts/run_acd_day.py SPY 2026-09-18 --a 0.5 --c 0.5 --confirm 15 --html spy.html
    python scripts/run_acd_day.py SPY QQQ IWM 2026-09-18 --levels-only --a-atr 0.1 --c-atr 0.15
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from acd.simulation.runner import compute_day_levels, run_acd_day  # noqa: E402
from common.market_data import get_provider  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("args", nargs="+", help="TICKER [TICKER ...] DATE")
    parser.add_argument("--a", type=float, help="A value in price units")
    parser.add_argument("--c", type=float, help="C value in price units")
    parser.add_argument("--a-atr", type=float, help="A as a multiple of ATR(14)")
    parser.add_argument("--c-atr", type=float, help="C as a multiple of ATR(14)")
    parser.add_argument("--confirm", type=int, help="Confirmation minutes (required unless --levels-only)")
    parser.add_argument("--or-minutes", type=int, default=30)
    parser.add_argument("--keep-a-at-b", action="store_true", help="Don't exit the A trade at point B")
    parser.add_argument("--no-c", action="store_true", help="Disable the C reversal")
    parser.add_argument("--levels-only", action="store_true")
    parser.add_argument("--source", default="yahoo-cached", choices=["yahoo-cached", "yahoo", "csv"])
    parser.add_argument("--html", help="Write the chart to this HTML file (single ticker)")
    parser.add_argument("--json", action="store_true", help="Print the result summary as JSON")
    opts = parser.parse_args(argv)

    *tickers, date = opts.args
    if not tickers:
        parser.error("give at least one ticker and a date")
    provider = get_provider(opts.source)
    pd.set_option("display.width", 160)
    pd.set_option("display.max_rows", 500)

    if opts.levels_only:
        for ticker in tickers:
            levels = compute_day_levels(provider, ticker.upper(), date, opts.or_minutes, opts.a, opts.c,
                                        opts.a_atr, opts.c_atr)
            print(json.dumps(levels.summary(), indent=2, default=str))
        return 0

    if opts.confirm is None:
        parser.error("--confirm is required for a simulation")
    for ticker in tickers:
        result, inputs, config = run_acd_day(
            provider, ticker.upper(), date, opts.confirm, a_value=opts.a, c_value=opts.c,
            a_atr_multiple=opts.a_atr, c_atr_multiple=opts.c_atr, opening_range_minutes=opts.or_minutes,
            exit_on_b=not opts.keep_a_at_b, allow_c_reversal=not opts.no_c,
        )
        if opts.json:
            print(json.dumps(result.summary(), indent=2, default=str))
        else:
            print(f"\n=== {ticker.upper()} {result.date}  A={config.a_value:.4f} C={config.c_value:.4f} "
                  f"confirm={config.confirmation_minutes}m ===")
            for name, value in result.levels_dict().items():
                print(f"  {name:<18}{value:>12.4f}")
            print("\nEvent log:")
            print(result.events_frame().to_string(index=False))
            if result.trades:
                print("\nTrades:")
                print(result.trades_frame().to_string(index=False))
            print(f"\nDay P&L per share: {result.pnl:+.4f} ({100 * result.return_pct:+.3f}%)")
        if opts.html:
            from acd.charts import plot_acd_day

            out = Path(opts.html if len(tickers) == 1 else f"{Path(opts.html).stem}_{ticker.upper()}.html")
            plot_acd_day(inputs.minute_data, result).write_html(out)
            print(f"Chart written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
