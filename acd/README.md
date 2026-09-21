# ACD (Mark Fisher, *The Logical Trader*)

## Layout

| File | Purpose |
|---|---|
| `levels.py` | Opening range, daily pivot range, A/C levels (pure functions) |
| `signals.py` | Level touches, confirmation rules, context signals such as open vs pivot range and pivot-range trend |
| `charts.py` | Annotated one-day chart |
| `simulation/config.py` | `ACDConfig` |
| `simulation/execution.py` | Fill model (V1: at the level) and P&L, isolated from the strategy |
| `simulation/simulator.py` | `simulate_acd_day`: explicit state machine plus event log |
| `simulation/runner.py` | Loads data from a provider, resolves ATR-based A/C, and computes levels for a date |

The simulator takes a minute DataFrame and the previous day's H/L/C. It never calls a data vendor itself.

## V1 rules (as implemented)

1. **Opening range:** high and low of the bars in `[09:30, 09:30 + OR minutes)`. Trading starts at the end of the OR.
2. **Pivot range (context only):** `pivot = (H+L+C)/3`, `bc = (H+L)/2`, `tc = 2*pivot - bc`.
3. **A levels:** `A_UP = OR_HIGH + A`, `A_DOWN = OR_LOW - A`. A is an explicit number. `runner.resolve_value` can derive it as a multiple of ATR(14), using only daily bars before the date.
4. **Touch:** a bar's high ≥ A-Up, or its low ≤ A-Down, starts a candidate.
5. **Confirmation** (`signals.confirm_level_hold`): every bar from the touch bar on must close beyond the level (or, with `confirmation_price="full_bar"`, trade entirely beyond it) until `confirmation_minutes` have elapsed. Elapsed time is wall-clock, so missing bars don't stretch the window. The first bar that fails ends the candidate.
6. **Entry** at the A level on the confirming bar.
7. **Point B:** OR low for a long, OR high for a short. When crossed, the A trade exits at B (`exit_on_b`), and the machine waits for the opposite C level.
8. **C levels:** `C_UP = OR_HIGH + C`, `C_DOWN = OR_LOW - C`. The formula can be replaced via `c_levels_fn`. C uses the same confirmation rule, and `c_confirmation_minutes` can override the time.
9. **Exit:** end of day at the last regular-session close, unless B exits first.
10. **P&L** per share: `(exit - entry)` for longs and `(entry - exit)` for shorts, minus `2 x commission`. Slippage is applied adversely to each fill.

Intrabar assumptions are listed at the top of `simulation/simulator.py`.

## Open questions to verify against the book

These rules are configurable placeholders. Don't treat them as Fisher's method until they have been checked against the book:

- Fisher's exact A confirmation: the time held beyond A, and the price used for it (close, or every trade).
- Whether the A trade should be stopped at B, at the OR midpoint, or elsewhere.
- The exact definitions of C, and when a C trade is valid (for example, only after a failed A).
- Profit-taking and exits beyond "hold to the close".
- How to calibrate A and C (fixed ticks, % of price, or fraction of ATR). `simulation_plan_comments.md` suggests `A = 0.10 x ATR14` with a 20-minute OR.

## Next milestone

Pick one SPY day, run `scripts/run_acd_day.py ... --html`, and walk through the event log bar by bar against the chart before building a multi-day backtest.
