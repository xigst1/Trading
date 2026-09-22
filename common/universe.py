"""Ticker universes (e.g. S&P 500 constituents) for scans across many stocks.

The S&P 500 list is scraped from Wikipedia and cached as data/universe/sp500.csv, with
the latest market cap, shares outstanding and average volume from Yahoo and a market-cap
rank. Symbols are converted to Yahoo format (BRK.B -> BRK-B).
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


SIZE_COLUMNS = [
    "market_cap_rank", "market_cap", "shares_outstanding", "implied_shares_outstanding", "float_shares",
    "avg_volume_3m", "avg_volume_10d", "avg_dollar_volume_3m", "price",
]


def add_market_cap_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Rank companies by market cap, largest = 1.

    Yahoo reports the whole company's market cap on every share class (GOOGL and GOOG
    both show Alphabet's total), so classes of one company (same CIK) share one rank
    instead of taking two places. Tickers without a market cap get no rank.
    """
    out = df.copy()
    company = out["cik"] if "cik" in out.columns else out["symbol"]
    company_cap = out.groupby(company)["market_cap"].transform("max")
    out["market_cap_rank"] = company_cap.rank(method="dense", ascending=False).astype("Int64")
    return out


def fetch_sp500(with_fundamentals: bool = True, provider=None) -> pd.DataFrame:
    """Current constituents from Wikipedia, optionally with Yahoo market cap, shares and
    average volume, ranked by market cap (largest first)."""
    table = fetch_sp500_list()
    if not with_fundamentals:
        return table
    if provider is None:
        from common.market_data import YFinanceProvider

        provider = YFinanceProvider()
    fundamentals = provider.get_fundamentals(table["symbol"].tolist())
    table = table.merge(fundamentals, on="symbol", how="left")
    table["avg_dollar_volume_3m"] = table["price"] * table["avg_volume_3m"]
    table = add_market_cap_rank(table)
    first = ["symbol", "name", "sector"] + SIZE_COLUMNS
    table = table[first + [c for c in table.columns if c not in first]]
    return table.sort_values(["market_cap_rank", "symbol"], na_position="last").reset_index(drop=True)


def fetch_sp500_list() -> pd.DataFrame:
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


def refresh_sp500(root: Union[str, Path, None] = None, with_fundamentals: bool = True) -> pd.DataFrame:
    """Fetch the list (with market cap etc. unless disabled) and save it; returns the frame."""
    df = fetch_sp500(with_fundamentals)
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
