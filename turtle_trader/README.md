# Turtle Trader

Placeholder. Nothing is implemented yet.

- `simulation/` is empty and reserved for the simulator.
- Shared helpers the strategy will likely need already exist in `common/indicators.py`:
  - `atr(df, period=20, method="wilder")` gives the Turtle "N".
  - `donchian_channel(df, period)` gives breakout channels, e.g. 20/55-day entries and 10/20-day exits. It excludes the current bar by default, so there is no look-ahead.
- Data comes from `common.market_data`, the same providers the ACD code uses.
