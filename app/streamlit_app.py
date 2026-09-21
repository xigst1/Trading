"""Streamlit UI: price history, ACD day simulator and the trading agent.

Run from the repo root:  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from acd.charts import plot_acd_day  # noqa: E402
from acd.simulation.runner import compute_day_levels, run_acd_day  # noqa: E402
from common.charts import candlestick_figure, line_figure  # noqa: E402
from common.indicators import atr, true_range  # noqa: E402
from common.market_data import get_provider  # noqa: E402
from common.sessions import filter_regular_session  # noqa: E402

st.set_page_config(page_title="Trading Lab", layout="wide")

SOURCES = {
    "Yahoo + local cache": "yahoo-cached",
    "Yahoo only": "yahoo",
    "Local CSV only": "csv",
}


def last_weekday(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


@st.cache_resource
def provider_for(source: str):
    return get_provider(source)


@st.cache_data(ttl=600, show_spinner=False)
def load_daily(source: str, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    return provider_for(source).get_daily_data(ticker, start, end)


@st.cache_data(ttl=600, show_spinner=False)
def load_minutes(source: str, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    frames = []
    for day in pd.bdate_range(start, end):
        try:
            frames.append(filter_regular_session(provider_for(source).get_minute_data(ticker, day.date())))
        except Exception:
            continue
    return pd.concat(frames) if frames else pd.DataFrame()


# --- Sidebar -------------------------------------------------------------------------
st.sidebar.title("Trading Lab")
page = st.sidebar.radio("View", ["Price history", "ACD day", "Agent", "Turtle Trader"])
source_label = st.sidebar.selectbox("Data source", list(SOURCES))
source = SOURCES[source_label]
st.sidebar.caption("Yahoo serves 1-minute bars for about the last 30 days only. "
                   "Run scripts/download_data.py regularly to archive them locally.")


# --- Pages -----------------------------------------------------------------------------
def page_price_history():
    st.header("Price history")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    ticker = c1.text_input("Ticker", "SPY").strip().upper()
    interval = c2.selectbox("Interval", ["1d", "1m"])
    today = dt.date.today()
    default_start = today - dt.timedelta(days=365 if interval == "1d" else 2)
    start = c3.date_input("Start", default_start)
    end = c4.date_input("End", today)
    if not ticker:
        return
    try:
        with st.spinner("Loading data..."):
            if interval == "1d":
                df = load_daily(source, ticker, start, end)
            else:
                if len(pd.bdate_range(start, end)) > 5:
                    st.warning("Limited to 5 trading days for 1-minute bars.")
                    return
                df = load_minutes(source, ticker, start, end)
    except Exception as exc:
        st.error(f"Could not load data: {exc}")
        return
    if df.empty:
        st.warning("No data for that range.")
        return

    last, first = df.iloc[-1], df.iloc[0]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Last close", f"{last['close']:,.2f}",
              f"{100 * (last['close'] / first['open'] - 1):+.2f}% over range")
    m2.metric("Range high", f"{df['high'].max():,.2f}")
    m3.metric("Range low", f"{df['low'].min():,.2f}")
    m4.metric("Bars", f"{len(df):,}")
    st.plotly_chart(candlestick_figure(df, f"{ticker} ({interval})"), use_container_width=True)

    if interval == "1d" and len(df) > 20:
        period = st.slider("ATR period", 5, 50, 14)
        tr, atr_series = true_range(df), atr(df, period)
        st.plotly_chart(line_figure({f"ATR({period})": atr_series, "True range": tr},
                                    f"{ticker} true range and ATR", "Price units"), use_container_width=True)
        table = df.assign(tr=tr, atr=atr_series)
    else:
        table = df
    with st.expander("Data table"):
        st.dataframe(table.sort_index(ascending=False), use_container_width=True)


def page_acd_day():
    st.header("ACD one-day simulator")
    st.caption("Mark Fisher's ACD (The Logical Trader). V1 rules: confirmation, B and C are configurable "
               "placeholders pending verification against the book.")
    with st.form("acd"):
        c1, c2, c3, c4 = st.columns(4)
        ticker = c1.text_input("Ticker", "SPY").strip().upper()
        date = c2.date_input("Date", last_weekday(dt.date.today() - dt.timedelta(days=1)))
        or_minutes = c3.number_input("Opening range (min)", 5, 120, 30, step=5)
        confirm = c4.number_input("Confirmation (min)", 0, 120, 15)

        mode = st.radio("A / C values", ["Multiple of ATR(14)", "Fixed price units"], horizontal=True)
        c5, c6, c7, c8 = st.columns(4)
        if mode.startswith("Multiple"):
            a_mult = c5.number_input("A x ATR", 0.0, 2.0, 0.10, step=0.01, format="%.3f")
            c_mult = c6.number_input("C x ATR", 0.0, 2.0, 0.15, step=0.01, format="%.3f")
            a_val = c_val = None
        else:
            a_val = c5.number_input("A value", 0.0, 1000.0, 0.50, step=0.05)
            c_val = c6.number_input("C value", 0.0, 1000.0, 0.50, step=0.05)
            a_mult = c_mult = None
        exit_on_b = c7.checkbox("Exit A trade at B", True)
        allow_c = c8.checkbox("Allow C reversal", True)
        submitted = st.form_submit_button("Run simulation", type="primary")
    if not submitted:
        return

    provider = provider_for(source)
    try:
        with st.spinner("Loading data and simulating..."):
            result, inputs, config = run_acd_day(
                provider, ticker, date, int(confirm), a_value=a_val, c_value=c_val, a_atr_multiple=a_mult,
                c_atr_multiple=c_mult, opening_range_minutes=int(or_minutes), exit_on_b=exit_on_b,
                allow_c_reversal=allow_c,
            )
            levels = compute_day_levels(provider, ticker, date, int(or_minutes))
    except Exception as exc:
        st.error(f"{type(exc).__name__}: {exc}")
        return

    for w in result.warnings:
        st.warning(w)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Trade", result.direction or "No trade")
    m2.metric("Entry", "-" if result.entry_price is None else f"{result.entry_price:,.2f}",
              None if result.entry_time is None else result.entry_time.strftime("%H:%M"), delta_color="off")
    m3.metric("Exit", "-" if result.exit_price is None else f"{result.exit_price:,.2f}",
              result.exit_reason, delta_color="off")
    m4.metric("Day P&L / share", f"{result.pnl:+,.2f}", f"{100 * result.return_pct:+.2f}%")
    m5.metric("A / C used", f"{config.a_value:.2f} / {config.c_value:.2f}",
              None if inputs.atr is None else f"ATR14 {inputs.atr:.2f}", delta_color="off")

    st.plotly_chart(plot_acd_day(inputs.minute_data, result), use_container_width=True)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Event log")
        st.dataframe(result.events_frame(), use_container_width=True, hide_index=True, height=420)
    with right:
        st.subheader("Levels")
        st.dataframe(pd.DataFrame(result.levels_dict().items(), columns=["level", "price"]).round(4),
                     use_container_width=True, hide_index=True)
        summary = levels.summary()
        st.caption(f"Previous day {summary['previous_day']['date']}: H {summary['previous_day']['high']:.2f} "
                   f"L {summary['previous_day']['low']:.2f} C {summary['previous_day']['close']:.2f}")
        context = {k: summary[k] for k in ("open_vs_pivot_range", "pivot_vs_previous_pivot") if k in summary}
        if context:
            st.json(context)
        if result.trades:
            st.subheader("Trades")
            st.dataframe(result.trades_frame(), use_container_width=True, hide_index=True)


def page_agent():
    st.header("Trading agent")
    st.caption("Ask for prices, ATR, ACD levels or a one-day ACD simulation. Runs Claude with local tools; "
               "needs the `anthropic` package and an API key (ANTHROPIC_API_KEY) or `ant auth login`.")
    try:
        from agent.agent import TradingAgent
        import anthropic
    except ImportError as exc:
        st.error(f"Agent unavailable: {exc}. Install it with `pip install anthropic`.")
        return

    if "chat" not in st.session_state:
        st.session_state.chat = []  # what the UI shows
        st.session_state.agent_history = []  # what the API sees
    if st.sidebar.button("Clear conversation"):
        st.session_state.chat, st.session_state.agent_history = [], []

    for i, msg in enumerate(st.session_state.chat):
        with st.chat_message(msg["role"]):
            _render_message(msg, i)

    question = st.chat_input("e.g. Simulate ACD for SPY yesterday with A=0.1xATR, C=0.15xATR, 15 min confirmation")
    if not question:
        return
    st.session_state.chat.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                agent = TradingAgent(provider_for(source))
                reply, history = agent.ask(question, st.session_state.agent_history)
        except anthropic.AuthenticationError:
            st.error("Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.")
            return
        except anthropic.RateLimitError:
            st.error("Rate limited by the API. Wait a moment and try again.")
            return
        except anthropic.APIConnectionError:
            st.error("Could not reach the Anthropic API. Check your network connection.")
            return
        except anthropic.APIStatusError as exc:
            st.error(f"API error {exc.status_code}: {exc.message}")
            return
        st.session_state.agent_history = history
        msg = {"role": "assistant", "text": reply.text, "figures": reply.figures, "tables": reply.tables,
               "tool_calls": reply.tool_calls}
        st.session_state.chat.append(msg)
        _render_message(msg, len(st.session_state.chat) - 1)


def _render_message(msg, idx):
    st.markdown(msg["text"])
    for j, fig in enumerate(msg.get("figures", [])):
        st.plotly_chart(fig, use_container_width=True, key=f"fig-{idx}-{j}")
    for title, table in msg.get("tables", []):
        with st.expander(title):
            st.dataframe(table, use_container_width=True)
    if msg.get("tool_calls"):
        with st.expander("Tool calls"):
            st.json(msg["tool_calls"])


def page_turtle():
    st.header("Turtle Trader")
    st.info("Placeholder. Strategy code will live in turtle_trader/. Shared helpers already available: "
            "common.indicators.atr (N) and common.indicators.donchian_channel (breakouts).")


{"Price history": page_price_history, "ACD day": page_acd_day, "Agent": page_agent,
 "Turtle Trader": page_turtle}[page]()
