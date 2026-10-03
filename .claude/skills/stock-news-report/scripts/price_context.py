"""Price context for one or more tickers, for the stock-news-report skill.

    python price_context.py AAPL MSFT --out /tmp/price.json

Prints a readable summary and writes JSON keyed by ticker. Every field is best effort:
a ticker that fails gets an "error" entry instead of stopping the run, because a report
with partial price context still beats no report.

Uses the last completed session. When Yahoo has not published that day's close yet (it
sometimes serves a NaN close for hours), the close is rebuilt from that session's
5-minute bars, and "close_source" says so.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys

MARKET_TZ = "America/New_York"


def _pct(new, old):
    if new is None or old is None or not old or (isinstance(old, float) and math.isnan(old)):
        return None
    return round(100 * (new / old - 1), 2)


def _round(value, digits=2):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else round(value, digits)


def _atr(df, period=14):
    """Wilder ATR of the last row, or None when there is too little history."""
    if len(df) < period + 1:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev = close.shift(1)
    tr = (high - low).combine((high - prev).abs(), max).combine((low - prev).abs(), max)
    values = tr.dropna().tolist()
    if len(values) < period:
        return None
    atr = sum(values[:period]) / period
    for value in values[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def _last_intraday_close(ticker, day, yf):
    """Close of the last regular-session bar, used when Yahoo's daily close is missing."""
    try:
        bars = yf.Ticker(ticker).history(start=day.isoformat(), end=(day + dt.timedelta(days=1)).isoformat(),
                                         interval="5m", prepost=False, auto_adjust=False, actions=False)
    except Exception:
        return None
    if bars is None or bars.empty:
        return None
    idx = bars.index
    idx = idx.tz_localize(MARKET_TZ) if idx.tz is None else idx.tz_convert(MARKET_TZ)
    session = bars[(idx.time >= dt.time(9, 30)) & (idx.time < dt.time(16, 0))]
    closes = session["Close"].dropna()
    return float(closes.iloc[-1]) if len(closes) else None


def context_for(ticker, yf, pd):
    hist = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=False, actions=False)
    if hist is None or hist.empty:
        raise ValueError(f"no daily history for {ticker}")
    hist = hist.dropna(subset=["Open", "High", "Low"])

    close_source = "daily bar"
    if pd.isna(hist["Close"].iloc[-1]):
        day = hist.index[-1].date()
        rebuilt = _last_intraday_close(ticker, day, yf)
        if rebuilt is None:
            hist = hist.dropna(subset=["Close"])
        else:
            hist.loc[hist.index[-1], "Close"] = rebuilt
            close_source = "last 5m bar (Yahoo daily close not published yet)"

    closes = hist["Close"].dropna()
    last = float(closes.iloc[-1])
    as_of = closes.index[-1].date()

    def ago(n):
        return float(closes.iloc[-1 - n]) if len(closes) > n else None

    year_high, year_low = float(hist["High"].max()), float(hist["Low"].min())
    volumes = hist["Volume"].dropna()
    atr = _atr(hist)

    out = {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "close": _round(last),
        "close_source": close_source,
        "change_1d_pct": _pct(last, ago(1)),
        "change_5d_pct": _pct(last, ago(5)),
        "change_1m_pct": _pct(last, ago(21)),
        "change_3m_pct": _pct(last, ago(63)),
        "change_1y_pct": _pct(last, ago(251)),
        "week_52_high": _round(year_high),
        "week_52_low": _round(year_low),
        "pct_from_52w_high": _pct(last, year_high),
        "pct_above_52w_low": _pct(last, year_low),
        "atr14": _round(atr),
        "atr14_pct_of_close": _round(100 * atr / last) if atr else None,
        "avg_volume_20d": int(volumes.tail(20).mean()) if len(volumes) else None,
        "last_volume_vs_20d_avg": _round(float(volumes.iloc[-1]) / float(volumes.tail(20).mean())) if len(volumes) >= 20 else None,
    }

    # Best effort extras: these endpoints are flaky, so never let them break the run.
    try:
        info = yf.Ticker(ticker).info or {}
        out["name"] = info.get("shortName") or info.get("longName")
        out["market_cap"] = info.get("marketCap")
        out["trailing_pe"] = _round(info.get("trailingPE"))
        out["next_earnings"] = info.get("earningsDate") if isinstance(info.get("earningsDate"), str) else None
    except Exception:
        pass
    try:
        calendar = yf.Ticker(ticker).calendar
        dates = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
        if dates:
            first = dates[0] if isinstance(dates, (list, tuple)) else dates
            out["next_earnings"] = str(getattr(first, "isoformat", lambda: first)())
    except Exception:
        pass
    return out


def summarize(data):
    lines = []
    for ticker, row in data.items():
        if "error" in row:
            lines.append(f"{ticker}: {row['error']}")
            continue
        parts = [f"{ticker} {row['close']} on {row['as_of']}"]
        for label, key in (("1d", "change_1d_pct"), ("5d", "change_5d_pct"), ("1m", "change_1m_pct")):
            if row.get(key) is not None:
                parts.append(f"{label} {row[key]:+.2f}%")
        if row.get("pct_from_52w_high") is not None:
            parts.append(f"{row['pct_from_52w_high']:+.1f}% vs 52w high")
        if row.get("atr14") is not None:
            parts.append(f"ATR14 {row['atr14']}")
        if row["close_source"] != "daily bar":
            parts.append(f"[{row['close_source']}]")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--out", help="Write JSON here (default: print it)")
    args = parser.parse_args(argv)

    try:
        import pandas as pd
        import yfinance as yf
    except ImportError as exc:
        print(f"price context unavailable: {exc}. Install with: pip install yfinance", file=sys.stderr)
        return 2

    data = {}
    for ticker in args.tickers:
        ticker = ticker.upper()
        try:
            data[ticker] = context_for(ticker, yf, pd)
        except Exception as exc:
            data[ticker] = {"ticker": ticker, "error": f"{type(exc).__name__}: {exc}"}

    print(summarize(data))
    payload = json.dumps(data, indent=2, default=str)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(payload + "\n")
        print(f"\nwrote {args.out}")
    else:
        print("\n" + payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
