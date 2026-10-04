"""Turtle (Richard Dennis) position view from daily bars.

Pure functions over the standard daily layout (see common.sessions); no data calls.
Rules as published in *The Original Turtle Trading Rules* (Faith):

- N = 20-day Wilder ATR.
- System 1: enter on a break of the 20-day high/low, exit on the opposite 10-day extreme.
- System 2: enter on a break of the 55-day high/low, exit on the opposite 20-day extreme.
- Stop: 2N from the entry price. Add a unit every 1/2 N in favour, up to 4 units.
- Unit: the share count where a 1 N move equals 1% of account equity.

Not modelled: System 1's "skip the signal after a winning breakout" filter, pyramided
stops (each add moves the stop up), and portfolio-level unit limits.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from common.indicators import atr, donchian_channel

SYSTEMS = {"S1": (20, 10), "S2": (55, 20)}
N_PERIOD = 20
MAX_UNITS = 4


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def system_state(daily: pd.DataFrame, entry_period: int, exit_period: int,
                 n: Optional[pd.Series] = None) -> Dict[str, Any]:
    """Replay one Turtle system bar by bar and return where it stands after the last bar.

    Entries fill at the breakout level (or the open, if the bar gaps through it). Exits
    fill when the bar trades through the exit channel or the 2N stop, whichever is closer.
    """
    n = atr(daily, N_PERIOD) if n is None else n
    entry_ch = donchian_channel(daily, entry_period)
    exit_ch = donchian_channel(daily, exit_period)

    position, entry_date, entry_price, entry_n = 0, None, None, None
    last_exit: Optional[Dict[str, Any]] = None
    for t, bar in daily.iterrows():
        up, down = entry_ch.at[t, "upper"], entry_ch.at[t, "lower"]
        if position == 0:
            if pd.isna(up) or pd.isna(n.at[t]):
                continue
            if bar["high"] > up:
                position, entry_price = 1, max(bar["open"], up)
            elif bar["low"] < down:
                position, entry_price = -1, min(bar["open"], down)
            else:
                continue
            entry_date, entry_n = t, n.at[t]
            continue

        if position == 1:
            stop = max(exit_ch.at[t, "lower"], entry_price - 2 * entry_n)
            if bar["low"] < stop:
                last_exit = {"date": t.date().isoformat(), "side": "long", "price": _r(min(bar["open"], stop)),
                             "entry_price": _r(entry_price)}
                position = 0
        else:
            stop = min(exit_ch.at[t, "upper"], entry_price + 2 * entry_n)
            if bar["high"] > stop:
                last_exit = {"date": t.date().isoformat(), "side": "short", "price": _r(max(bar["open"], stop)),
                             "entry_price": _r(entry_price)}
                position = 0

    # Levels for the next session use the channel including the last bar.
    next_entry = donchian_channel(daily, entry_period, exclude_current=False).iloc[-1]
    next_exit = donchian_channel(daily, exit_period, exclude_current=False).iloc[-1]
    close = float(daily["close"].iloc[-1])

    out: Dict[str, Any] = {
        "entry_period": entry_period,
        "exit_period": exit_period,
        "position": {1: "long", -1: "short", 0: "flat"}[position],
        "next_long_entry": _r(next_entry["upper"]),
        "next_short_entry": _r(next_entry["lower"]),
        "pct_to_long_entry": _r(100 * (next_entry["upper"] / close - 1)),
        "last_exit": last_exit,
    }
    if position:
        sign = position
        stop_2n = entry_price - sign * 2 * entry_n
        channel_exit = next_exit["lower"] if sign == 1 else next_exit["upper"]
        out.update({
            "entry_date": entry_date.date().isoformat(),
            "entry_price": _r(entry_price),
            "n_at_entry": _r(entry_n),
            "open_pnl_pct": _r(100 * sign * (close / entry_price - 1)),
            "stop_2n": _r(stop_2n),
            "next_channel_exit": _r(channel_exit),
            "effective_exit": _r(max(stop_2n, channel_exit) if sign == 1 else min(stop_2n, channel_exit)),
            "add_levels": [_r(entry_price + sign * k * 0.5 * entry_n) for k in range(1, MAX_UNITS)],
        })
    return out


def turtle_view(daily: pd.DataFrame, equity: float = 100_000.0) -> Dict[str, Any]:
    """Both Turtle systems plus N and unit size for ``equity``."""
    if len(daily) < max(e for e, _ in SYSTEMS.values()) + 1:
        raise ValueError(f"need more than {max(e for e, _ in SYSTEMS.values())} daily bars, got {len(daily)}")
    n = atr(daily, N_PERIOD)
    last_n = float(n.iloc[-1])
    close = float(daily["close"].iloc[-1])
    unit_shares = int(0.01 * equity / last_n) if last_n > 0 else 0
    return {
        "as_of": daily.index[-1].date().isoformat(),
        "close": _r(close),
        "n": _r(last_n),
        "n_pct_of_close": _r(100 * last_n / close),
        "equity": equity,
        "unit_shares": unit_shares,
        "unit_value": _r(unit_shares * close, 0),
        "systems": {name: system_state(daily, e, x, n) for name, (e, x) in SYSTEMS.items()},
    }
