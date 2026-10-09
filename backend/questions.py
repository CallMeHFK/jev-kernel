"""Typed bounded questions and calibrated verdicts (mu's jev wire format).

Three kinds, exactly as mu's judgment kernel:

* ``noul``   — yes/no; the answer carries ``probability`` = P(yes).
* ``choice`` — one of a few named options; the answer carries
  ``probabilities`` per option.
* ``score``  — a level on a criteria ladder; the answer carries ``score``
  and ``confidence``.

A verdict is *uncertain* when its probability falls inside the configured
margin (boolean: between ``thresholds.no`` and ``thresholds.yes``;
choice/score: below ``thresholds.minProbability``). Uncertain questions
cascade to the next judge tier; a question no judge resolves gets mu's
fallback verdict (boolean 0.5, score ladder midpoint) and the ledger
records why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import num

KINDS = ("noul", "choice", "score")
DEFAULT_CRITERIA = ["0", "5", "10"]


@dataclass
class Question:
    point: str
    kind: str
    instructions: str
    options: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown question kind: {self.kind}")
        if self.kind == "score" and not self.options:
            self.options = list(DEFAULT_CRITERIA)

    # -- wire -----------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        if self.kind == "noul":
            return {"type": "boolean", "instructions": self.instructions}
        if self.kind == "choice":
            return {
                "type": "choice",
                "instructions": self.instructions,
                "options": list(self.options),
            }
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.options),
        }


@dataclass
class Verdict:
    kind: str
    value: Any                 # bool | str | float
    probability: float         # confidence in `value`
    uncertain: bool
    judge: str = ""
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    fallback: str = ""         # why no judge answered (empty when one did)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "kind": self.kind,
            "value": self.value,
            "probability": round(self.probability, 4),
            "uncertain": self.uncertain,
            "judge": self.judge,
            "latencyMs": round(self.latency_ms, 1),
        }
        if self.fallback:
            out["fallback"] = self.fallback
        return out


def fallback_verdict(question: Question, reason: str) -> Verdict:
    """mu's no-verdict answers: boolean 0.5, score ladder midpoint."""
    if question.kind == "noul":
        return Verdict("noul", False, 0.5, True, fallback=reason)
    if question.kind == "choice":
        return Verdict("choice", None, 0.0, True, fallback=reason)
    mid = (len(question.options) - 1) / 2
    return Verdict("score", mid, 0.0, True, fallback=reason)


def parse_verdict(
    question: Question,
    raw: Any,
    thresholds: dict[str, Any],
    judge: str = "",
    latency_ms: float = 0.0,
) -> Verdict | None:
    """Validate one wire answer; None when malformed (cascade continues)."""
    if not isinstance(raw, dict):
        return None
    yes = num(thresholds.get("yes"), 0.85)
    no = num(thresholds.get("no"), 0.15)
    min_p = num(thresholds.get("minProbability"), 0.6)

    if question.kind == "noul":
        p = raw.get("probability", raw.get("p"))
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            return None
        p = max(0.0, min(1.0, float(p)))
        uncertain = no < p < yes
        value = p >= 0.5
        return Verdict(
            "noul", value, p if value else 1.0 - p, uncertain,
            judge=judge, latency_ms=latency_ms, raw=raw,
        )

    if question.kind == "choice":
        choice = raw.get("choice")
        if choice not in question.options:
            return None
        probs = raw.get("probabilities")
        p = probs.get(choice) if isinstance(probs, dict) else raw.get("probability")
        if p is None or isinstance(p, bool) or not isinstance(p, (int, float)):
            return None
        p = max(0.0, min(1.0, float(p)))
        return Verdict(
            "choice", choice, p, p < min_p,
            judge=judge, latency_ms=latency_ms, raw=raw,
        )

    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    conf = raw.get("confidence", raw.get("probability"))
    if conf is None or isinstance(conf, bool) or not isinstance(conf, (int, float)):
        conf = 0.0
    conf = max(0.0, min(1.0, float(conf)))
    return Verdict(
        "score", float(score), conf, conf < min_p,
        judge=judge, latency_ms=latency_ms, raw=raw,
    )
