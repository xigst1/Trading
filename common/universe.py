"""Ticker universes (e.g. S&P 500 constituents) for scans across many stocks.

The S&P 500 list is scraped from Wikipedia and cached as data/universe/sp500.csv.
Symbols are converted to Yahoo format (BRK.B -> BRK-B).
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from common.config import DATA_DIR

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Wikipedia rejects requests without a browser-like User-Agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (trading-research script)"}


def sp500_path(root: Union[str, Path, None] = None) -> Path:
    return (Path(root) if root is not None else DATA_DIR) / "universe" / "sp500.csv"


def to_yahoo_symbol(symbol: str) -> str:
    """Yahoo uses '-' for share classes: BRK.B -> BRK-B, BF.B -> BF-B."""
    return symbol.strip().upper().replace(".", "-")


def fetch_sp500() -> pd.DataFrame:
    """Current constituents from Wikipedia: symbol, name, sector, sub_industry, cik, date_added."""
    request = urllib.request.Request(SP500_URL, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8")
    table = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
    table = table.rename(columns={
        "Symbol": "symbol", "Security": "name", "GICS Sector": "sector",
        "GICS Sub-Industry": "sub_industry", "CIK": "cik", "Date added": "date_added",
    })
    table["symbol"] = table["symbol"].map(to_yahoo_symbol)
    cols = [c for c in ("symbol", "name", "sector", "sub_industry", "cik", "date_added") if c in table.columns]
    return table[cols].sort_values("symbol").reset_index(drop=True)


def refresh_sp500(root: Union[str, Path, None] = None) -> pd.DataFrame:
    """Fetch the list and save it; returns the frame."""
    df = fetch_sp500()
    path = sp500_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def load_sp500(root: Union[str, Path, None] = None, refresh_if_missing: bool = True) -> pd.DataFrame:
    path = sp500_path(root)
    if path.exists():
        return pd.read_csv(path)
    if not refresh_if_missing:
        raise FileNotFoundError(f"{path} not found; run scripts/update_universe.py")
    return refresh_sp500(root)


def sp500_tickers(root: Union[str, Path, None] = None, sector: Optional[str] = None) -> List[str]:
    df = load_sp500(root)
    if sector:
        df = df[df["sector"].str.lower() == sector.lower()]
    return df["symbol"].tolist()
