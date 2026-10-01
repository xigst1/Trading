"""Claude-powered trading-research agent.

Runs locally: Claude decides which tools to call, the tools run on this machine
against the configured MarketDataProvider, and numbers/charts come back to the UI.

Credentials are resolved by the Anthropic SDK (ANTHROPIC_API_KEY, or an
`ant auth login` profile). Nothing is hard-coded here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from agent.tools import ToolOutput, TradingTools
from common.market_data import MarketDataProvider

MAX_TOOL_ROUNDS = 10


@dataclass(frozen=True)
class ModelChoice:
    """One selectable model, with the request settings it accepts.

    The families differ in what they take: adaptive thinking exists from the 4.6
    generation on, while Haiku 4.5 still needs an explicit thinking budget, and the
    server-side refusal fallback (which silently re-runs a declined request on another
    model) is an Opus-tier feature. Sending the wrong combination is a 400, so each
    model carries its own settings rather than the caller guessing.
    """

    key: str  # "high" | "medium" | "low"
    label: str
    model_id: str
    input_per_mtok: float
    output_per_mtok: float
    thinking: Dict[str, Any]
    use_refusal_fallback: bool = False
    note: str = ""

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1_000_000


# Re-runs a declined request on Anthropic's recommended fallback model, server-side.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MODELS: Dict[str, ModelChoice] = {
    "high": ModelChoice(
        key="high", label="High cost — Claude Opus 5", model_id="claude-opus-5",
        input_per_mtok=5.0, output_per_mtok=25.0, thinking={"type": "adaptive"},
        use_refusal_fallback=True,
        note="Most capable. Best for open-ended questions and multi-step reasoning.",
    ),
    "medium": ModelChoice(
        key="medium", label="Medium cost — Claude Sonnet 5", model_id="claude-sonnet-5",
        input_per_mtok=2.0, output_per_mtok=10.0, thinking={"type": "adaptive"},
        note="About 60% cheaper than Opus and strong at tool-driven work. A good default.",
    ),
    "low": ModelChoice(
        key="low", label="Low cost — Claude Haiku 4.5", model_id="claude-haiku-4-5",
        input_per_mtok=1.0, output_per_mtok=5.0,
        thinking={"type": "enabled", "budget_tokens": 4000},
        note="Cheapest and fastest. Fine for lookups; weaker on open-ended analysis.",
    ),
}
DEFAULT_MODEL_KEY = "medium"
DEFAULT_MODEL = MODELS[DEFAULT_MODEL_KEY].model_id


def resolve_model(model: Optional[str]) -> ModelChoice:
    """Accept a tier key ("high"), a model id ("claude-opus-5") or None."""
    if model is None:
        return MODELS[DEFAULT_MODEL_KEY]
    if model in MODELS:
        return MODELS[model]
    for choice in MODELS.values():
        if choice.model_id == model:
            return choice
    raise ValueError(f"Unknown model {model!r}; choose one of {list(MODELS)} or a known model id")


SYSTEM_PROMPT = """You are a trading-research assistant running on the user's own machine.
You answer questions about stock prices, volatility and trading strategies by calling tools
that load market data and run the user's strategy code. Currently implemented: price history,
true range / ATR, and Mark Fisher's ACD method (levels and a one-day simulator). The Turtle
Trader strategy is a placeholder and not implemented yet.

Rules:
- Every number you report must come from a tool result. If a tool fails or data is missing,
  say so plainly; never estimate prices.
- Charts and tables returned by tools are shown to the user automatically below your reply,
  so refer to them rather than re-listing every value.
- Dates are US/Eastern trading dates. Resolve relative dates ("yesterday", "last Friday")
  against today's date given below, and skip weekends.
- ACD parameters: if the user does not give A, C or the confirmation time, ask for them or
  state the values you chose explicitly (e.g. A = 0.1 x ATR14, C = 0.15 x ATR14,
  confirmation = half the opening range) so the user can change them. The defaults are
  placeholders, so an answer that does not name them cannot be checked.
- Keep answers short: lead with the result, then the key numbers."""


@dataclass
class Usage:
    """Token counts and estimated spend for one question (summed over tool rounds)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_label: str = ""

    def summary(self) -> str:
        return (f"{self.model_label} · {self.input_tokens:,} in / {self.output_tokens:,} out "
                f"· about ${self.cost_usd:.3f}")


@dataclass
class AgentReply:
    text: str
    figures: List[Any] = field(default_factory=list)
    tables: List[Tuple[str, pd.DataFrame]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class TradingAgent:
    def __init__(self, provider: MarketDataProvider, model: Optional[str] = None, client=None,
                 max_tool_rounds: int = MAX_TOOL_ROUNDS):
        if client is None:
            import anthropic  # imported lazily so the app loads without the SDK installed

            client = anthropic.Anthropic()
        self.client = client
        self.choice = resolve_model(model)
        self.model = self.choice.model_id
        self.tools = TradingTools(provider)
        self.max_tool_rounds = max_tool_rounds

    def _system(self) -> str:
        return f"{SYSTEM_PROMPT}\n\nToday's date: {dt.date.today().isoformat()}"

    def _request_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"thinking": self.choice.thinking}
        if self.choice.use_refusal_fallback:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _add_usage(self, usage: Usage, response) -> None:
        reported = getattr(response, "usage", None)
        if reported is None:
            return
        usage.input_tokens += (getattr(reported, "input_tokens", 0) or 0)
        # Cached reads are billed differently, but counting them keeps the estimate honest.
        usage.input_tokens += (getattr(reported, "cache_read_input_tokens", 0) or 0)
        usage.output_tokens += (getattr(reported, "output_tokens", 0) or 0)
        usage.cost_usd = self.choice.cost(usage.input_tokens, usage.output_tokens)

    def ask(self, question: str, history: Optional[List[Dict[str, Any]]] = None) -> Tuple[AgentReply, List[Dict[str, Any]]]:
        """Answer one user turn. Returns the reply and the updated message history.

        ``history`` holds prior API messages (user/assistant turns, including tool
        calls) so follow-up questions keep their context.
        """
        base = list(history or [])
        messages = base + [{"role": "user", "content": question}]
        reply = AgentReply(text="", usage=Usage(model_label=self.choice.label))

        for _ in range(self.max_tool_rounds):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self._system(),
                tools=self.tools.definitions,
                messages=messages,
                **self._request_kwargs(),
            )
            self._add_usage(reply.usage, response)

            if response.stop_reason == "refusal":
                # Don't keep a refused turn in the history; the next question starts clean.
                reply.text = "The model declined this request. Try rephrasing it."
                return reply, base

            messages.append({"role": "assistant", "content": response.content})
            text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()

            if response.stop_reason != "tool_use":
                reply.text = text
                if response.stop_reason == "max_tokens":
                    reply.text += "\n\n_(Response truncated: output limit reached.)_"
                return reply, messages

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output: ToolOutput = self.tools.run(block.name, dict(block.input or {}))
                reply.tool_calls.append({"tool": block.name, "input": dict(block.input or {}),
                                         "error": output.content.get("error") if output.is_error else None})
                reply.figures.extend(output.figures)
                reply.tables.extend(output.tables)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output.to_model_text(), "is_error": output.is_error})
            # All results for one assistant turn go back in a single user message.
            messages.append({"role": "user", "content": results})

        reply.text = "Stopped after too many tool calls without a final answer."
        return reply, base  # history would end on tool results; drop the unfinished turn
