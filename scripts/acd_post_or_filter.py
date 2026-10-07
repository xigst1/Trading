"""Post-OR filter: keep stocks whose latest 5-minute bars are beyond A-Up / A-Down, and show for how long.

Run by hand any time after the opening range, e.g. 10:00 ET / 07:00 PT or 11:36 ET, after the morning scan:

    python scripts/acd_post_or_filter.py                                    # at least 2 bars in a row
    python scripts/acd_post_or_filter.py --last-bars 3
    python scripts/acd_post_or_filter.py --last-bars 0                      # every bar since --start
    python scripts/acd_post_or_filter.py --completed-only                   # ignore the bar in progress
    python scripts/acd_post_or_filter.py --date 2026-10-06 --as-of 11:36    # replay a past session

Reads data/acd/morning/or_scan_<date>.xlsx, fetches that day's 5-minute bars for every symbol in it.
Going back from the latest bar one bar at a time, it counts the bars in a row that are entirely above
a_up (bar low > a_up) or entirely below a_down (bar high < a_down) on the same side. A stock is kept
when that run is at least --last-bars long (default 2); bars_checked is the run's actual length, with
first_bar and last_bar as its ends. Earlier bars only end the run. The bar still in progress counts
unless --completed-only is given (its result can still flip; the last_bar_partial column marks
those rows). Bars start no earlier than --start (default 09:55 ET; the OR is 09:30-09:50). A stock
whose run stops at a missing bar before reaching the minimum is listed but not kept.
Saves data/acd/morning/post_or_hold_<date>_<HHMM ET>.xlsx (with _lastN / _all / _completed added for
non-default options) and prints the two groups.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from acd.post_or_filter import (ABOVE, BELOW, DEFAULT_LAST_BARS, evaluate_holds, expected_starts,  # noqa: E402
                                latest_bar_start)
from common.config import DATA_DIR  # noqa: E402
from common.excel import write_table_xlsx  # noqa: E402
from common.market_data import YFinanceProvider  # noqa: E402
from common.sessions import MARKET_TZ, REGULAR_OPEN, market_timestamp, parse_time  # noqa: E402

INTERVALS = {"1m": 1, "2m": 2, "5m": 5}  # must divide the time from the open to --start
LEADING_COLUMNS = ["symbol", "name", "sector", "market_cap_rank", "mkt_cap_b", "shares_out_m", "avg_vol_3m_m",
                   "side", "a_up", "a_down", "bars_checked", "first_bar", "last_bar", "last_bar_partial",
                   "worst_extreme", "cushion", "cushion_atr", "last_close"]
HOLD_COLUMNS = ("side", "bars_checked", "first_bar", "last_bar", "last_bar_partial", "worst_extreme",
                "cushion", "cushion_atr", "last_close")
HOLD_FILL, AC_FILL = "FFF2CC", "C6E0B4"
LIST_LIMIT = 20


def print_side(df: pd.DataFrame, title: str) -> None:
    print(f"\n=== {title} ({len(df)}) ===")
    if df.empty:
        return
    view = pd.DataFrame({
        "symbol": df["symbol"], "sector": df["sector"].fillna("").str.slice(0, 22),
        "mcap(B)": df.get("mkt_cap_b"), "A-Up": df["a_up"], "A-Down": df["a_down"],
        "worst": df["worst_extreme"], "cushion": df["cushion"], "cushion/ATR": df["cushion_atr"],
        "last": df["last_close"], "bars": df["bars_checked"],
        "run": df["first_bar"] + "-" + df["last_bar"] + df["last_bar_partial"].map({True: "*", False: ""}),
    })
    print(view.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="Session (YYYY-MM-DD), default today")
    parser.add_argument("--start", default="09:55", help="First bar to check, HH:MM ET (default 09:55)")
    parser.add_argument("--interval", default="5m", choices=list(INTERVALS))
    parser.add_argument("--last-bars", type=int, default=DEFAULT_LAST_BARS,
                        help=f"Minimum run of latest bars beyond the level (default {DEFAULT_LAST_BARS}); "
                             "0 = the run must reach back to --start")
    parser.add_argument("--completed-only", action="store_true", help="Ignore the bar still in progress")
    parser.add_argument("--as-of", help="Treat HH:MM ET as the current time (to replay a past session)")
    parser.add_argument("--scan-file", type=Path, help="OR scan workbook (default data/acd/morning/or_scan_<date>.xlsx)")
    args = parser.parse_args(argv)

    now = pd.Timestamp.now(MARKET_TZ)
    scan_date = dt.date.fromisoformat(args.date) if args.date else now.date()
    interval_minutes = INTERVALS[args.interval]
    if scan_date > now.date():
        parser.error("--date is in the future")
    as_of = market_timestamp(scan_date, args.as_of) if args.as_of else now
    if as_of > now:
        parser.error("--as-of is in the future")
    open_ts, start_ts = market_timestamp(scan_date, REGULAR_OPEN), market_timestamp(scan_date, parse_time(args.start))
    if (start_ts - open_ts) % pd.Timedelta(minutes=interval_minutes):
        parser.error(f"--interval {args.interval} does not divide the time from the open to --start {args.start}")

    if args.last_bars < 0:
        parser.error("--last-bars must be 0 or more")
    include_partial = not args.completed_only
    needed = max(args.last_bars, 1)
    step = pd.Timedelta(minutes=interval_minutes)
    ready_at = start_ts + step * (needed - 1 if include_partial else needed)
    if expected_starts(scan_date, args.start, interval_minutes, as_of, args.last_bars, include_partial).empty:
        print(f"Fewer than {needed} {args.interval} bar(s) from {args.start} so far"
              f"{'' if include_partial else ' (finished bars only)'}. Rerun after {ready_at:%H:%M} ET "
              f"({ready_at.tz_convert('America/Los_Angeles'):%H:%M} PT).")
        return 1
    rule = (f"latest {args.last_bars}+ bars in a row" if args.last_bars else f"every bar since {args.start}") + \
        (" (incl. bar in progress)" if include_partial else " (finished bars only)")

    scan_path = args.scan_file or DATA_DIR / "acd" / "morning" / f"or_scan_{scan_date}.xlsx"
    if not scan_path.exists():
        print(f"No OR scan at {scan_path}. Run: python scripts/acd_morning_or_scan.py"
              f"{f' --date {scan_date}' if scan_date != now.date() else ''}", file=sys.stderr)
        return 1
    try:
        scan = pd.read_excel(scan_path)
    except PermissionError:
        print(f"Cannot read {scan_path.name}: it is locked, probably open in Excel. Close it and rerun.", file=sys.stderr)
        return 1
    missing_cols = [c for c in ("symbol", "a_up", "a_down") if c not in scan.columns]
    if missing_cols:
        print(f"{scan_path.name} has no column(s): {', '.join(missing_cols)}", file=sys.stderr)
        return 1

    tickers = scan["symbol"].tolist()
    print(f"Post-OR filter {scan_date}: {len(tickers)} tickers from {scan_path.name}, {args.interval} bars, "
          f"{rule}, as of {as_of:%H:%M} ET ({as_of.tz_convert('America/Los_Angeles'):%H:%M} PT)")
    t0 = time.time()
    bars = YFinanceProvider().get_intraday_batch(tickers, scan_date, args.interval)
    print(f"  fetched {len(bars)}/{len(tickers)} tickers in {time.time() - t0:.1f}s")
    newest = latest_bar_start(bars, scan_date)
    if newest is None:
        print("No bars returned (market holiday, or no data yet).", file=sys.stderr)
        return 1
    if not args.as_of:  # live run: show how fresh Yahoo's data is
        last_expected = (now if include_partial else now - step).floor(f"{interval_minutes}min")
        note = "" if newest >= last_expected else f" (Yahoo is behind: expected {last_expected:%H:%M}; consider rerunning)"
        print(f"  newest bar starts {newest:%H:%M} ET{note}")

    kept, no_data, gaps = evaluate_holds(bars, scan, scan_date, args.start, interval_minutes, as_of,
                                         args.last_bars, include_partial)

    ordered = [c for c in LEADING_COLUMNS if c in kept.columns]
    output = kept[ordered + [c for c in kept.columns if c not in ordered]].round(4)
    # Non-default options go in the name so reruns with other options do not overwrite each other.
    suffix = ("" if args.last_bars == DEFAULT_LAST_BARS else "_all" if args.last_bars == 0 else f"_last{args.last_bars}") \
        + ("_completed" if args.completed_only else "")
    out_path = DATA_DIR / "acd" / "morning" / f"post_or_hold_{scan_date}_{as_of:%H%M}{suffix}.xlsx"
    prices = ["mkt_cap_b", "shares_out_m", "avg_vol_3m_m", "a_up", "a_down", "c_up", "c_down", "worst_extreme",
              "cushion", "cushion_atr", "last_close", "or_high", "or_low", "or_size", "pr_low", "pr_high"]
    fills = {**{c: HOLD_FILL for c in HOLD_COLUMNS if c in output.columns},
             **{c: AC_FILL for c in ("a_up", "a_down", "c_up", "c_down") if c in output.columns}}
    try:
        write_table_xlsx(output, out_path, sheet_name=f"Hold {scan_date} {as_of:%H%M}",
                         number_formats={c: "#,##0.00" for c in prices if c in output.columns}, column_fills=fills)
    except PermissionError:
        print(f"Cannot write {out_path.name}: it is open in Excel. Close it and rerun.", file=sys.stderr)
        return 1

    pd.set_option("display.width", 200)
    print_side(kept[kept["side"] == ABOVE], "Latest bars entirely ABOVE A-Up")
    print_side(kept[kept["side"] == BELOW], "Latest bars entirely BELOW A-Down")
    if len(no_data) > max(5, len(tickers) // 20):
        print(f"\nWARNING: {len(no_data)} of {len(tickers)} symbols returned no bars. Yahoo is probably rate-limiting "
              f"(many fetches in a row); wait a minute or two and rerun before trusting this list.")
    print(f"\nkept {len(kept)} of {len(tickers)} (above {int((kept['side'] == ABOVE).sum())}, "
          f"below {int((kept['side'] == BELOW).sum())}); gaps {len(gaps)}, no data {len(no_data)}")
    if include_partial and (kept["last_bar_partial"].any() if len(kept) else False):
        print("* = last bar still in progress; its result can change before it closes")
    for label, symbols in (("gaps (run stopped at a missing bar, not confirmed)", gaps), ("no data", no_data)):
        if symbols:
            print(f"{label}: {', '.join(symbols[:LIST_LIMIT])}{' ...' if len(symbols) > LIST_LIMIT else ''}")
    print(f"Saved -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
