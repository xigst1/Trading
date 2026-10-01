"""Agent tools and loop, with an in-memory provider and a scripted fake Claude client."""

from types import SimpleNamespace

import pandas as pd
import pytest

from agent.agent import DEFAULT_MODEL_KEY, MODELS, TradingAgent, resolve_model
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


def _usage(input_tokens=1000, output_tokens=100):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                           cache_read_input_tokens=0)


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
        SimpleNamespace(stop_reason="tool_use", usage=_usage(2000, 300), content=[
            _block(type="text", text="Running it."),
            _block(type="tool_use", id="t1", name="simulate_acd_day",
                   input={"ticker": "SPY", "date": DATE, "confirmation_minutes": 15, "a_value": 0.5, "c_value": 0.75}),
        ]),
        SimpleNamespace(stop_reason="end_turn", usage=_usage(3000, 200),
                        content=[_block(type="text", text="Long at 101.50, +1.50/share.")]),
    ])
    agent = TradingAgent(FakeProvider(), model="high", client=client)
    reply, history = agent.ask("simulate SPY")
    assert reply.text == "Long at 101.50, +1.50/share."
    assert len(reply.figures) == 1 and reply.tool_calls[0]["tool"] == "simulate_acd_day"
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and not tool_result["is_error"]
    assert client.requests[0]["fallbacks"] == "default"  # Opus tier only
    assert reply.usage.input_tokens == 5000 and reply.usage.output_tokens == 500
    assert reply.usage.cost_usd == pytest.approx(5000 * 5 / 1e6 + 500 * 25 / 1e6)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_agent_refusal_keeps_previous_history():
    client = FakeClient([SimpleNamespace(stop_reason="refusal", usage=_usage(), content=[])])
    prior = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    reply, history = TradingAgent(FakeProvider(), client=client).ask("x", prior)
    assert "declined" in reply.text and history == prior


def test_model_tiers_carry_their_own_request_settings():
    """Each family accepts different parameters; sending the wrong one is a 400."""
    assert [m.key for m in MODELS.values()] == ["high", "medium", "low"]
    assert MODELS["high"].input_per_mtok > MODELS["medium"].input_per_mtok > MODELS["low"].input_per_mtok

    def kwargs_for(key):
        return TradingAgent(FakeProvider(), model=key, client=FakeClient([]))._request_kwargs()

    high = kwargs_for("high")
    assert high["thinking"] == {"type": "adaptive"} and high["fallbacks"] == "default"
    medium = kwargs_for("medium")
    assert medium["thinking"] == {"type": "adaptive"} and "fallbacks" not in medium
    low = kwargs_for("low")
    assert low["thinking"]["type"] == "enabled" and low["thinking"]["budget_tokens"] < 16000
    assert "fallbacks" not in low  # refusal fallback is Opus-tier only


def test_resolve_model_accepts_tier_key_model_id_or_none():
    assert resolve_model(None).key == DEFAULT_MODEL_KEY
    assert resolve_model("low").model_id == "claude-haiku-4-5"
    assert resolve_model("claude-opus-5").key == "high"
    with pytest.raises(ValueError):
        resolve_model("gpt-9")


def test_cheaper_tier_costs_less_for_the_same_tokens():
    tokens = (20_000, 2_000)
    assert MODELS["low"].cost(*tokens) < MODELS["medium"].cost(*tokens) < MODELS["high"].cost(*tokens)
