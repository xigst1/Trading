# Turtle Trader

## Layout

| File | Purpose |
|---|---|
| `signals.py` | `system_state` replays System 1 (20/10) or System 2 (55/20) over daily bars. `turtle_view` returns both systems plus N and unit size |
| `simulation/` | Empty, reserved for a multi-day simulator |

Shared helpers come from `common/indicators.py`: `atr(df, 20)` gives the Turtle "N", and `donchian_channel(df, period)` gives the breakout and exit channels without look-ahead. Data comes from `common.market_data`.

## Command line

```
python scripts/turtle_view.py SPCX --equity 100000
```

This prints JSON for each ticker: position, entry, the 2N stop, the next channel exit, pyramid levels at 1/2 N steps, and the next breakout levels. The `turtle-trader` agent in `.claude/agents/` uses it.

## Rules not modelled yet

- System 1's filter that skips a breakout after a winning trade
- Moving the stop up after each added unit
- Portfolio-level unit limits
