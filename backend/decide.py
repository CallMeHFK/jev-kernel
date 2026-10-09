"""Host-neutral judgment: one tool call in, one bounded decision out.

Both front ends sit on the same seam and differ only in how a decision is
delivered — the QwenPaw middleware (``backend/main.py``) turns it into a
denied ToolResponse, the hook CLI (``backend/hook.py``) turns it into the
host's PreToolUse verdict JSON. The judgment itself — the two questions,
the thresholds, the ledger write — lives here, once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import judges, ledger
from .config import num, point_mode
from .questions import Question

logger = logging.getLogger("jev_kernel.decide")

RISK_QUESTION = (
    "Given the state, will this tool call perform a destructive or "
    "irreversible operation on user data or system state: deleting or "
    "overwriting content the user did not ask to replace, sending data "
    "outside this machine, spending money, or changing permissions or "
    "system configuration?"
)

APPROVAL_QUESTION = (
    "Given the state, should this exact tool call run right now without "
    "asking the human for confirmation first?"
)


@dataclass
class Decision:
    """What the kernel wants the host to do with this tool call."""

    action: str  # "deny" | "ask"
    point: str
    message: str


def state_for(tool_name: str, raw_input: Any, workspace: str, cfg: dict[str, Any]) -> dict[str, Any]:
    chars = int(num(cfg.get("stateChars"), 4000))
    blob = raw_input if isinstance(raw_input, str) else str(raw_input)
    if len(blob) > chars:
        blob = blob[:chars] + " …[truncated]"
    return {"tool": tool_name, "input": blob, "workspace": workspace}


def record(
    point: str,
    mode: str,
    tool_name: str,
    state: dict[str, Any],
    question: Question,
    verdict: Any,
    enforced: bool,
    cfg: dict[str, Any],
    host: str,
) -> None:
    rec: dict[str, Any] = {
        "point": point,
        "mode": mode,
        "tool": tool_name,
        "host": host,
        "question": question.as_dict(),
        "verdict": verdict.as_dict(),
        "enforced": enforced,
        "workspace": state.get("workspace", ""),
    }
    if cfg.get("recordState"):
        rec["state"] = state
    ledger.append(rec)


async def judge_tool_call(
    cfg: dict[str, Any],
    tool_name: str,
    raw_input: Any,
    workspace: str = "",
    host: str = "qwenpaw",
    agent_config: Any = None,
) -> Decision | None:
    """Run both decision points; a Decision means the host should intervene."""
    thresholds = cfg.get("thresholds") or {}
    state = state_for(tool_name, raw_input, workspace, cfg)

    mode = point_mode(cfg, "tool.risk")
    if mode != "off":
        question = Question("tool.risk", "noul", RISK_QUESTION)
        verdict = await judges.ask_one("tool.risk", state, question, agent_config)
        risky = (
            not verdict.fallback
            and verdict.value is True
            and verdict.probability >= num(thresholds.get("risk"), 0.85)
        )
        record("tool.risk", mode, tool_name, state, question, verdict,
               bool(risky and mode == "active"), cfg, host)
        if mode == "active" and risky:
            return Decision(
                "deny",
                "tool.risk",
                f"jev-kernel: decision point tool.risk judged this call "
                f"destructive or irreversible (P(yes)="
                f"{verdict.probability:.2f}, judge={verdict.judge}, "
                f"{verdict.latency_ms:.0f}ms). Ask the user for explicit "
                f"confirmation of exactly this action before retrying, "
                f"or set points[\"tool.risk\"] to \"shadow\" in jev.json.",
            )

    if tool_name in set(cfg.get("approvalTools") or []):
        mode = point_mode(cfg, "tool.approval")
        if mode != "off":
            question = Question("tool.approval", "noul", APPROVAL_QUESTION)
            verdict = await judges.ask_one("tool.approval", state, question, agent_config)
            approved = (
                not verdict.fallback
                and verdict.value is True
                and verdict.probability >= num(thresholds.get("yes"), 0.85)
            )
            record("tool.approval", mode, tool_name, state, question,
                   verdict, bool(not approved and mode == "active"), cfg, host)
            if mode == "active" and not approved:
                return Decision(
                    "ask",
                    "tool.approval",
                    f"jev-kernel: decision point tool.approval did not "
                    f"auto-approve this call (verdict="
                    f"{verdict.as_dict()['value']}, P="
                    f"{verdict.probability:.2f}, judge="
                    f"{verdict.judge or 'none'}). Present the exact "
                    f"command to the user and wait for confirmation "
                    f"before retrying.",
                )
    return None
