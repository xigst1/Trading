"""Refresh the S&P 500 constituent list (data/universe/sp500.csv).

    python scripts/update_universe.py

Index membership changes a few times a quarter; refresh before a nightly scan.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.universe import refresh_sp500, sp500_path  # noqa: E402


def main() -> int:
    df = refresh_sp500()
    print(f"{len(df)} S&P 500 constituents -> {sp500_path()}")
    print(df["sector"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
