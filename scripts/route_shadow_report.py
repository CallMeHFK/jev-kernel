#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agreement report for ``jev_route`` shadow records.

Reads the kernel ledger, keeps the routing records, and answers the one
question shadow mode exists for: when the dispatch table chose an agent,
how often did the router recommend the same one?

The kernel rotates its ledger in a single generation
(``ledger.jsonl`` -> ``ledger.jsonl.1``), so the report reads the rotated
generation first and then the live file. Reading only ``ledger.jsonl``
reported an empty window right after every rotation.

    python3 scripts/route_shadow_report.py
    python3 scripts/route_shadow_report.py --ledger /path/to/ledger.jsonl
    python3 scripts/route_shadow_report.py --json

Agreement counts only records that carry ``shadow_expected`` and produced a
recommendation. An abstention (``ask_human``) is reported separately: it is
not a disagreement, it is the router declining to guess.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    override = os.environ.get("JEV_KERNEL_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".qwenpaw" / "plugin-data" / "jev-kernel"


def ledger_paths(base: Path) -> list[Path]:
    """The rotated generation first, then the live file (oldest first)."""
    return [base / "ledger.jsonl.1", base / "ledger.jsonl"]


def load_all(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(load_routes(path))
    return rows


def load_routes(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("tool") == "jev_route":
                rows.append(record)
    return rows


def bucket(confidence: float) -> str:
    if confidence >= 0.9:
        return ">=0.90"
    if confidence >= 0.75:
        return "0.75-0.90"
    if confidence >= 0.6:
        return "0.60-0.75"
    return "<0.60"


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dispatch = [r for r in rows if r.get("action") == "dispatch"]
    abstain = [r for r in rows if r.get("action") == "ask_human"]
    scored = [r for r in dispatch if r.get("agree") is not None]
    agreed = [r for r in scored if r.get("agree") is True]
    disagreed = [r for r in scored if r.get("agree") is False]

    pairs = Counter(
        (str(r.get("shadow_expected")), str(r.get("recommended")))
        for r in disagreed
    )
    reasons = Counter(str(r.get("reason") or "?") for r in abstain)
    picks = Counter(
        str(r.get("detail")) for r in abstain if r.get("detail")
    )
    buckets = Counter(bucket(float(r.get("confidence") or 0.0)) for r in dispatch)

    return {
        "records": len(rows),
        "dispatch": len(dispatch),
        "abstain": len(abstain),
        "scored": len(scored),
        "agreed": len(agreed),
        "disagreed": len(disagreed),
        "agreement": (len(agreed) / len(scored)) if scored else None,
        "disagreement_pairs": [
            {"expected": expected, "recommended": recommended, "count": count}
            for (expected, recommended), count in pairs.most_common()
        ],
        "abstain_reasons": dict(reasons.most_common()),
        "uncertain_picks": dict(picks.most_common()),
        "confidence_buckets": dict(buckets.most_common()),
        "unscored_dispatch": len(dispatch) - len(scored),
    }


def render(summary: dict[str, Any]) -> str:
    lines = [
        f"jev_route records: {summary['records']}  "
        f"dispatch: {summary['dispatch']}  ask_human: {summary['abstain']}"
    ]
    if summary["scored"]:
        lines.append(
            f"agreement: {summary['agreed']}/{summary['scored']} "
            f"({summary['agreement'] * 100:.1f}%)"
        )
    else:
        lines.append("agreement: no scored dispatch records yet")
    if summary["unscored_dispatch"]:
        lines.append(
            f"dispatch without shadow_expected: {summary['unscored_dispatch']}"
        )
    if summary["disagreement_pairs"]:
        lines.append("disagreements (expected -> recommended):")
        for row in summary["disagreement_pairs"]:
            lines.append(
                f"  {row['expected']} -> {row['recommended']}: {row['count']}"
            )
    if summary["abstain_reasons"]:
        lines.append("abstain reasons: " + ", ".join(
            f"{reason} {count}" for reason, count in summary["abstain_reasons"].items()
        ))
    if summary["uncertain_picks"]:
        lines.append("uncertain picks (judge's discarded answer): " + ", ".join(
            f"{pick} {count}" for pick, count in summary["uncertain_picks"].items()
        ))
    if summary["confidence_buckets"]:
        lines.append("dispatch confidence: " + ", ".join(
            f"{name} {count}" for name, count in summary["confidence_buckets"].items()
        ))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help=(
            "read exactly this ledger file (default: both generations under "
            "$JEV_KERNEL_DATA_DIR or the QwenPaw plugin-data home)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    paths = [args.ledger] if args.ledger else ledger_paths(data_dir())
    rows = load_all(paths)
    if not rows:
        where = ", ".join(str(path) for path in paths)
        print(f"no jev_route records in {where}", file=sys.stderr)
        return 1
    summary = summarise(rows)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
