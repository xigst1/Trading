# Trading

Local trading research: download prices from Yahoo Finance, compute strategy signals, simulate them, and explore everything in a Streamlit UI with a built-in agent.

## Layout

```
common/            shared helpers used by every strategy
  market_data/     provider interface, Yahoo (yfinance), local CSV store, caching wrapper
  sessions.py      US/Eastern session handling, OHLCV standardization, intraday validation
  indicators.py    true range, ATR (Wilder/SMA), ATR as of a date, Donchian channels
  charts.py        Plotly candlesticks, levels, bands, markers
acd/               Mark Fisher's ACD: levels, signals, charts   (see acd/README.md)
  simulation/      one-day ACD state-machine simulator
turtle_trader/     Turtle Trader (placeholder)
  simulation/      (empty)
agent/             Claude-powered agent with local tools (prices, ATR, ACD levels, simulation)
app/               Streamlit UI
scripts/           command-line entry points
tests/             pytest suite (synthetic data, no network)
data/              downloaded market data (git-ignored)
```

To add a strategy, create a top-level folder next to `acd/` with its own `simulation/` subfolder. Put helpers that more than one strategy needs in `common/`.

## Setup

The agent needs `anthropic` 1.x, which requires Python ≥ 3.10. The system Python on this Mac is 3.9, so use a virtual environment:

```bash
python3.11 -m venv .venv          # or: uv venv --python 3.11
source .venv/bin/activate
pip install -r requirements.txt
```

For the agent, set `ANTHROPIC_API_KEY` or run `ant auth login`. The agent uses `claude-opus-5` with the server-side refusal fallback enabled.

## Usage

```bash
# Download data. Yahoo keeps only ~30 days of 1m bars, so run this regularly to build an archive.
python scripts/download_data.py SPY QQQ --minute-days 29

# Simulate one ACD day and write an annotated chart
python scripts/run_acd_day.py SPY 2026-09-18 --a-atr 0.1 --c-atr 0.15 --confirm 15 --html spy.html

# Nightly (after the close): S&P 500 daily bars -> next session's pivot ranges
#   -> data/acd/pivots/pivot_ranges_<session>.csv
python scripts/update_universe.py        # refresh the S&P 500 list (occasionally)
python scripts/acd_nightly_pivots.py

# Morning (after the 20-min OR, from 09:51 ET / 06:51 PT): ORs vs pivot ranges, A/C levels
#   -> data/acd/morning/or_scan_<date>.xlsx, all stocks; or_outside_pr = OR fully above/below PR
python scripts/acd_morning_or_scan.py

# Levels only (pivot range, OR, A/C) for several tickers
python scripts/run_acd_day.py SPY QQQ IWM 2026-09-18 --levels-only --a-atr 0.1 --c-atr 0.15

# UI
streamlit run app/streamlit_app.py

# Tests
python -m pytest
```

Set `TRADING_DATA_DIR` to keep the data archive somewhere other than `./data`.
