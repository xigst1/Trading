import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from common.charts import price_sma_slope_stack
from common.moving_averages import moving_average_frame, recent_slope_flip, slope_state

ROOT = Path(__file__).resolve().parent.parent


def series(values, start="2026-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def load_script():
    spec = importlib.util.spec_from_file_location("make_ma_charts", ROOT / "scripts" / "make_ma_charts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sma_slope_and_percent_slope():
    frame = moving_average_frame(series([10, 11, 12, 13, 14, 15]), windows=[3])
    # SMA_3 is NaN for the first two rows, then the mean of each trailing 3.
    assert frame["SMA_3"].iloc[:2].isna().all()
    assert frame["SMA_3"].iloc[2] == pytest.approx(11.0)
    assert frame["SMA_3"].iloc[-1] == pytest.approx(14.0)
    # The slope is the change in the average, so a 1-per-day rise gives 1.0.
    assert frame["SMA_3_d1"].iloc[-1] == pytest.approx(1.0)
    assert frame["SMA_3_d1_pct"].iloc[-1] == pytest.approx(100 * 1.0 / 14.0)
    assert pd.isna(frame["SMA_3_d1"].iloc[2])  # one row deeper in NaNs than the SMA


def test_several_windows_and_close_column():
    frame = moving_average_frame(series(list(range(1, 61))), windows=[5, 20, 50])
    assert list(frame.columns)[0] == "close"
    for window in (5, 20, 50):
        assert {f"SMA_{window}", f"SMA_{window}_d1", f"SMA_{window}_d1_pct"} <= set(frame.columns)
    assert frame["SMA_50"].notna().sum() == 11  # 60 rows - 49


def test_slope_state_and_flip_detection():
    rising = moving_average_frame(series([1, 2, 3, 4, 5, 6, 7, 8]), windows=[3])
    assert slope_state(rising, 3) == "RISING"
    falling = moving_average_frame(series([8, 7, 6, 5, 4, 3, 2, 1]), windows=[3])
    assert slope_state(falling, 3) == "FALLING"
    assert slope_state(moving_average_frame(series([1, 2]), windows=[3]), 3) == "UNKNOWN"

    # Down then up: the average's slope turns positive on the last bars.
    turned_up = moving_average_frame(series([10, 9, 8, 7, 6, 7, 9, 12, 15]), windows=[3])
    assert recent_slope_flip(turned_up, 3, lookback=3) == "uptrend"
    assert recent_slope_flip(rising, 3) == ""  # never changed sign


def test_empty_and_bad_window_rejected():
    with pytest.raises(ValueError):
        moving_average_frame(pd.Series(dtype=float))
    with pytest.raises(ValueError):
        moving_average_frame(series([1, 2, 3]), windows=[0])


def test_chart_has_three_traces_per_window_and_needs_computed_columns():
    frame = moving_average_frame(series(list(range(1, 40))), windows=[5, 20])
    fig = price_sma_slope_stack(frame, "TEST", windows=[5, 20])
    assert len(fig.data) == 6  # close + SMA + slope, per window
    assert "TEST" in fig.layout.title.text
    with pytest.raises(KeyError):
        price_sma_slope_stack(frame[["close"]], "TEST", windows=[5])


def test_filenames_are_undated_and_filesystem_safe():
    script = load_script()
    assert script.safe_name("^GSPC") == "GSPC"
    assert script.safe_name("BRK-B") == "BRK-B"
    assert "2026" not in script.safe_name("SPY")  # names carry no date, so runs overwrite


def test_watchlist_resolution_order(tmp_path, monkeypatch):
    script = load_script()

    class Args:
        tickers = None
        config = None
        windows = None
        start_date = None

    # 1. explicit --tickers wins and is upper-cased
    args = Args()
    args.tickers = ["spy", "qqq"]
    assert script.load_settings(args)[0] == ["SPY", "QQQ"]

    # 2. a JSON config supplies tickers and settings
    config = tmp_path / "watchlist.json"
    config.write_text(json.dumps({"tickers": ["AAPL"], "windows": [10, 30], "start_date": "2026-02-01"}))
    args = Args()
    args.config = str(config)
    tickers, settings = script.load_settings(args)
    assert tickers == ["AAPL"] and settings["windows"] == [10, 30] and settings["start_date"] == "2026-02-01"

    # 3. flags override the config file
    args.windows = [7]
    assert script.load_settings(args)[1]["windows"] == [7]

    # 4. with no file, TRADING_TICKERS is used
    monkeypatch.setattr(script, "DEFAULT_CONFIG", tmp_path / "missing.json")
    monkeypatch.setenv("TRADING_TICKERS", "msft, nvda")
    assert script.load_settings(Args())[0] == ["MSFT", "NVDA"]

    # 5. nothing anywhere is a clear error, not a silent empty run
    monkeypatch.delenv("TRADING_TICKERS")
    monkeypatch.setattr(script, "ROOT", tmp_path)  # so no real .env is found
    with pytest.raises(SystemExit):
        script.load_settings(Args())


def test_env_file_parser_ignores_comments_and_quotes(tmp_path):
    script = load_script()
    (tmp_path / ".env").write_text('# a comment\nTRADING_TICKERS="SPY QQQ"\nOTHER=x\n\n')
    values = script.read_env_file(tmp_path / ".env")
    assert values["TRADING_TICKERS"] == "SPY QQQ" and values["OTHER"] == "x"
    assert script.read_env_file(tmp_path / "nope.env") == {}
