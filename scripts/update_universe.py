"""Refresh the S&P 500 constituent list (data/universe/sp500.csv).

    python scripts/update_universe.py                    # list + market cap, shares, avg volume, rank
    python scripts/update_universe.py --no-fundamentals  # list only (Wikipedia, no Yahoo calls)

Index membership changes a few times a quarter; market cap and volume change daily, so
run it after the close whenever you want fresh numbers (~20 s, one Yahoo request per ticker).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from common.universe import refresh_sp500, sp500_path  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-fundamentals", action="store_true", help="Skip market cap / shares / volume")
    args = parser.parse_args(argv)

    t0 = time.time()
    df = refresh_sp500(with_fundamentals=not args.no_fundamentals)
    print(f"{len(df)} S&P 500 constituents -> {sp500_path()} ({time.time() - t0:.1f}s)")
    if "market_cap" not in df.columns:
        return 0

    missing = df.loc[df["market_cap"].isna(), "symbol"].tolist()
    print(f"market cap for {len(df) - len(missing)}/{len(df)}"
          + (f"; missing: {', '.join(missing)}" if missing else ""))
    for col in ("shares_outstanding", "avg_volume_3m", "avg_volume_10d"):
        print(f"  {col}: {int(df[col].notna().sum())}/{len(df)}")

    top = df.head(15)[["market_cap_rank", "symbol", "name", "market_cap", "avg_volume_3m"]].copy()
    top["market_cap"] = (top["market_cap"] / 1e9).map("{:,.0f}B".format)
    top["avg_volume_3m"] = (top["avg_volume_3m"] / 1e6).map("{:,.1f}M".format)
    print("\nLargest by market cap:")
    print(top.to_string(index=False))
    return 0


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    sys.exit(main())
