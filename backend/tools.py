"""Agent-facing tools: ``jev_ask``, ``jev_route`` and ``jev_ledger``.

``jev_ask`` exposes mu's bounded-question interface to the agent itself:
any decision point in a skill or workflow can ask the kernel one typed
question and get a calibrated verdict back. ``jev_route`` narrows that to
agent routing: one task in, one agent id out, with the confidence and margin
kept internal so the caller cannot turn them into dispatch parameters.
``jev_ledger`` is mu's ``mu ledger``: read the last verdicts with their
probabilities, judges and timings before handing a decision point to a judge.
"""

from __future__ import annotations

import json
import logging
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

from . import judges, ledger, routing
from .config import load_config, num, point_mode
from .questions import Question

logger = logging.getLogger("jev_kernel.tools")

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

ROUTE_DESC = (
    "Route one task to exactly one configured agent id. Returns agent_id "
    "plus action: 'dispatch' (send the task to that agent) or 'ask_human' "
    "(no safe choice; ask the user). mode says whether to follow the "
    "recommendation: 'active' yes, 'shadow' no — in shadow the caller keeps "
    "its own dispatch table and only records the recommendation. Pass "
    "shadow_expected with the agent the dispatch table chose to record "
    "agreement. The kernel only classifies; the caller performs the dispatch "
    "with its own agent tools, so the target keeps its own model."
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


def _route_candidates(candidates: Any, cfg: dict[str, Any]) -> list[str]:
    """Explicit candidates win; otherwise the configured roster. Deduped.

    A model may hand a JSON array back as a string; split it instead of
    iterating characters into a nonsense roster.
    """
    route_cfg = cfg.get("agentRoute") or {}
    raw: Any = candidates if candidates else route_cfg.get("candidates") or []
    if isinstance(raw, str):
        raw = raw.replace("，", ",").split(",")
    out: list[str] = []
    for value in raw:
        name = str(value or "").strip().strip("[]'\"")
        if name and name != "ask_human" and name not in out:
            out.append(name)
    return out


def _route_state(task: str, candidates: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
    chars = int(num(cfg.get("stateChars"), 4000))
    text = task if len(task) <= chars else task[:chars] + " …[truncated]"
    return {"task": text, "candidates": list(candidates)}


def _route_instructions(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    """Question text: the roster with descriptions plus the task itself."""
    descs = (cfg.get("agentRoute") or {}).get("descriptions") or {}
    lines: list[str] = []
    for name in state["candidates"]:
        desc = str(descs.get(name) or "").strip()
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    lines.append("- ask_human: no safe choice; ask the user")
    return (
        f"{routing.ROUTE_INSTRUCTIONS}\n\nAgents:\n"
        + "\n".join(lines)
        + f"\n\nTask:\n{state['task']}"
    )


def _record_route(
    cfg: dict[str, Any],
    point: str,
    mode: str,
    candidates: list[str],
    question: Question | None,
    verdict: Any,
    decision: routing.RouteDecision,
    state: dict[str, Any] | None,
) -> None:
    record: dict[str, Any] = {
        "point": point,
        "mode": mode,
        "tool": "jev_route",
        "host": "qwenpaw",
        "candidates": list(candidates),
        "question": question.as_dict() if question is not None else None,
        "verdict": verdict.as_dict() if verdict is not None else None,
        "recommended": decision.agent_id,
        "action": decision.action,
        "confidence": round(decision.confidence, 4),
        "margin": round(decision.margin, 4) if decision.margin is not None else None,
        "reason": decision.reason,
        "detail": decision.detail,
        "shadow_expected": decision.expected,
        "agree": decision.agree,
        "enforced": bool(mode == "active" and decision.action == "dispatch"),
    }
    if cfg.get("recordState") and state is not None:
        record["state"] = state
    ledger.append(record)


def _route_response(decision: routing.RouteDecision, mode: str) -> ToolResponse:
    payload = decision.as_dict()
    payload["mode"] = mode
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    return ToolResponse(content=[TextBlock(type="text", text=text)])


@tool_descriptor(name="jev_route", description=ROUTE_DESC)
async def jev_route(
    task: str,
    candidates: list[str] | None = None,
    shadow_expected: str | None = None,
    point: str = "agent_route",
) -> ToolResponse:
    """Classify one task to one agent id; the caller dispatches."""
    cfg = load_config()
    point = (point or "agent_route").strip() or "agent_route"
    mode = point_mode(cfg, point)
    expected = (shadow_expected or "").strip() or None

    if mode == "off":
        decision = routing.RouteDecision(
            "ask_human", None, 0.0, None, "point_off", expected
        )
        return _route_response(decision, mode)

    names = _route_candidates(candidates, cfg)
    state = _route_state(task or "", names, cfg)

    if not (task or "").strip():
        decision = routing.RouteDecision(
            "ask_human", None, 0.0, None, "empty_task", expected
        )
        _record_route(cfg, point, mode, names, None, None, decision, state)
        return _route_response(decision, mode)
    if not names:
        decision = routing.RouteDecision(
            "ask_human", None, 0.0, None, "no_candidates", expected
        )
        _record_route(cfg, point, mode, names, None, None, decision, state)
        return _route_response(decision, mode)
    if len(names) == 1:
        decision = routing.RouteDecision(
            "dispatch", names[0], 1.0, None, "single_candidate", expected
        )
        _record_route(cfg, point, mode, names, None, None, decision, state)
        return _route_response(decision, mode)

    question = Question(
        point=point,
        kind="choice",
        instructions=_route_instructions(state, cfg),
        options=names + ["ask_human"],
    )
    verdict: Any = None
    try:
        verdict = await judges.ask_one(point, state, question)
        decision = routing.decide_route(verdict, cfg, expected)
    except Exception as e:  # noqa: BLE001 - a dead router must ask, not guess
        logger.exception("jev-kernel: jev_route failed; asking the human")
        decision = routing.RouteDecision(
            "ask_human", None, 0.0, None, f"kernel_error:{type(e).__name__}", expected
        )
    _record_route(cfg, point, mode, names, question, verdict, decision, state)
    return _route_response(decision, mode)


@tool_descriptor(name="jev_ledger", description=LEDGER_DESC)
async def jev_ledger(limit: int | None = None) -> ToolResponse:
    """Read the last verdict records from the ledger."""
    cfg = load_config()
    eff = int(num(cfg.get("ledgerTail"), 20)) if limit is None else int(limit)
    records = ledger.tail(eff)
    text = json.dumps(records, ensure_ascii=False, indent=1)
    return ToolResponse(content=[TextBlock(type="text", text=text)])
