"""S&P 500 daily OHLC for the last two years, as one HTML page: a chart per stock, grouped by sector.

    python scripts/sp500_ohlc_html.py                  # download from Yahoo, write reports/sp500_ohlc_<date>.html
    python scripts/sp500_ohlc_html.py --from-cache     # rebuild the page from the last download (no network)
    python scripts/sp500_ohlc_html.py --limit 20       # quick test with the 20 largest companies

Needs data/universe/sp500.csv (run scripts/update_universe.py once). The download is saved to
data/ohlc/ so the page can be rebuilt without fetching again. Prices are split- and
dividend-adjusted, so a stock split does not look like a crash. Today's bar is left out until the
market has closed, so the page never shows a half-finished day.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from common.config import DATA_DIR  # noqa: E402
from common.ohlc_report import ETF_GROUP, build_payload, render_html  # noqa: E402
from common.universe import add_sector_etfs  # noqa: E402
from common.sessions import MARKET_TZ  # noqa: E402
from common.universe import load_sp500  # noqa: E402

DAYS = 730  # two years
CHUNK = 100  # tickers per Yahoo request
OHLC_DIR = DATA_DIR / "ohlc"
REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"



def download(symbols, start: dt.date, end_inclusive: dt.date, pause: float = 5.0) -> pd.DataFrame:
    """Long table of daily bars: symbol, date, open, high, low, close, volume."""
    import yfinance as yf

    frames = []
    for i in range(0, len(symbols), CHUNK):
        part = symbols[i:i + CHUNK]
        raw = None
        for attempt in range(3):
            try:
                raw = yf.download(part, start=start.isoformat(),
                                  end=(end_inclusive + dt.timedelta(days=1)).isoformat(), interval="1d",
                                  group_by="ticker", auto_adjust=True, actions=False, threads=True,
                                  progress=False)
            except Exception as exc:  # Yahoo rate limits and transient errors: retry with a longer wait
                print(f"  request failed ({type(exc).__name__}); retrying", file=sys.stderr)
                raw = None
            if raw is not None and not raw.empty:
                break
            time.sleep(30 * (attempt + 1))
        if raw is None or raw.empty:
            print(f"  no data for chunk starting {part[0]}", file=sys.stderr)
            continue
        for symbol in part:
            if isinstance(raw.columns, pd.MultiIndex):
                if symbol not in raw.columns.get_level_values(0):
                    continue
                sub = raw[symbol]
            else:
                sub = raw
            sub = sub.dropna(subset=["Close"]).rename(columns=str.lower)
            if sub.empty:
                continue
            frame = sub[["open", "high", "low", "close", "volume"]].copy()
            frame["date"] = pd.to_datetime(frame.index).date
            frame["symbol"] = symbol
            frames.append(frame.reset_index(drop=True))
        print(f"  downloaded {min(i + CHUNK, len(symbols))}/{len(symbols)}")
        if i + CHUNK < len(symbols):
            time.sleep(pause)
    if not frames:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    return pd.concat(frames, ignore_index=True)[["symbol", "date", "open", "high", "low", "close", "volume"]]


def drop_unfinished_today(prices: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Before the 16:00 ET close, today's bar is still moving, so leave it out."""
    if now.time() >= dt.time(16, 0):
        return prices
    return prices[prices["date"] != now.date()]


def latest_cache() -> Path:
    files = sorted(OHLC_DIR.glob("prices_*.csv"))
    if not files:
        raise SystemExit(f"No saved download in {OHLC_DIR}. Run without --from-cache first.")
    return files[-1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-cache", action="store_true", help="Use the last saved download, no network")
    parser.add_argument("--limit", type=int, help="Only the N largest companies (for testing)")
    parser.add_argument("--days", type=int, default=DAYS, help="Calendar days of history (default 730)")
    parser.add_argument("--no-etf", action="store_true", help="Leave out the sector ETF group")
    args = parser.parse_args(argv)

    now = pd.Timestamp.now(MARKET_TZ)
    try:
        universe = load_sp500(refresh_if_missing=False)
    except FileNotFoundError:
        print("No S&P 500 list yet. Run: python scripts/update_universe.py", file=sys.stderr)
        return 1
    universe = universe[["symbol", "name", "sector", "market_cap_rank", "market_cap", "price"]].copy()
    universe = universe.sort_values(["market_cap_rank", "symbol"], na_position="last")
    if args.limit:
        universe = universe.head(args.limit)
    if not args.no_etf:
        universe = add_sector_etfs(universe)

    if args.from_cache:
        cache = latest_cache()
        print(f"Using saved download {cache.name}")
        prices = pd.read_csv(cache, parse_dates=["date"])
    else:
        end = now.date()
        start = end - dt.timedelta(days=args.days)
        print(f"Downloading {len(universe)} stocks, {start} to {end} (adjusted daily bars)")
        prices = download(universe["symbol"].tolist(), start, end)
        prices = drop_unfinished_today(prices, now)
        if prices.empty:
            print("Nothing downloaded (rate limited or market data unavailable). Try again in a few minutes.",
                  file=sys.stderr)
            return 1
        OHLC_DIR.mkdir(parents=True, exist_ok=True)
        cache = OHLC_DIR / f"prices_{end.isoformat()}.csv"
        prices.to_csv(cache, index=False)
        print(f"Saved download -> {cache}")

    payload = build_payload(prices, universe)
    dates = payload["dates"]
    etf_count = sum(len(g["stocks"]) for g in payload["groups"] if g["sector"] == ETF_GROUP)
    stocks = sum(len(g["stocks"]) for g in payload["groups"]) - etf_count
    sectors = len([g for g in payload["groups"] if g["sector"] != ETF_GROUP])
    title = "S&P 500: daily prices, last two years"
    etf_text = f" and {etf_count} sector ETFs" if etf_count else ""
    subtitle = (f"{stocks} stocks in {sectors} sectors{etf_text} · {dates[0]} to {dates[-1]} · "
                f"generated {now:%Y-%m-%d %H:%M} ET")
    out = REPORT_DIR / f"sp500_ohlc_{now.date().isoformat()}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(payload, title, subtitle), encoding="utf-8")

    print(f"{stocks} stocks in {sectors} sectors, {etf_count} sector ETFs, {len(dates)} sessions")
    if payload["missing"]:
        print(f"no price data for {len(payload['missing'])}: {', '.join(payload['missing'][:20])}"
              f"{' ...' if len(payload['missing']) > 20 else ''}")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

