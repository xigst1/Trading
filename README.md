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

For the agent, set `ANTHROPIC_API_KEY` or run `ant auth login`. The Agent page has a model dropdown — high (`claude-opus-5`), medium (`claude-sonnet-5`, the default) or low (`claude-haiku-4-5`) cost — and shows the estimated cost of each answer plus a session total. API usage is billed to your Anthropic Console credits, separately from any Claude subscription.

## Usage

```bash
# Download data. Yahoo keeps only ~30 days of 1m bars, so run this regularly to build an archive.
python scripts/download_data.py SPY QQQ --minute-days 29

# Simulate one ACD day and write an annotated chart
python scripts/run_acd_day.py SPY 2026-09-18 --a-atr 0.1 --c-atr 0.15 --confirm 15 --html spy.html

# Nightly (after the close): S&P 500 daily bars -> next session's pivot ranges
#   -> data/acd/pivots/pivot_ranges_<session>.csv
python scripts/update_universe.py        # S&P 500 list + market cap, shares, avg volume, market-cap rank
python scripts/acd_nightly_pivots.py

# Morning (after the 20-min OR, from 09:51 ET / 06:51 PT): ORs vs pivot ranges, A/C levels
#   -> data/acd/morning/or_scan_<date>.xlsx, all stocks; or_outside_pr = OR fully above/below PR
python scripts/acd_morning_or_scan.py

# By hand, any time from 10:00 ET / 07:00 PT (after the 09:30-09:50 OR): keep only stocks whose latest 2+ five-minute bars
#   in a row (the one still in progress counts) are entirely above A-Up or entirely below A-Down; bars_checked is the
#   actual run length counted back from the latest bar -> data/acd/morning/post_or_hold_<date>_<HHMM>.xlsx.
#   Close or_scan_<date>.xlsx in Excel first.
#   Options: --last-bars 3 (minimum run; 0 = back to 09:55), --completed-only, --date D --as-of 11:36 (replay a past session)
python scripts/acd_post_or_filter.py

# Levels only (pivot range, OR, A/C) for several tickers
python scripts/run_acd_day.py SPY QQQ IWM 2026-09-18 --levels-only --a-atr 0.1 --c-atr 0.15

# UI
streamlit run app/streamlit_app.py

# Tests
python -m pytest
```

Set `TRADING_DATA_DIR` to keep the data archive somewhere other than `./data`.

## Perspective agents (Claude Code)

Three subagents in `.claude/agents/` each apply one investor's published rules to a ticker. Each runs a script that prints the numbers as JSON, then reports hold / add / exit in its own terms. They are rule-based screens, not investment advice, and they cannot reproduce anyone's judgment.

| Agent | Perspective | Uses | Script |
|---|---|---|---|
| `turtle-trader` | Richard Dennis: price-only trend following, breakout and 2N stop | daily bars | `scripts/turtle_view.py` |
| `canslim-trader` | William O'Neil: earnings growth, base and buy point, relative strength, market direction | daily bars, Yahoo fundamentals, a few searches | `scripts/canslim_view.py` |
| `link-investor` | Stephanie Link (Hightower), inferred from her public interviews: quality on sale, forward P/E against growth, operating-margin direction | daily bars, Yahoo fundamentals, a few searches | `scripts/link_view.py` |

Start a new Claude Code session so the agents load, then ask, for example: "Run turtle-trader, canslim-trader and link-investor on SPCX and compare." Running them as subagents uses the same login as the session (your subscription, or API credits if you logged in with the API).
