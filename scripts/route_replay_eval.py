#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay a labeled task set through the live ``jev_route`` decision path.

Live shadow only scores whatever the Orchestrator happens to dispatch, so a
fresh install can sit at one or two records for days. This script closes that
gap without touching the live ledger: it feeds a fixed set of realistic tasks
through the *same* code path (``_route_instructions`` -> judge -> ``decide_route``)
and compares the recommendation with the dispatch table's owner.

    python3 scripts/route_replay_eval.py
    python3 scripts/route_replay_eval.py --cases scripts/route_cases.json --json
    python3 scripts/route_replay_eval.py --min-agreement 0.9 --min-scored 20

This is a replay against a labeled set, not live shadow. It answers "would the
router agree with the table on known work?"; the live ledger still answers
"does it agree on real traffic?". Cases with ``"expected": null`` are reported
but excluded from agreement — they are either deliberate abstain checks or
known roster gaps (e.g. a disabled owner).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend import judges, routing  # noqa: E402
from backend.config import load_config  # noqa: E402
from backend.questions import Question  # noqa: E402
from backend.tools import (  # noqa: E402
    _route_candidates,
    _route_instructions,
    _route_state,
)

DEFAULT_CASES = REPO / "scripts" / "route_cases.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a JSON array of cases")
    cases: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get("task") or "").strip():
            raise ValueError(f"{path}: case {index} has no task")
        expected = item.get("expected")
        if expected is not None and not str(expected).strip():
            raise ValueError(f"{path}: case {index} has a blank expected")
        cases.append(
            {
                "task": str(item["task"]).strip(),
                "expected": str(expected).strip() if expected is not None else None,
                "note": str(item.get("note") or ""),
            }
        )
    return cases


def bucket(confidence: float) -> str:
    if confidence >= 0.9:
        return ">=0.90"
    if confidence >= 0.75:
        return "0.75-0.90"
    if confidence >= 0.6:
        return "0.60-0.75"
    return "<0.60"


async def run_case(
    case: dict[str, Any],
    cfg: dict[str, Any],
    point: str,
    names: list[str],
) -> dict[str, Any]:
    state = _route_state(case["task"], names, cfg)
    question = Question(
        point=point,
        kind="choice",
        instructions=_route_instructions(state, cfg),
        options=names + ["ask_human"],
    )
    started = time.monotonic()
    verdict: Any = None
    try:
        verdict = await judges.ask_one(point, state, question)
        decision = routing.decide_route(verdict, cfg, case["expected"])
    except Exception as e:  # noqa: BLE001 - mirror the tool's ask-don't-guess rule
        decision = routing.RouteDecision(
            "ask_human", None, 0.0, None, f"kernel_error:{type(e).__name__}", case["expected"]
        )
    elapsed = time.monotonic() - started
    return {
        "task": case["task"],
        "note": case["note"],
        "expected": case["expected"],
        "recommended": decision.agent_id,
        "action": decision.action,
        "reason": decision.reason,
        "detail": decision.detail,
        "confidence": round(decision.confidence, 4),
        "margin": round(decision.margin, 4) if decision.margin is not None else None,
        "agree": decision.agree,
        "latency_ms": round(elapsed * 1000),
        "judge": getattr(verdict, "judge", None),
        "fallback": getattr(verdict, "fallback", "") or None,
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r["expected"] is not None and r["action"] == "dispatch"]
    agreed = [r for r in scored if r["agree"] is True]
    disagreed = [r for r in scored if r["agree"] is False]
    known_abstain = [r for r in rows if r["expected"] is not None and r["action"] == "ask_human"]
    unlabeled_dispatch = [r for r in rows if r["expected"] is None and r["action"] == "dispatch"]
    unlabeled_abstain = [r for r in rows if r["expected"] is None and r["action"] == "ask_human"]

    pairs = Counter(
        (str(r["expected"]), str(r["recommended"])) for r in disagreed
    )
    reasons = Counter(str(r["reason"] or "?") for r in known_abstain)
    picks = Counter(str(r["detail"]) for r in known_abstain if r.get("detail"))
    buckets = Counter(bucket(float(r["confidence"] or 0.0)) for r in scored)
    latencies = sorted(int(r["latency_ms"]) for r in rows)

    def median(values: list[int]) -> int | None:
        if not values:
            return None
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) // 2

    return {
        "cases": len(rows),
        "scored": len(scored),
        "agreed": len(agreed),
        "disagreed": len(disagreed),
        "agreement": (len(agreed) / len(scored)) if scored else None,
        "known_abstain": len(known_abstain),
        "known_abstain_reasons": dict(reasons.most_common()),
        "uncertain_picks": dict(picks.most_common()),
        "unlabeled_dispatch": len(unlabeled_dispatch),
        "unlabeled_abstain": len(unlabeled_abstain),
        "disagreement_pairs": [
            {"expected": expected, "recommended": recommended, "count": count}
            for (expected, recommended), count in pairs.most_common()
        ],
        "confidence_buckets": dict(buckets.most_common()),
        "median_latency_ms": median(latencies),
        "rows": rows,
    }


