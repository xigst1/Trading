"""CAN SLIM (William O'Neil, *How to Make Money in Stocks*) chart checks from daily bars.

Pure functions over the standard daily layout (see common.sessions); no data calls.
Covers the price-based letters: the base and buy point, leadership (relative strength)
and market direction (M). The fundamental letters (C, A, N, S, I) need vendor data
and live in scripts/canslim_view.py.

Simplifications, so read the output as a screen rather than a chart reading:
- The base is everything since the highest high in the lookback window, and the buy
  point is that high. Handles, double bottoms and flat-base shapes are not detected.
- "RS" is IBD-style weighted performance versus a benchmark, not IBD's 1-99 rank,
  which needs the whole market.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

BUY_ZONE_PCT = 5.0       # buy no more than 5% above the buy point
STOP_PCT = (7.0, 8.0)    # cut losses 7-8% below the purchase price
MAX_DEPTH_PCT = 33.0     # typical limit for a sound base; up to ~50% in a bear market
MIN_BASE_WEEKS = 5       # flat base minimum; cups are usually 7+ weeks
BREAKOUT_VOLUME = 1.4    # breakout volume at least 40% above the 50-day average
DISTRIBUTION_DROP = -0.2  # % close-to-close drop that counts as a distribution day


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def base_analysis(daily: pd.DataFrame, lookback: int = 252, breakout_bars: int = 5) -> Dict[str, Any]:
    """Locate the current base and buy point, and say where price is relative to them.

    The base peak is searched before the last ``breakout_bars`` sessions, so a stock that
    is breaking out right now is measured against the base it is leaving.
    """
    window = daily.tail(lookback)
    if len(window) <= breakout_bars:
        raise ValueError(f"need more than {breakout_bars} daily bars, got {len(window)}")
    close = float(window["close"].iloc[-1])
    head = window.iloc[:-breakout_bars]
    peak_t = head["high"].idxmax()
    peak = float(head.at[peak_t, "high"])
    after = window.loc[peak_t:]
    low_t = after["low"].idxmin()
    low = float(after.at[low_t, "low"])
    weeks = (len(after) - 1) / 5

    depth = 100 * (1 - low / peak)
    vol50 = daily["volume"].rolling(50, min_periods=20).mean()
    vol_ratio = daily["volume"].iloc[-1] / vol50.iloc[-1] if vol50.iloc[-1] else None
    pct_from_buy = 100 * (close / peak - 1)

    flaws = []
    if depth > 50:
        flaws.append("too deep (>50%)")
    elif depth > MAX_DEPTH_PCT:
        flaws.append(f"deeper than {MAX_DEPTH_PCT:.0f}% (acceptable only in a bear market)")
    if weeks < MIN_BASE_WEEKS:
        flaws.append(f"too short ({weeks:.1f} weeks)")

    if pct_from_buy > BUY_ZONE_PCT:
        status = "extended above the buy zone"
    elif pct_from_buy >= 0:
        status = "in the buy zone" if not flaws else "above the buy point, but the base is faulty"
    elif depth > 50:
        status = "repairing after a >50% decline: not a buyable base"
    else:
        status = "building the right side of the base"

    return {
        "lookback_bars": len(window),
        "buy_point": _r(peak),
        "buy_point_date": peak_t.date().isoformat(),
        "base_low": _r(low),
        "base_low_date": low_t.date().isoformat(),
        "base_depth_pct": _r(depth, 1),
        "base_weeks": _r(weeks, 1),
        "depth_ok": bool(depth <= MAX_DEPTH_PCT),
        "right_side_recovered_pct": _r(100 * (close - low) / (peak - low), 1) if peak > low else None,
        "pct_from_buy_point": _r(pct_from_buy),
        "buy_zone_top": _r(peak * (1 + BUY_ZONE_PCT / 100)),
        "stop_if_bought_at_buy_point": [_r(peak * (1 - p / 100)) for p in STOP_PCT],
        "last_volume_vs_50d": _r(vol_ratio),
        "breakout_volume_ok": bool(vol_ratio is not None and vol_ratio >= BREAKOUT_VOLUME),
        "base_flaws": flaws,
        "status": status,
    }


def moving_averages(daily: pd.DataFrame) -> Dict[str, Any]:
    close = daily["close"]
    last = float(close.iloc[-1])
    out: Dict[str, Any] = {}
    for period in (21, 50, 200):
        ma = close.rolling(period, min_periods=period).mean().iloc[-1]
        out[f"ma{period}"] = _r(ma)
        out[f"pct_vs_ma{period}"] = _r(100 * (last / ma - 1)) if not pd.isna(ma) else None
    return out


def weighted_performance(close: pd.Series) -> Optional[float]:
    """IBD-style 12-month performance: the latest quarter weighted double (40/20/20/20).

    Uses whatever quarters are available, re-weighted, so recent IPOs still get a number.
    """
    quarters, weights = [], [0.4, 0.2, 0.2, 0.2]
    for q in range(4):
        end, start = len(close) - 1 - 63 * q, len(close) - 1 - 63 * (q + 1)
        if start < 0:
            break
        quarters.append(close.iloc[end] / close.iloc[start] - 1)
    if not quarters:
        return None
    used = weights[:len(quarters)]
    return 100 * sum(w * r for w, r in zip(used, quarters)) / sum(used)


def relative_strength(stock: pd.DataFrame, bench: pd.DataFrame) -> Dict[str, Any]:
    """Weighted performance versus the benchmark, and the RS line versus its own high."""
    joined = pd.concat([stock["close"], bench["close"]], axis=1, keys=["s", "b"]).dropna()
    rs_line = joined["s"] / joined["b"]
    stock_perf, bench_perf = weighted_performance(joined["s"]), weighted_performance(joined["b"])
    tail = rs_line.tail(252)
    return {
        "weighted_perf_pct": _r(stock_perf),
        "benchmark_weighted_perf_pct": _r(bench_perf),
        "outperforming": None if stock_perf is None or bench_perf is None else bool(stock_perf > bench_perf),
        "rs_line_pct_from_high": _r(100 * (rs_line.iloc[-1] / tail.max() - 1)),
        "rs_line_at_new_high": bool(rs_line.iloc[-1] >= tail.max()),
        "history_days": len(joined),
    }


def market_direction(index_daily: pd.DataFrame, window: int = 25) -> Dict[str, Any]:
    """The M in CAN SLIM: index trend and distribution-day count over ``window`` sessions."""
    close, volume = index_daily["close"], index_daily["volume"]
    change = 100 * close.pct_change()
    distribution = (change <= DISTRIBUTION_DROP) & (volume > volume.shift(1))
    count = int(distribution.tail(window).sum())
    mas = moving_averages(index_daily)
    above50 = (mas["pct_vs_ma50"] or 0) > 0
    above200 = (mas["pct_vs_ma200"] or 0) > 0
    if above50 and above200 and count < 5:
        label = "uptrend"
    elif count >= 5 or not above50:
        label = "uptrend under pressure" if above200 else "correction"
    else:
        label = "mixed"
    return {"as_of": index_daily.index[-1].date().isoformat(), "close": _r(close.iloc[-1]), **mas,
            "distribution_days": count, "window": window, "label": label}
