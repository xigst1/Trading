"""Market data providers. Strategies consume standard OHLCV frames, never a vendor API.

    Data provider  ->  standard OHLCV DataFrame  ->  strategy / simulator
"""

from common.market_data.base import DataUnavailableError, MarketDataProvider, PreviousDay
from common.market_data.cached_provider import CachedProvider
from common.market_data.csv_provider import CSVProvider, LocalStore
from common.market_data.yfinance_provider import YFinanceProvider


def get_provider(source: str = "yahoo-cached", root=None) -> MarketDataProvider:
    """Factory used by scripts, the Streamlit app and the agent.

    source: "yahoo-cached" (default), "yahoo", or "csv" (local files only).
    """
    if source == "yahoo":
        return YFinanceProvider()
    if source == "csv":
        return CSVProvider(root)
    if source == "yahoo-cached":
        return CachedProvider(YFinanceProvider(), root)
    raise ValueError(f"Unknown data source {source!r}")


__all__ = [
    "CSVProvider", "CachedProvider", "DataUnavailableError", "LocalStore", "MarketDataProvider",
    "PreviousDay", "YFinanceProvider", "get_provider",
]