def render(summary: dict[str, Any]) -> str:
    lines = [
        "route replay (labeled set, not live shadow)",
        f"cases: {summary['cases']}  scored: {summary['scored']}  "
        f"known-abstain: {summary['known_abstain']}",
    ]
    if summary["scored"]:
        lines.append(
            f"agreement: {summary['agreed']}/{summary['scored']} "
            f"({summary['agreement'] * 100:.1f}%)"
        )
    else:
        lines.append("agreement: no scored cases")
    lines.append(
        f"unlabeled: dispatch {summary['unlabeled_dispatch']}  "
        f"abstain {summary['unlabeled_abstain']}"
    )
    if summary["disagreement_pairs"]:
        lines.append("disagreements (expected -> recommended):")
        for row in summary["disagreement_pairs"]:
            lines.append(f"  {row['expected']} -> {row['recommended']}: {row['count']}")
    if summary["known_abstain_reasons"]:
        lines.append(
            "known-case abstain reasons: "
            + ", ".join(f"{k} {v}" for k, v in summary["known_abstain_reasons"].items())
        )
    if summary["uncertain_picks"]:
        lines.append(
            "uncertain picks (judge's discarded answer): "
            + ", ".join(f"{k} {v}" for k, v in summary["uncertain_picks"].items())
        )
    if summary["confidence_buckets"]:
        lines.append(
            "dispatch confidence: "
            + ", ".join(f"{k} {v}" for k, v in summary["confidence_buckets"].items())
        )
    if summary["median_latency_ms"] is not None:
        lines.append(f"median latency: {summary['median_latency_ms']} ms")
    lines.append("")
    lines.append("per case:")
    for row in summary["rows"]:
        mark = "OK " if row["agree"] is True else ("XX " if row["agree"] is False else "-- ")
        got = row["recommended"] or row["action"]
        lines.append(
            f"  {mark}{row['expected'] or '(unlabeled)':<18} -> {got:<18} "
            f"p={row['confidence']:.2f} {row['reason']:<16} {row['task']}"
        )
        if row["note"]:
            lines.append(f"      note: {row['note']}")
    return "\n".join(lines)


async def replay(cases: list[dict[str, Any]], cfg: dict[str, Any], point: str) -> list[dict[str, Any]]:
    names = _route_candidates(None, cfg)
    rows: list[dict[str, Any]] = []
    for case in cases:
        rows.append(await run_case(case, cfg, point, names))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--point", default="agent_route")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--min-agreement",
        type=float,
        default=0.9,
        help="exit 1 below this agreement (default 0.9)",
    )
    parser.add_argument(
        "--min-scored",
        type=int,
        default=20,
        help="exit 1 below this many scored cases (default 20)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    cases = load_cases(args.cases)
    rows = asyncio.run(replay(cases, cfg, args.point))
    summary = summarise(rows)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render(summary))

    if summary["scored"] < args.min_scored:
        print(
            f"gate: only {summary['scored']} scored cases (< {args.min_scored})",
            file=sys.stderr,
        )
        return 1
    if summary["agreement"] is None or summary["agreement"] < args.min_agreement:
        print(
            f"gate: agreement {summary['agreement']} < {args.min_agreement}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
