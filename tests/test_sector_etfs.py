import pandas as pd

from acd.morning_scan import add_size_columns
from common.universe import SECTOR_ETF_GROUP, SECTOR_ETFS, add_sector_etfs, sector_etf_frame


def test_eleven_sector_etfs_one_per_sector():
    frame = sector_etf_frame()
    assert len(frame) == 11 and frame["symbol"].is_unique
    assert set(frame["sector"]) == {SECTOR_ETF_GROUP}
    assert len({sector for _, sector in SECTOR_ETFS}) == 11


def test_add_sector_etfs_appends_once_and_keeps_stocks():
    stocks = pd.DataFrame([{"symbol": "AAPL", "name": "Apple", "sector": "Information Technology"}])
    once = add_sector_etfs(stocks)
    twice = add_sector_etfs(once)
    assert len(once) == 12 and len(twice) == 12
    assert list(once["symbol"][:1]) == ["AAPL"]
    assert once["symbol"].is_unique


def test_morning_size_join_leaves_etf_size_columns_blank_not_dropped():
    table = pd.DataFrame({"symbol": ["AAPL", "XLK"], "sector": ["Information Technology", "ETF"]})
    universe = pd.DataFrame([{"symbol": "AAPL", "market_cap": 4.9e12, "shares_outstanding": 1.5e10,
                              "avg_volume_3m": 4.6e7, "market_cap_rank": 2}])
    joined = add_size_columns(table, universe)
    assert list(joined["symbol"]) == ["AAPL", "XLK"]
    assert pd.isna(joined.loc[joined["symbol"] == "XLK", "mkt_cap_b"]).all()
