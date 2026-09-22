"""Morning job: S&P 500 opening ranges vs last night's pivot ranges, with A/C levels.

Run right after the opening range ends (20-min OR: from 09:51 ET / 06:51 PT):

    python scripts/acd_morning_or_scan.py
    python scripts/acd_morning_or_scan.py --a-atr 0.1 --c-atr 0.15 --atr-period 14
    python scripts/acd_morning_or_scan.py --date 2026-09-21        # rebuild a past session

Needs the nightly file from scripts/acd_nightly_pivots.py for the previous session.
Keeps every stock. The boolean column or_outside_pr is True when the whole OR is ABOVE or
BELOW the pivot range (False when they overlap) so you can filter on it. Prints the stocks
grouped ABOVE / BELOW / OVERLAPPING with OR / PR / A / C levels and saves them all to
data/acd/morning/or_scan_<date>.xlsx.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from acd.morning_scan import add_size_columns, build_or_table, sort_scan  # noqa: E402
from common.config import DATA_DIR  # noqa: E402
from common.excel import write_table_xlsx  # noqa: E402
from common.market_data import YFinanceProvider  # noqa: E402
from common.sessions import MARKET_TZ, REGULAR_OPEN, market_timestamp  # noqa: E402
from common.universe import load_sp500  # noqa: E402

PIVOT_FILE = re.compile(r"pivot_ranges_(\d{4}-\d{2}-\d{2})\.csv$")  # full-universe files only
INTERVALS = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30}

# Columns written to the .xlsx, in this order. Anything else the scan computes
# (or_bars, expected_bars, other ATRs, ...) is left out of the file.
OUTPUT_COLUMNS = [
    "symbol", "name", "sector", "market_cap_rank", "mkt_cap_b", "shares_out_m", "avg_vol_3m_m", "date",
    "a_up", "a_down", "c_up", "c_down",
    "or_outside_pr", "or_vs_pr", "or_high", "or_low", "or_size", "incomplete_or",
    "pr_low", "pr_high", "a_value", "c_value", "distance_from_pr", "atr14", "pivot_source_date",
]

# Column background colors in the .xlsx: OR columns, pivot-range columns, A/C levels.
OR_FILL, PR_FILL, AC_FILL = "D9D9D9", "BDD7EE", "C6E0B4"


def latest_pivot_file(scan_date: dt.date) -> Path:
    folder = DATA_DIR / "acd" / "pivots"
    candidates = []
    for path in folder.glob("pivot_ranges_*.csv"):
        match = PIVOT_FILE.search(path.name)
        if match and dt.date.fromisoformat(match.group(1)) < scan_date:
            candidates.append((match.group(1), path))
    if not candidates:
        raise SystemExit(f"No pivot file before {scan_date} in {folder}. Run: "
                         f"python scripts/acd_nightly_pivots.py --as-of <previous session date>")
    return max(candidates)[1]


def previous_weekday(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def print_side(df: pd.DataFrame, title: str) -> None:
    print(f"\n=== {title} ({len(df)}) ===")
    if df.empty:
        return
    view = pd.DataFrame({
        "symbol": df["symbol"] + df["incomplete_or"].map({True: "*", False: ""}),
        "sector": df["sector"].fillna("").str.slice(0, 22),
        "mcap(B)": df["mkt_cap_b"], "avgvol(M)": df["avg_vol_3m_m"],
        "OR low": df["or_low"], "OR high": df["or_high"],
        "PR low": df["pr_low"], "PR high": df["pr_high"],
        "A-Up": df["a_up"], "A-Down": df["a_down"], "C-Up": df["c_up"], "C-Down": df["c_down"],
    })
    print(view.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="Session to scan (YYYY-MM-DD), default today")
    parser.add_argument("--or-minutes", type=int, default=20)
    parser.add_argument("--interval", default="5m", choices=list(INTERVALS))
    parser.add_argument("--a-atr", type=float, default=0.10, help="A = multiple x ATR (default 0.10)")
    parser.add_argument("--c-atr", type=float, default=0.15, help="C = multiple x ATR (default 0.15, placeholder)")
    parser.add_argument("--atr-period", type=int, default=14, choices=[5, 10, 14, 20])
    args = parser.parse_args(argv)

    now = pd.Timestamp.now(MARKET_TZ)
    scan_date = dt.date.fromisoformat(args.date) if args.date else now.date()
    interval_minutes = INTERVALS[args.interval]
    if args.or_minutes % interval_minutes:
        parser.error(f"--interval {args.interval} does not divide a {args.or_minutes}-minute opening range")
    if scan_date > now.date():
        parser.error("--date is in the future")
    or_end = market_timestamp(scan_date, REGULAR_OPEN) + pd.Timedelta(minutes=args.or_minutes)
    ready_at = or_end + pd.Timedelta(minutes=1)
    if now < ready_at:
        print(f"Opening range not complete yet. Rerun after {ready_at:%H:%M} ET "
              f"({ready_at.tz_convert('America/Los_Angeles'):%H:%M} PT).")
        return 1

    pivot_path = latest_pivot_file(scan_date)
    pivots = pd.read_csv(pivot_path)
    source_date = dt.date.fromisoformat(PIVOT_FILE.search(pivot_path.name).group(1))
    if source_date < previous_weekday(scan_date):
        print(f"WARNING: newest pivot file is from {source_date}, older than the previous weekday "
              f"{previous_weekday(scan_date)}. Run the nightly job (unless that day was a holiday).")

    tickers = pivots["symbol"].tolist()
    print(f"Scan {scan_date}: {len(tickers)} tickers, OR {args.or_minutes} min from {args.interval} bars, "
          f"pivots from {source_date}, A = {args.a_atr} x ATR{args.atr_period}, C = {args.c_atr} x ATR{args.atr_period}")
    t0 = time.time()
    bars = YFinanceProvider().get_intraday_batch(tickers, scan_date, args.interval)
    print(f"  fetched {len(bars)}/{len(tickers)} tickers in {time.time() - t0:.1f}s")

    table, missing = build_or_table(bars, pivots, scan_date, args.or_minutes, interval_minutes,
                                    args.a_atr, args.c_atr, f"atr{args.atr_period}")
    if table.empty:
        print("No opening ranges computed (market holiday, or no data yet).", file=sys.stderr)
        return 1
    table = sort_scan(table)

    try:
        universe = load_sp500(refresh_if_missing=False)
    except FileNotFoundError:
        universe = pd.DataFrame({"symbol": []})
    if "market_cap" in universe.columns:
        as_of = universe["fundamentals_as_of"].dropna().iloc[0] if "fundamentals_as_of" in universe.columns else "?"
        print(f"  market cap / shares / volume from sp500.csv (fetched {as_of})")
    else:
        print("  WARNING: sp500.csv has no market cap data; run scripts/update_universe.py tonight. "
              "Size columns will be empty.")
    table = add_size_columns(table, universe)

    out_dir = DATA_DIR / "acd" / "morning"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"or_scan_{scan_date}.xlsx"
    atr_col = f"atr{args.atr_period}"  # show the ATR that A and C were built from
    columns = [atr_col if c == "atr14" else c for c in OUTPUT_COLUMNS]
    output = table[columns].round(4)
    prices = ["mkt_cap_b", "shares_out_m", "avg_vol_3m_m", "a_up", "a_down", "c_up", "c_down",
              "or_high", "or_low", "or_size", "pr_low", "pr_high", "a_value", "c_value",
              "distance_from_pr", atr_col]
    fills = {
        **{c: OR_FILL for c in output.columns if c.startswith("or_")},
        **{c: PR_FILL for c in ("pr_low", "pr_high", "pivot_source_date")},
        **{c: AC_FILL for c in ("a_up", "a_down", "c_up", "c_down")},
    }
    write_table_xlsx(output, out_path, sheet_name=f"OR scan {scan_date}",
                     number_formats={c: "#,##0.00" for c in prices}, column_fills=fills)

    pd.set_option("display.width", 200)
    print_side(table[table["or_vs_pr"] == "ABOVE"], "OR ABOVE pivot range (or_outside_pr = True)")
    print_side(table[table["or_vs_pr"] == "BELOW"], "OR BELOW pivot range (or_outside_pr = True)")
    print_side(table[table["or_vs_pr"] == "OVERLAPPING"], "OR OVERLAPPING pivot range (or_outside_pr = False)")

    counts = table["or_vs_pr"].value_counts()
    print(f"\nabove {counts.get('ABOVE', 0)}, below {counts.get('BELOW', 0)}, "
          f"overlapping {counts.get('OVERLAPPING', 0)}, missing {len(missing)}, "
          f"incomplete OR {int(table['incomplete_or'].sum())} (marked *)")
    if missing:
        print(f"missing: {', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''}")
    print(f"Saved all {len(table)} stocks ({int(table['or_outside_pr'].sum())} with or_outside_pr) -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
