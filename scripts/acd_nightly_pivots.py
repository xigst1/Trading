"""Nightly job: daily OHLC for the S&P 500 and the pivot range for the next session.

Run any time after the close (after 16:15 ET the day's bar is used; earlier, the
previous session's):

    python scripts/acd_nightly_pivots.py
    python scripts/acd_nightly_pivots.py --refresh-universe       # re-pull the S&P 500 list first
    python scripts/acd_nightly_pivots.py --as-of 2026-09-18       # rebuild for a past session
    python scripts/acd_nightly_pivots.py --tickers AAPL MSFT NVDA  # custom list

Output: data/acd/pivots/pivot_ranges_<source_date>.csv, where source_date is the
session whose high/low/close were used. The ranges apply to the NEXT session.
Daily bars are also merged into the local archive (data/daily/) unless --no-archive.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from acd.pivot_scan import build_pivot_table, last_completed_session_cutoff  # noqa: E402
from common.config import DATA_DIR  # noqa: E402
from common.market_data import LocalStore, YFinanceProvider  # noqa: E402
from common.universe import load_sp500, refresh_sp500  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", nargs="+", help="Tickers to scan instead of the S&P 500")
    parser.add_argument("--sector", help="Only this GICS sector, e.g. Energy")
    parser.add_argument("--as-of", help="Use the last session on or before this date (YYYY-MM-DD)")
    parser.add_argument("--refresh-universe", action="store_true", help="Re-download the S&P 500 list first")
    # Wilder ATR needs a long run-in to converge; 150 days (~100 bars) keeps ATR20 within ~2%.
    parser.add_argument("--history-days", type=int, default=150, help="Calendar days of daily history (for ATR)")
    parser.add_argument("--no-archive", action="store_true", help="Don't merge daily bars into data/daily/")
    parser.add_argument("--out-dir", default=None, help="Default: data/acd/pivots")
    args = parser.parse_args(argv)

    universe = refresh_sp500() if args.refresh_universe else load_sp500()
    if args.tickers:
        tickers = [t.upper().replace(".", "-") for t in args.tickers]
    else:
        subset = universe
        if args.sector:
            subset = universe[universe["sector"].str.lower() == args.sector.lower()]
            if subset.empty:
                parser.error(f"No tickers in sector {args.sector!r}; sectors: {sorted(universe['sector'].unique())}")
        tickers = subset["symbol"].tolist()

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else last_completed_session_cutoff()
    start = as_of - dt.timedelta(days=args.history_days)
    print(f"Fetching daily bars for {len(tickers)} tickers, {start} .. {as_of} ...")
    t0 = time.time()
    daily = YFinanceProvider().get_daily_batch(tickers, start, as_of)
    print(f"  got {len(daily)}/{len(tickers)} tickers in {time.time() - t0:.1f}s")

    table, errors = build_pivot_table(daily, as_of, universe)
    missing = sorted(set(tickers) - set(daily) | set(errors))
    if table.empty:
        print("No pivot ranges computed.", file=sys.stderr)
        return 1

    source_date = table.loc[~table["stale"], "source_date"].iloc[0]
    out_dir = Path(args.out_dir) if args.out_dir else DATA_DIR / "acd" / "pivots"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Full-universe runs get the plain name that the morning scan will look for.
    if args.tickers:
        suffix = "_custom"
    elif args.sector:
        suffix = "_" + args.sector.lower().replace(" ", "_")
    else:
        suffix = ""
    out_path = out_dir / f"pivot_ranges_{source_date}{suffix}.csv"
    table.round(4).to_csv(out_path, index=False)

    if not args.no_archive:
        store = LocalStore()
        for symbol, df in daily.items():
            store.save_daily(symbol, df)

    print(f"\nPivot ranges from the {source_date} session (for the next session) -> {out_path}")
    print(f"  {len(table)} tickers, {int(table['stale'].sum())} stale (last bar before {source_date}), "
          f"{len(missing)} missing")
    if missing:
        print(f"  missing: {', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''}")
    stale = table[table["stale"]]
    if not stale.empty:
        print(f"  stale: {', '.join(f'{s} ({d})' for s, d in zip(stale['symbol'], stale['source_date']))}")

    fresh = table[~table["stale"]]
    print("\nPivot range vs previous day's:")
    print(fresh["pr_vs_previous_pr"].value_counts().to_string())
    cols = ["symbol", "prev_close", "pr_low", "pr_high", "pr_width_pct", "atr5", "atr10", "atr14", "atr20"]
    print("\nNarrowest pivot ranges (width % of close):")
    print(fresh.nsmallest(10, "pr_width_pct")[cols].round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    sys.exit(main())
