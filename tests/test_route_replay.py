"""Replay eval: labeled cases score agreement, abstains stay visible."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_replay():
    spec = importlib.util.spec_from_file_location(
        "jev_route_replay", REPO / "scripts" / "route_replay_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RouteReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.replay = load_replay()

    def test_shipped_case_set_covers_every_candidate(self):
        cases = self.replay.load_cases(REPO / "scripts" / "route_cases.json")
        self.assertGreaterEqual(len(cases), 20)
        expected = {c["expected"] for c in cases if c["expected"] is not None}
        for name in (
            "IPP",
            "Codex_Agent",
            "PCB_Agent",
            "Designer",
            "PM",
            "PersonalHelper",
            "SkillEvolver_Agent",
            "default",
        ):
            self.assertIn(name, expected)
        self.assertTrue(any(c["expected"] is None for c in cases))

    def test_load_cases_rejects_a_blank_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps([{"task": "  "}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.replay.load_cases(path)

    def test_summarise_scores_labeled_dispatch_only(self):
        rows = [
            {"expected": "A", "recommended": "A", "action": "dispatch",
             "agree": True, "reason": "ok", "detail": None,
             "confidence": 0.9, "latency_ms": 100, "task": "t", "note": ""},
            {"expected": "A", "recommended": "B", "action": "dispatch",
             "agree": False, "reason": "ok", "detail": None,
             "confidence": 0.7, "latency_ms": 100, "task": "t", "note": ""},
            {"expected": "B", "recommended": None, "action": "ask_human",
             "agree": None, "reason": "uncertain", "detail": "default@0.43",
             "confidence": 0.43, "latency_ms": 100, "task": "t", "note": ""},
            {"expected": None, "recommended": None, "action": "ask_human",
             "agree": None, "reason": "uncertain", "detail": "ask_human@0.37",
             "confidence": 0.37, "latency_ms": 100, "task": "t", "note": ""},
        ]
        summary = self.replay.summarise(rows)
        self.assertEqual(summary["scored"], 2)
        self.assertEqual(summary["agreed"], 1)
        self.assertEqual(summary["agreement"], 0.5)
        self.assertEqual(summary["known_abstain"], 1)
        self.assertEqual(summary["known_abstain_reasons"], {"uncertain": 1})
        self.assertEqual(summary["uncertain_picks"], {"default@0.43": 1})
        self.assertEqual(summary["unlabeled_abstain"], 1)
        self.assertEqual(
            summary["disagreement_pairs"],
            [{"expected": "A", "recommended": "B", "count": 1}],
        )


if __name__ == "__main__":
    unittest.main()
