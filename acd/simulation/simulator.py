"""One-ticker, one-day ACD simulator implemented as an explicit state machine.

Bars are processed strictly in time order; the decision at bar ``t`` uses only bars
``<= t`` plus the previous session's H/L/C. Timestamps are bar *start* times, and a
decision that uses a bar's close is effective at the end of that bar.

Intrabar assumptions (1-minute bars have no tick order):
  * A bar that touches both A-Up and A-Down is ambiguous and ignored (logged).
  * A touch bar counts towards confirmation in the same bar.
  * After an entry, point B is only checked from the next bar on.
  * Crossing B and touching C in the same bar is allowed (C lies beyond B).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd

from acd.levels import (ACDLevels, InsufficientDataError, OpeningRange, PivotRange, calculate_acd_levels,
                        calculate_daily_pivot_range, calculate_opening_range)
from acd.signals import (DOWN, UP, ConfirmationResult, confirm_a_down, confirm_a_up, confirm_c_down,
                         confirm_c_up, detect_level_touch)
from acd.simulation.config import ACDConfig
from acd.simulation.execution import LONG, SHORT, ExecutionModel, LevelExecution, calculate_pnl
from common.sessions import (DateLike, filter_regular_session, parse_date, standardize_ohlcv,
                             validate_intraday_data)


class State(str, Enum):
    WAITING_FOR_A = "WAITING_FOR_A"
    A_UP_CANDIDATE = "A_UP_CANDIDATE"
    A_DOWN_CANDIDATE = "A_DOWN_CANDIDATE"
    LONG_A = "LONG_A"
    SHORT_A = "SHORT_A"
    WAITING_FOR_C_DOWN = "WAITING_FOR_C_DOWN"
    WAITING_FOR_C_UP = "WAITING_FOR_C_UP"
    C_DOWN_CANDIDATE = "C_DOWN_CANDIDATE"
    C_UP_CANDIDATE = "C_UP_CANDIDATE"
    SHORT_C = "SHORT_C"
    LONG_C = "LONG_C"
    DONE = "DONE"


@dataclass
class Trade:
    setup: str  # "A" or "C"
    direction: str  # LONG / SHORT
    entry_time: pd.Timestamp
    entry_level: float
    entry_price: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    return_pct: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class ACDDayResult:
    ticker: str
    date: dt.date

    or_high: float
    or_low: float
    or_mid: float

    pivot: float
    pivot_low: float
    pivot_high: float

    a_up: float
    a_down: float
    c_up: float
    c_down: float

    trade_taken: bool
    # The first trade of the day (always the A trade; a C reversal is in ``trades``).
    direction: Optional[str]
    entry_time: Optional[pd.Timestamp]
    entry_price: Optional[float]
    exit_time: Optional[pd.Timestamp]
    exit_price: Optional[float]
    exit_reason: Optional[str]

    # Totals over all trades of the day (A trade + optional C reversal).
    pnl: float
    return_pct: float  # total pnl / first entry price

    events: List[Dict[str, Any]]
    trades: List[Trade] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    final_state: str = State.DONE.value
    opening_range: Optional[OpeningRange] = None
    pivot_range: Optional[PivotRange] = None
    levels: Optional[ACDLevels] = None

    def events_frame(self) -> pd.DataFrame:
        df = pd.DataFrame(self.events, columns=["time", "event", "price", "state", "detail"])
        if not df.empty:
            df["time"] = pd.to_datetime(df["time"]).dt.strftime("%H:%M")
        return df

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(t) for t in self.trades])

    def levels_dict(self) -> Dict[str, float]:
        return {
            "OR high": self.or_high, "OR low": self.or_low, "OR mid": self.or_mid,
            "A up": self.a_up, "A down": self.a_down, "C up": self.c_up, "C down": self.c_down,
            "Pivot": self.pivot, "Pivot range low": self.pivot_low, "Pivot range high": self.pivot_high,
        }

    def summary(self) -> Dict[str, Any]:
        """JSON-friendly summary (used by scripts and the agent)."""
        return {
            "ticker": self.ticker, "date": self.date.isoformat(), "trade_taken": self.trade_taken,
            "levels": {k: round(v, 4) for k, v in self.levels_dict().items()},
            "trades": [
                {
                    "setup": t.setup, "direction": t.direction,
                    "entry_time": _fmt_time(t.entry_time), "entry_price": round(t.entry_price, 4),
                    "exit_time": _fmt_time(t.exit_time),
                    "exit_price": None if t.exit_price is None else round(t.exit_price, 4),
                    "exit_reason": t.exit_reason, "pnl_per_share": round(t.pnl, 4),
                    "return_pct": round(100 * t.return_pct, 4),
                }
                for t in self.trades
            ],
            "total_pnl_per_share": round(self.pnl, 4), "total_return_pct": round(100 * self.return_pct, 4),
            "final_state": self.final_state, "warnings": self.warnings,
        }


def _fmt_time(ts: Optional[pd.Timestamp]) -> Optional[str]:
    return None if ts is None else ts.strftime("%H:%M")


class _ACDDayMachine:
    def __init__(self, bars: pd.DataFrame, levels: ACDLevels, config: ACDConfig, execution: ExecutionModel):
        self.bars = bars
        self.levels = levels
        self.config = config
        self.execution = execution
        self.state: Optional[State] = None
        self.events: List[Dict[str, Any]] = []
        self.trades: List[Trade] = []
        self.position: Optional[Trade] = None
        self.candidate_start: Optional[int] = None
        self.i = -1

    # --- logging -----------------------------------------------------------------
    def log(self, ts, event: str, price: Optional[float] = None, detail: str = "") -> None:
        self.events.append({"time": ts, "event": event, "price": None if price is None else float(price),
                            "state": self.state.value if self.state else None, "detail": detail})

    def transition(self, ts, new_state: State, reason: str = "") -> None:
        old = self.state.value if self.state else "START"
        self.state = new_state
        detail = f"{old} -> {new_state.value}" + (f" ({reason})" if reason else "")
        self.log(ts, "STATE_CHANGE", detail=detail)

    # --- positions -----------------------------------------------------------------
    def open_position(self, bar, direction: str, setup: str, level: float) -> None:
        price = self.execution.entry_price(direction, level, bar)
        self.position = Trade(setup=setup, direction=direction, entry_time=bar.Index, entry_level=level,
                              entry_price=price)
        self.trades.append(self.position)
        self.log(bar.Index, f"{direction}_ENTRY", price, f"{setup} trade at level {level:.4f}")

    def close_position(self, bar, reference_price: float, reason: str) -> None:
        trade = self.position
        if trade is None:
            return
        trade.exit_time = bar.Index
        trade.exit_price = self.execution.exit_price(trade.direction, reference_price, bar)
        trade.exit_reason = reason
        trade.pnl, trade.return_pct = calculate_pnl(trade.direction, trade.entry_price, trade.exit_price,
                                                    self.config.commission)
        self.position = None
        self.log(bar.Index, f"{trade.direction}_EXIT", trade.exit_price, f"{reason}; pnl {trade.pnl:+.4f}")

    # --- main loop -----------------------------------------------------------------
    def run(self) -> None:
        self.transition(self.bars.index[0], State.WAITING_FOR_A, "opening range complete")
        for self.i, bar in enumerate(self.bars.itertuples()):
            # A handler returns True when the same bar should be re-evaluated in the new state.
            for _ in range(4):
                if not self.step(bar):
                    break
        self.end_of_day(next(self.bars.iloc[[-1]].itertuples()))

    def step(self, bar) -> bool:
        handler = {
            State.WAITING_FOR_A: self.on_waiting_for_a,
            State.A_UP_CANDIDATE: lambda b: self.on_candidate(b, UP, "A"),
            State.A_DOWN_CANDIDATE: lambda b: self.on_candidate(b, DOWN, "A"),
            State.LONG_A: lambda b: self.on_in_a_trade(b, LONG),
            State.SHORT_A: lambda b: self.on_in_a_trade(b, SHORT),
            State.WAITING_FOR_C_DOWN: lambda b: self.on_waiting_for_c(b, DOWN),
            State.WAITING_FOR_C_UP: lambda b: self.on_waiting_for_c(b, UP),
            State.C_DOWN_CANDIDATE: lambda b: self.on_candidate(b, DOWN, "C"),
            State.C_UP_CANDIDATE: lambda b: self.on_candidate(b, UP, "C"),
        }.get(self.state)
        # LONG_C / SHORT_C / DONE: nothing to do intraday in V1 (held to the close).
        return handler(bar) if handler else False

    def on_waiting_for_a(self, bar) -> bool:
        up = detect_level_touch(bar.high, bar.low, self.levels.a_up, UP)
        down = detect_level_touch(bar.high, bar.low, self.levels.a_down, DOWN)
        if up and down:
            self.log(bar.Index, "AMBIGUOUS_BAR", detail="bar touched both A-Up and A-Down; ignored")
            return False
        if not (up or down):
            return False
        self.candidate_start = self.i
        if up:
            self.log(bar.Index, "A_UP_TOUCHED", bar.high, f"high {bar.high:.4f} >= A-Up {self.levels.a_up:.4f}")
            self.transition(bar.Index, State.A_UP_CANDIDATE)
        else:
            self.log(bar.Index, "A_DOWN_TOUCHED", bar.low, f"low {bar.low:.4f} <= A-Down {self.levels.a_down:.4f}")
            self.transition(bar.Index, State.A_DOWN_CANDIDATE)
        return True

    def _confirm(self, direction: str, setup: str) -> ConfirmationResult:
        window = self.bars.iloc[self.candidate_start: self.i + 1]  # touch bar .. current bar only
        cfg = self.config
        if setup == "A":
            level = self.levels.a_up if direction == UP else self.levels.a_down
            fn = confirm_a_up if direction == UP else confirm_a_down
            minutes = cfg.confirmation_minutes
        else:
            level = self.levels.c_up if direction == UP else self.levels.c_down
            fn = confirm_c_up if direction == UP else confirm_c_down
            minutes = cfg.effective_c_confirmation_minutes
        return fn(window, level, minutes, cfg.confirmation_price)

    def on_candidate(self, bar, direction: str, setup: str) -> bool:
        result = self._confirm(direction, setup)
        name = f"{setup}_{direction}"
        trade_dir = LONG if direction == UP else SHORT
        if result.confirmed:
            self.candidate_start = None
            self.log(bar.Index, f"{name}_CONFIRMED", result.level,
                     f"held {result.minutes_held} min beyond {result.level:.4f}")
            if setup == "C" and self.position is not None:
                self.close_position(bar, result.level, "REVERSED_AT_C")
            self.open_position(bar, trade_dir, setup, result.level)
            new_state = {("A", LONG): State.LONG_A, ("A", SHORT): State.SHORT_A,
                         ("C", LONG): State.LONG_C, ("C", SHORT): State.SHORT_C}[(setup, trade_dir)]
            self.transition(bar.Index, new_state, f"{name} confirmed")
        elif result.failed:
            self.candidate_start = None
            self.log(bar.Index, f"{name}_FAILED", bar.close,
                     f"close {bar.close:.4f} back through {result.level:.4f} after {result.minutes_held} min")
            if setup == "A":
                self.transition(bar.Index, State.WAITING_FOR_A, f"{name} not confirmed")
            else:
                back = State.WAITING_FOR_C_DOWN if direction == DOWN else State.WAITING_FOR_C_UP
                self.transition(bar.Index, back, f"{name} not confirmed")
        return False

    def on_in_a_trade(self, bar, direction: str) -> bool:
        if direction == LONG:
            b_level = self.levels.b_for_long
            reached = bar.low <= b_level
        else:
            b_level = self.levels.b_for_short
            reached = bar.high >= b_level
        if not reached:
            return False
        self.log(bar.Index, "B_REACHED", b_level,
                 f"{'low' if direction == LONG else 'high'} crossed B ({'OR low' if direction == LONG else 'OR high'})")
        if self.config.exit_on_b:
            self.close_position(bar, b_level, "B_REACHED")
        if self.config.allow_c_reversal:
            next_state = State.WAITING_FOR_C_DOWN if direction == LONG else State.WAITING_FOR_C_UP
            self.transition(bar.Index, next_state, "A thesis failed")
            return True
        if self.position is None and self.config.allow_multiple_trades:
            self.transition(bar.Index, State.WAITING_FOR_A, "re-armed for a new A")
        else:
            self.transition(bar.Index, State.DONE, "A thesis failed")
        return False

    def on_waiting_for_c(self, bar, direction: str) -> bool:
        level = self.levels.c_up if direction == UP else self.levels.c_down
        if not detect_level_touch(bar.high, bar.low, level, direction):
            return False
        self.candidate_start = self.i
        price = bar.high if direction == UP else bar.low
        self.log(bar.Index, f"C_{direction}_TOUCHED", price, f"C-{direction.title()} {level:.4f}")
        self.transition(bar.Index, State.C_UP_CANDIDATE if direction == UP else State.C_DOWN_CANDIDATE)
        return True

    def end_of_day(self, last_bar) -> None:
        if self.candidate_start is not None:
            self.log(last_bar.Index, "CANDIDATE_EXPIRED", detail=f"{self.state.value} unresolved at the close")
            self.candidate_start = None
        if self.position is not None:
            if self.config.force_close_at_market_close:
                self.close_position(last_bar, last_bar.close, "END_OF_DAY")
            else:
                self.log(last_bar.Index, "POSITION_OPEN_AT_CLOSE", last_bar.close)
        if self.state != State.DONE:
            self.transition(last_bar.Index, State.DONE, "end of session")


def simulate_acd_day(
    ticker: str,
    target_date: DateLike,
    minute_data: pd.DataFrame,
    previous_high: float,
    previous_low: float,
    previous_close: float,
    config: ACDConfig,
    execution: Optional[ExecutionModel] = None,
) -> ACDDayResult:
    """Run the ACD state machine over one day of 1-minute bars.

    Raises DataValidationError / InsufficientDataError for unusable data; recoverable
    issues (e.g. missing minutes) are emitted as DataQualityWarning and returned in
    ``result.warnings``.
    """
    date = parse_date(target_date)
    execution = execution or LevelExecution(config.slippage)

    df = filter_regular_session(standardize_ohlcv(minute_data), config.market_open, config.market_close)
    data_warnings = validate_intraday_data(df, date, config.market_open, config.market_close)

    opening_range = calculate_opening_range(df, date, config.opening_range_minutes, config.market_open,
                                            config.max_missing_or_minutes)
    pivot_range = calculate_daily_pivot_range(previous_high, previous_low, previous_close)
    levels = calculate_acd_levels(opening_range, config.a_value, config.c_value, config.c_levels_fn)

    trading_bars = df[df.index >= opening_range.end]
    if trading_bars.empty:
        raise InsufficientDataError(f"No bars after the opening range ended at {opening_range.end:%H:%M}")

    machine = _ACDDayMachine(trading_bars, levels, config, execution)
    or_last_bar = df[df.index < opening_range.end].index[-1]
    for msg in data_warnings:
        machine.log(df.index[0], "DATA_WARNING", detail=msg)
    machine.log(or_last_bar, "OPENING_RANGE", detail=(
        f"{opening_range.start:%H:%M}-{opening_range.end:%H:%M} high {opening_range.high:.4f} "
        f"low {opening_range.low:.4f} ({opening_range.bar_count}/{opening_range.expected_bars} bars)"))
    machine.log(or_last_bar, "LEVELS", detail=(
        f"A-Up {levels.a_up:.4f} A-Down {levels.a_down:.4f} C-Up {levels.c_up:.4f} C-Down {levels.c_down:.4f} "
        f"pivot {pivot_range.pivot:.4f} [{pivot_range.low:.4f}, {pivot_range.high:.4f}]"))
    machine.run()

    trades = machine.trades
    first = trades[0] if trades else None
    total_pnl = sum(t.pnl for t in trades)
    return ACDDayResult(
        ticker=ticker, date=date,
        or_high=opening_range.high, or_low=opening_range.low, or_mid=opening_range.mid,
        pivot=pivot_range.pivot, pivot_low=pivot_range.low, pivot_high=pivot_range.high,
        a_up=levels.a_up, a_down=levels.a_down, c_up=levels.c_up, c_down=levels.c_down,
        trade_taken=bool(trades),
        direction=first.direction if first else None,
        entry_time=first.entry_time if first else None,
        entry_price=first.entry_price if first else None,
        exit_time=first.exit_time if first else None,
        exit_price=first.exit_price if first else None,
        exit_reason=first.exit_reason if first else None,
        pnl=total_pnl,
        return_pct=total_pnl / first.entry_price if first else 0.0,
        events=machine.events, trades=trades, warnings=data_warnings,
        final_state=machine.state.value, opening_range=opening_range, pivot_range=pivot_range, levels=levels,
    )
