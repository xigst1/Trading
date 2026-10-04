"""CAN SLIM (William O'Neil) view of one or more tickers, as JSON. Used by the canslim-trader agent.

Examples (from the repo root):
    python scripts/canslim_view.py SPCX
    python scripts/canslim_view.py SPCX NVDA --benchmark SPY --out canslim.json

Chart checks come from canslim.signals. Fundamentals (C, A, S, I) are best effort from
Yahoo: a missing field is reported as null rather than failing the run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from canslim.signals import base_analysis, market_direction, moving_averages, relative_strength  # noqa: E402
from common.market_data import get_provider  # noqa: E402


def _num(value, digits=2):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else round(value, digits)


def _yoy(row):
    """Year-over-year % growth per quarter, newest first, from a statement row.

    Quarters are matched by date (about 365 days earlier) because Yahoo often skips
    quarters, so the row is not a contiguous series. Growth off a negative base is
    measured against its absolute value, so -0.34 -> -0.09 reads as +73.5%.
    """
    values = row.dropna().sort_index(ascending=False)
    out = []
    for period, new in values.items():
        prior = values[(values.index <= period - dt.timedelta(days=345))
                       & (values.index >= period - dt.timedelta(days=385))]
        if prior.empty:
            continue
        old = prior.iloc[0]
        out.append({"period": str(period.date()), "value": _num(new), "year_ago": _num(old),
                    "yoy_pct": _num(100 * (new - old) / abs(old)) if old else None})
    return out


def fundamentals(ticker: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(ticker)
    out: dict = {}
    try:
        info = t.info or {}
        out.update({
            "name": info.get("shortName") or info.get("longName"),
            "eps_quarterly_growth_pct": _num(100 * info["earningsQuarterlyGrowth"]) if info.get("earningsQuarterlyGrowth") is not None else None,
            "revenue_growth_pct": _num(100 * info["revenueGrowth"]) if info.get("revenueGrowth") is not None else None,
            "return_on_equity_pct": _num(100 * info["returnOnEquity"]) if info.get("returnOnEquity") is not None else None,
            "shares_outstanding": info.get("sharesOutstanding"),
            "float_shares": info.get("floatShares"),
            "institutional_ownership_pct": _num(100 * info["heldPercentInstitutions"]) if info.get("heldPercentInstitutions") is not None else None,
            "insider_ownership_pct": _num(100 * info["heldPercentInsiders"]) if info.get("heldPercentInsiders") is not None else None,
        })
    except Exception as exc:
        out["info_error"] = f"{type(exc).__name__}: {exc}"
    try:
        stmt = t.quarterly_income_stmt
        for label, key in (("Diluted EPS", "quarterly_eps"), ("Total Revenue", "quarterly_revenue")):
            if stmt is not None and label in stmt.index:
                row = stmt.loc[label]
                out[key] = [{"period": str(d.date()), "value": _num(v)}
                            for d, v in row.dropna().sort_index(ascending=False).items()]
                out[f"{key}_yoy"] = _yoy(row)
    except Exception as exc:
        out["statement_error"] = f"{type(exc).__name__}: {exc}"
    try:
        annual = t.income_stmt
        if annual is not None and "Diluted EPS" in annual.index:
            out["annual_eps"] = [{"period": str(d.date()), "value": _num(v)}
                                 for d, v in annual.loc["Diluted EPS"].dropna().sort_index(ascending=False).items()]
    except Exception as exc:
        out["annual_error"] = f"{type(exc).__name__}: {exc}"
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--benchmark", default="SPY", help="Index proxy for RS and market direction")
    parser.add_argument("--days", type=int, default=420, help="Calendar days of history to load")
    parser.add_argument("--source", default="yahoo", help="Data source: yahoo, yahoo-cached or csv")
    parser.add_argument("--no-fundamentals", action="store_true")
    parser.add_argument("--out", help="Also write the JSON here")
    args = parser.parse_args(argv)

    provider = get_provider(args.source)
    end = dt.date.today()
    start = end - dt.timedelta(days=args.days)

    def load(ticker):
        return provider.get_daily_data(ticker, start, end).dropna(subset=["close"])

    data: dict = {}
    try:
        bench = load(args.benchmark)
        data["market"] = {"benchmark": args.benchmark, **market_direction(bench)}
    except Exception as exc:
        bench = None
        data["market"] = {"benchmark": args.benchmark, "error": f"{type(exc).__name__}: {exc}"}

    for ticker in (t.upper() for t in args.tickers):
        try:
            daily = load(ticker)
            row = {
                "as_of": daily.index[-1].date().isoformat(),
                "close": _num(daily["close"].iloc[-1]),
                "trading_days": len(daily),
                "base": base_analysis(daily),
                "moving_averages": moving_averages(daily),
            }
            if bench is not None:
                row["relative_strength"] = relative_strength(daily, bench)
            if not args.no_fundamentals:
                row["fundamentals"] = fundamentals(ticker)
            data[ticker] = row
        except Exception as exc:
            data[ticker] = {"error": f"{type(exc).__name__}: {exc}"}

    payload = json.dumps(data, indent=2, default=str)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
