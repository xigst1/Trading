"""Agent tools and loop, with an in-memory provider and a scripted fake Claude client."""

from types import SimpleNamespace

import pandas as pd
import pytest

from agent.agent import TradingAgent
from agent.tools import TradingTools
from common.market_data import DataUnavailableError, MarketDataProvider
from common.sessions import standardize_daily
from tests.synthetic import DATE, day


class FakeProvider(MarketDataProvider):
    name = "fake"

    def __init__(self):
        self.minute = day([(30, 100.0), (40, 102.0), (60, 102.5), (389, 103.0)])
        idx = pd.bdate_range(end="2026-09-18", periods=40)
        rows = [(100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100.5 + i * 0.1) for i in range(len(idx))]
        self.daily = standardize_daily(pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"]))

    def get_minute_data(self, ticker, date):
        if str(date)[:10] != DATE:
            raise DataUnavailableError("no minute data")
        return self.minute

    def get_daily_data(self, ticker, start, end):
        return self.daily[(self.daily.index >= pd.Timestamp(start)) & (self.daily.index <= pd.Timestamp(end))]


def test_simulate_tool_returns_numbers_chart_and_tables():
    out = TradingTools(FakeProvider()).run("simulate_acd_day", {
        "ticker": "spy", "date": DATE, "confirmation_minutes": 15, "a_value": 0.5, "c_value": 0.75})
    assert not out.is_error
    assert out.content["trade_taken"] and out.content["trades"][0]["direction"] == "LONG"
    assert out.content["total_pnl_per_share"] == pytest.approx(1.5)
    assert len(out.figures) == 1 and [t for t, _ in out.tables] == ["Trades", "Event log"]
    out.to_model_text()  # JSON serializable


def test_simulate_tool_with_atr_multiple():
    out = TradingTools(FakeProvider()).run("simulate_acd_day", {
        "ticker": "SPY", "date": DATE, "confirmation_minutes": 15, "a_atr_multiple": 0.25, "c_atr_multiple": 0.25})
    assert out.content["parameters"]["a_value"] == pytest.approx(0.25 * out.content["parameters"]["atr_14"], abs=1e-3)


def test_levels_and_volatility_tools():
    tools = TradingTools(FakeProvider())
    levels = tools.run("get_acd_levels", {"ticker": "SPY", "date": DATE, "a_value": 0.5, "c_value": 0.5})
    assert levels.content["opening_range"]["high"] == 101.0
    assert levels.content["acd_levels"]["a_up"] == 101.5
    assert "pivot_range" in levels.content
    vol = tools.run("get_volatility", {"ticker": "SPY", "as_of_date": DATE})
    assert vol.content["atr_14"] > 0 and vol.figures


def test_tool_errors_are_reported_not_raised():
    tools = TradingTools(FakeProvider())
    assert tools.run("simulate_acd_day", {"ticker": "SPY", "date": "2026-09-17", "confirmation_minutes": 15,
                                          "a_value": 1, "c_value": 1}).is_error
    assert tools.run("no_such_tool", {}).is_error
    assert tools.run("get_price_history", {"ticker": "SPY"}).is_error  # missing args


def _block(**kw):
    return SimpleNamespace(**kw)


class FakeClient:
    """Returns scripted responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def test_agent_runs_tools_and_collects_figures():
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", content=[
            _block(type="text", text="Running it."),
            _block(type="tool_use", id="t1", name="simulate_acd_day",
                   input={"ticker": "SPY", "date": DATE, "confirmation_minutes": 15, "a_value": 0.5, "c_value": 0.75}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[_block(type="text", text="Long at 101.50, +1.50/share.")]),
    ])
    agent = TradingAgent(FakeProvider(), client=client)
    reply, history = agent.ask("simulate SPY")
    assert reply.text == "Long at 101.50, +1.50/share."
    assert len(reply.figures) == 1 and reply.tool_calls[0]["tool"] == "simulate_acd_day"
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and not tool_result["is_error"]
    assert client.requests[0]["fallbacks"] == "default"
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_agent_refusal_keeps_previous_history():
    client = FakeClient([SimpleNamespace(stop_reason="refusal", content=[])])
    prior = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    reply, history = TradingAgent(FakeProvider(), client=client).ask("x", prior)
    assert "declined" in reply.text and history == prior
