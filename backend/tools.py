"""Agent-facing tools: ``jev_ask`` and ``jev_ledger``.

``jev_ask`` exposes mu's bounded-question interface to the agent itself:
any decision point in a skill or workflow can ask the kernel one typed
question and get a calibrated verdict back. ``jev_ledger`` is mu's
``mu ledger``: read the last verdicts with their probabilities, judges and
timings before handing a decision point to a judge.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    from agentscope.message import TextBlock
    from agentscope.tool import ToolResponse
except ImportError:  # allow unit tests without AgentScope installed
    from dataclasses import dataclass, field

    @dataclass
    class TextBlock:  # type: ignore[no-redef]
        type: str = "text"
        text: str = ""

    @dataclass
    class ToolResponse:  # type: ignore[no-redef]
        content: list = field(default_factory=list)
        state: object = "success"

try:
    from qwenpaw.runtime.tool_registry import tool_descriptor
except ImportError:  # allow unit tests without QwenPaw installed

    def tool_descriptor(**kwargs: Any):  # type: ignore[no-redef]
        def deco(fn: Any) -> Any:
            fn._tool_descriptor = kwargs
            return fn

        return deco

from . import judges, ledger
from .config import load_config
from .questions import Question

ASK_DESC = (
    "Ask the jev judgment kernel one bounded typed question and get a "
    "calibrated verdict. kind: 'noul' (yes/no, verdict.value is a bool, "
    "verdict.probability is confidence in it), 'choice' (pass options; "
    "verdict.value is the chosen option), 'score' (pass criteria levels or "
    "leave default; verdict.value is the level index). The question cascades "
    "through the configured judge tiers; an uncertain or unanswered question "
    "comes back with verdict.uncertain=true and a fallback reason instead of "
    "a fabricated answer. Use for routing, risk and go/no-go decisions where "
    "a small fast judge is cheaper than the main model."
)

LEDGER_DESC = (
    "Read the jev judgment kernel ledger: the last `limit` verdict records "
    "(decision point, mode, question, verdict with probability, judge name, "
    "latency, fallback reason). mu's `mu ledger`. Read this before switching "
    "a decision point from shadow to active."
)


@tool_descriptor(name="jev_ask", description=ASK_DESC)
async def jev_ask(
    kind: str,
    question: str,
    options: list[str] | None = None,
    point: str = "adhoc",
) -> ToolResponse:
    """Ask the judgment kernel one bounded typed question."""
    cfg = load_config()
    q = Question(
        point=point or "adhoc",
        kind=kind,
        instructions=question,
        options=options or [],
    )
    state: dict[str, Any] = {"source": "jev_ask", "cwd": os.getcwd()}
    verdict = await judges.ask_one(q.point, state, q)
    record: dict[str, Any] = {
        "point": q.point,
        "mode": "tool",
        "question": q.as_dict(),
        "verdict": verdict.as_dict(),
    }
    if cfg.get("recordState"):
        record["state"] = state
    ledger.append(record)
    text = json.dumps(
        {"question": q.as_dict(), "verdict": verdict.as_dict()},
        ensure_ascii=False,
        indent=1,
    )
    return ToolResponse(content=[TextBlock(type="text", text=text)])


@tool_descriptor(name="jev_ledger", description=LEDGER_DESC)
async def jev_ledger(limit: int = 20) -> ToolResponse:
    """Read the last verdict records from the ledger."""
    records = ledger.tail(limit)
    text = json.dumps(records, ensure_ascii=False, indent=1)
    return ToolResponse(content=[TextBlock(type="text", text=text)])
