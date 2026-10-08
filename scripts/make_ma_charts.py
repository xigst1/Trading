"""Price + moving-average charts for a watchlist, one HTML and one PNG per stock.

    python scripts/make_ma_charts.py                 # uses config/watchlist.json
    python scripts/make_ma_charts.py --tickers SPY QQQ NVDA
    python scripts/make_ma_charts.py --config ~/my_watchlist.json --windows 10 50 200

Files are written to linechart/<TICKER>.html and linechart/<TICKER>.png with no date in
the name, so each run overwrites the previous one and you always keep exactly one current
copy per stock. Both the output folder and the watchlist file are git-ignored.

Where the ticker list comes from (first match wins):
  1. --tickers on the command line
  2. --config <file>
  3. config/watchlist.json
  4. TRADING_TICKERS in the environment or in a .env file at the repo root
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

# Shared with the nightly ACD job: Yahoo sometimes publishes a NaN close for the latest
# session, and these rebuild that bar from intraday data instead of silently losing a day.
from acd.pivot_scan import last_completed_session_cutoff, patch_missing_session, symbols_missing_session  # noqa: E402
from common.charts import price_sma_slope_stack, write_full_height_html  # noqa: E402
from common.market_data import YFinanceProvider  # noqa: E402
from common.moving_averages import DEFAULT_WINDOWS, moving_average_frame, recent_slope_flip, slope_state  # noqa: E402

DEFAULT_CONFIG = ROOT / "config" / "watchlist.json"
DEFAULT_OUTDIR = ROOT / "linechart"
DEFAULT_LOOKBACK_DAYS = 400  # enough for a 200-day average plus its slope


def read_env_file(path: Path) -> Dict[str, str]:
    """Minimal KEY=VALUE reader, so a .env works without adding a dependency."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(args) -> Tuple[List[str], dict]:
    """Resolve the ticker list and chart settings from flags, JSON config or .env."""
    settings: dict = {}

    if args.tickers:
        tickers = list(args.tickers)
    else:
        config_path = Path(args.config).expanduser() if args.config else DEFAULT_CONFIG
        if config_path.exists():
            settings = json.loads(config_path.read_text())
            tickers = settings.get("tickers") or []
            if not tickers:
                raise SystemExit(f"{config_path} has no 'tickers' list")
        else:
            raw = os.environ.get("TRADING_TICKERS") or read_env_file(ROOT / ".env").get("TRADING_TICKERS", "")
            tickers = [t for t in (s.strip() for s in raw.replace(",", " ").split()) if t]
            if not tickers:
                raise SystemExit(
                    f"No watchlist found. Create {config_path} (copy config/watchlist.example.json), "
                    "set TRADING_TICKERS in .env, or pass --tickers. Both files are git-ignored."
                )

    settings["tickers"] = [t.upper() for t in tickers]
    if args.windows:
        settings["windows"] = args.windows
    settings.setdefault("windows", list(DEFAULT_WINDOWS))
    if args.start_date:
        settings["start_date"] = args.start_date
    settings.setdefault("lookback_days", DEFAULT_LOOKBACK_DAYS)
    return settings["tickers"], settings


def safe_name(ticker: str) -> str:
    """Filename for a ticker: ^GSPC -> GSPC, BRK-B stays as is."""
    return ticker.lstrip("^").replace("/", "-").replace("\\", "-")


def fetch_daily(tickers: Sequence[str], start: dt.date, end: dt.date,
                intraday_fallback: bool = True) -> Dict[str, pd.DataFrame]:
    provider = YFinanceProvider()
    daily = provider.get_daily_batch(list(tickers), start, end)
    if not intraday_fallback or not daily:
        return daily
    stale = symbols_missing_session(daily, end)
    if stale and end.weekday() < 5:
        print(f"  no daily bar for {end} on {len(stale)} ticker(s); rebuilding from 5m bars ...")
        rebuilt = patch_missing_session(daily, provider.get_session_bars_from_intraday(stale, end), end)
        print(f"  rebuilt {len(rebuilt)}/{len(stale)}")
    return daily


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", nargs="+", help="Tickers to chart, instead of the config file")
    parser.add_argument("--config", help=f"Watchlist JSON (default: {DEFAULT_CONFIG})")
    parser.add_argument("--windows", nargs="+", type=int, help="Moving-average windows (default 5 20 50)")
    parser.add_argument("--start-date", help="First date to chart (YYYY-MM-DD)")
    parser.add_argument("--outdir", default=None, help=f"Default: {DEFAULT_OUTDIR}")
    parser.add_argument("--png-size", nargs=2, type=int, default=(1040, 1760), metavar=("W", "H"),
                        help="PNG size in pixels (default portrait 1040x1760)")
    parser.add_argument("--no-png", action="store_true", help="Write only the HTML")
    parser.add_argument("--pct-slope", action="store_true",
                        help="Plot slopes as percent of the average instead of price units")
    parser.add_argument("--no-intraday-fallback", action="store_true",
                        help="Don't rebuild a missing latest daily bar from intraday bars")
    args = parser.parse_args(argv)

    tickers, settings = load_settings(args)
    windows = settings["windows"]
    end = last_completed_session_cutoff()
    start = (dt.date.fromisoformat(settings["start_date"]) if settings.get("start_date")
             else end - dt.timedelta(days=int(settings["lookback_days"])))
    outdir = Path(args.outdir).expanduser() if args.outdir else DEFAULT_OUTDIR
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"{len(tickers)} tickers, {start} .. {end}, SMA windows {windows} -> {outdir}")
    daily = fetch_daily(tickers, start, end, intraday_fallback=not args.no_intraday_fallback)
    print(f"  fetched {len(daily)}/{len(tickers)}")

    rows, failures = [], []
    for ticker in tickers:
        frame_in = daily.get(ticker)
        if frame_in is None or frame_in.empty:
            failures.append((ticker, "no price data"))
            continue
        try:
            frame = moving_average_frame(frame_in["close"], windows)
            fig = price_sma_slope_stack(frame, ticker, windows, use_pct=args.pct_slope)
            stem = outdir / safe_name(ticker)
            write_full_height_html(fig, stem.with_suffix(".html"), title=f"{ticker} price and SMAs")
            if not args.no_png:
                fig.write_image(stem.with_suffix(".png"), width=args.png_size[0], height=args.png_size[1])
        except Exception as exc:
            failures.append((ticker, f"{type(exc).__name__}: {exc}"))
            continue

        row = {"ticker": ticker, "last": round(float(frame["close"].iloc[-1]), 2),
               "date": frame.index[-1].date().isoformat()}
        for window in windows:
            row[f"SMA_{window}"] = slope_state(frame, window)
            flip = recent_slope_flip(frame, window)
            if flip:
                row[f"SMA_{window}"] += f" ({flip} flip)"
        rows.append(row)

    if rows:
        pd.set_option("display.width", 200)
        print("\n" + pd.DataFrame(rows).to_string(index=False))
    print(f"\nWrote {len(rows)} chart pair(s) to {outdir} "
          f"({'HTML only' if args.no_png else 'HTML + PNG'}, names not dated - each run overwrites)")
    for ticker, reason in failures:
        print(f"  skipped {ticker}: {reason}", file=sys.stderr)
    return 1 if failures and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
