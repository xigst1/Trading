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

DEFAULT_MODEL = "claude-opus-5"
# Re-runs a declined request on Anthropic's recommended fallback model server-side.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 10

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
  confirmation = half the opening range) so the user can change them.
- Keep answers short: lead with the result, then the key numbers."""


@dataclass
class AgentReply:
    text: str
    figures: List[Any] = field(default_factory=list)
    tables: List[Tuple[str, pd.DataFrame]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


class TradingAgent:
    def __init__(self, provider: MarketDataProvider, model: str = DEFAULT_MODEL, client=None,
                 max_tool_rounds: int = MAX_TOOL_ROUNDS):
        if client is None:
            import anthropic  # imported lazily so the app loads without the SDK installed

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.tools = TradingTools(provider)
        self.max_tool_rounds = max_tool_rounds

    def _system(self) -> str:
        return f"{SYSTEM_PROMPT}\n\nToday's date: {dt.date.today().isoformat()}"

    def ask(self, question: str, history: Optional[List[Dict[str, Any]]] = None) -> Tuple[AgentReply, List[Dict[str, Any]]]:
        """Answer one user turn. Returns the reply and the updated message history.

        ``history`` holds prior API messages (user/assistant turns, including tool
        calls) so follow-up questions keep their context.
        """
        base = list(history or [])
        messages = base + [{"role": "user", "content": question}]
        reply = AgentReply(text="")

        for _ in range(self.max_tool_rounds):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self._system(),
                tools=self.tools.definitions,
                thinking={"type": "adaptive"},
                messages=messages,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
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
