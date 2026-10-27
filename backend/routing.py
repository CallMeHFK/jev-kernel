"""Agent routing: one bounded choice question in, one agent id out.

The router classifies only. It never calls QwenPaw and never picks a model:
the caller (the Orchestrator) performs the dispatch with its own
``chat_with_agent`` / ``submit_to_agent`` tools, so the target agent keeps its
own ``active_model`` and fallback chain.

Three gates decide whether a choice is actionable:

* a judge must have answered (no fallback) and must not itself pick
  ``ask_human``;
* the chosen option's probability must clear ``agentRoute.threshold``;
* the lead over the runner-up must clear ``agentRoute.margin``.

Anything else returns ``action="ask_human"`` with a reason instead of a
guess. ``confidence`` and ``margin`` are ledger material for the shadow
review; they never become dispatch parameters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import num

# The cascade discards a low-confidence answer and returns a fallback verdict
# whose ``fallback`` string still carries what the judge said, e.g.
# ``errors:lux:uncertain:default@0.43``. Recover that pick for the ledger so a
# shadow review can tell "judge unsure" apart from "judge dead".
_UNCERTAIN = re.compile(r"uncertain:([^@;|]+)@([0-9]*\.?[0-9]+)")


def _uncertain_pick(fallback: str) -> tuple[str, float] | None:
    matches = _UNCERTAIN.findall(fallback or "")
    if not matches:
        return None
    name, raw = matches[-1]
    try:
        return name, max(0.0, min(1.0, float(raw)))
    except ValueError:
        return None


ROUTE_INSTRUCTIONS = (
    "Route the task to exactly one agent. Pick the single agent that owns "
    "the work. Pick 'default' only when the orchestrator should answer "
    "directly without delegating. Pick 'ask_human' when no agent clearly "
    "owns the task, when it spans several domains, or when the request is "
    "ambiguous."
)


@dataclass
class RouteDecision:
    action: str              # "dispatch" | "ask_human"
    agent_id: str | None
    confidence: float
    margin: float | None
    reason: str
    expected: str | None = None
    detail: str | None = None   # e.g. the judge's discarded pick "default@0.43"

    @property
    def agree(self) -> bool | None:
        """Shadow agreement; None when either side made no recommendation."""
        if self.expected is None or self.agent_id is None:
            return None
        return self.expected == self.agent_id

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.action,
            "agent_id": self.agent_id,
            "reason": self.reason,
        }
        if self.detail:
            out["detail"] = self.detail
        if self.expected is not None:
            out["expected"] = self.expected
            out["agree"] = self.agree
        return out


def margin_of(verdict: Any, chosen: Any) -> float | None:
    """Chosen probability minus the runner-up, when the judge sent a map."""
    raw = getattr(verdict, "raw", None)
    probs = raw.get("probabilities") if isinstance(raw, dict) else None
    if not isinstance(probs, dict) or not isinstance(chosen, str):
        return None
    others = [
        float(value)
        for option, value in probs.items()
        if option != chosen
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ]
    if not others:
        return None
    return float(getattr(verdict, "probability", 0.0) or 0.0) - max(others)


def decide_route(
    verdict: Any,
    cfg: dict[str, Any],
    expected: str | None = None,
) -> RouteDecision:
    """Turn one choice verdict into a dispatch or an ask-human decision."""
    route_cfg = cfg.get("agentRoute") or {}
    threshold = num(route_cfg.get("threshold"), 0.6)
    ask_min = num(route_cfg.get("askMin"), 0.45)
    min_margin = num(route_cfg.get("margin"), 0.1)

    value = getattr(verdict, "value", None)
    probability = float(getattr(verdict, "probability", 0.0) or 0.0)
    margin = margin_of(verdict, value)

    def ask(reason: str, detail: str | None = None) -> RouteDecision:
        return RouteDecision(
            "ask_human", None, probability, margin, reason, expected, detail
        )

    fallback = str(getattr(verdict, "fallback", "") or "")
    if fallback or value is None:
        pick = _uncertain_pick(fallback)
        if pick is not None:
            name, p = pick
            return RouteDecision(
                "ask_human", None, p, margin, "uncertain", expected,
                f"{name}@{p:.2f}",
            )
        return ask("no_verdict")
    if value == "ask_human":
        return ask("judge_abstained")
    if getattr(verdict, "uncertain", True):
        return ask("uncertain")
    if probability < ask_min or probability < threshold:
        return ask("low_confidence")
    if margin is not None and margin < min_margin:
        return ask("close_call")
    return RouteDecision("dispatch", str(value), probability, margin, "ok", expected)
