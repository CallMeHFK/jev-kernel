"""Shadow agreement report: only scored dispatches count, abstains do not."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_report():
    spec = importlib.util.spec_from_file_location(
        "jev_route_report", REPO / "scripts" / "route_shadow_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RouteReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = load_report()

    def test_summarise_counts_only_scored_dispatch(self):
        rows = [
            {"tool": "jev_route", "action": "dispatch", "agree": True,
             "shadow_expected": "A", "recommended": "A", "confidence": 0.9,
             "reason": "ok"},
            {"tool": "jev_route", "action": "dispatch", "agree": False,
             "shadow_expected": "A", "recommended": "B", "confidence": 0.7,
             "reason": "ok"},
            {"tool": "jev_route", "action": "dispatch", "agree": None,
             "shadow_expected": None, "recommended": "A", "confidence": 0.8,
             "reason": "ok"},
            {"tool": "jev_route", "action": "ask_human", "agree": None,
             "shadow_expected": "A", "recommended": None, "confidence": 0.0,
             "reason": "close_call"},
        ]
        summary = self.report.summarise(rows)
        self.assertEqual(summary["records"], 4)
        self.assertEqual(summary["scored"], 2)
        self.assertEqual(summary["agreed"], 1)
        self.assertEqual(summary["disagreed"], 1)
        self.assertAlmostEqual(summary["agreement"], 0.5)
        self.assertEqual(summary["unscored_dispatch"], 1)
        self.assertEqual(summary["abstain_reasons"], {"close_call": 1})
        self.assertEqual(
            summary["disagreement_pairs"],
            [{"expected": "A", "recommended": "B", "count": 1}],
        )

    def test_load_filters_other_tools_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text(
                "\n".join([
                    json.dumps({"tool": "jev_route", "action": "dispatch"}),
                    "not json",
                    json.dumps({"tool": "jev_ask"}),
                    json.dumps({"tool": "jev_route", "action": "ask_human"}),
                ]),
                encoding="utf-8",
            )
            rows = self.report.load_routes(path)
        self.assertEqual(
            [row["action"] for row in rows],
            ["dispatch", "ask_human"],
        )

    def test_render_says_so_when_nothing_is_scored(self):
        text = self.report.render(self.report.summarise([
            {"tool": "jev_route", "action": "dispatch", "agree": None,
             "shadow_expected": None, "recommended": "A", "confidence": 0.8},
        ]))
        self.assertIn("no scored dispatch records yet", text)

    def _write_generations(self, base: Path) -> None:
        dispatch = {"tool": "jev_route", "action": "dispatch", "agree": True,
                    "shadow_expected": "A", "recommended": "A", "confidence": 0.9}
        abstain = {"tool": "jev_route", "action": "ask_human", "agree": None,
                   "reason": "uncertain"}
        (base / "ledger.jsonl.1").write_text(json.dumps(dispatch) + "\n", encoding="utf-8")
        (base / "ledger.jsonl").write_text(json.dumps(abstain) + "\n", encoding="utf-8")

    def test_ledger_paths_include_the_rotated_generation_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_generations(base)
            paths = self.report.ledger_paths(base)
        self.assertEqual([p.name for p in paths], ["ledger.jsonl.1", "ledger.jsonl"])

    def test_load_all_reads_both_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_generations(base)
            rows = self.report.load_all(self.report.ledger_paths(base))
        self.assertEqual([row["action"] for row in rows], ["dispatch", "ask_human"])

    def test_main_survives_a_rotation(self):
        # Regression: after the ledger rotated, the report read only the live
        # file and exited 1 with "no jev_route records" — an empty window
        # instead of the records that were still on disk.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_generations(Path(tmp))
            previous = os.environ.get("JEV_KERNEL_DATA_DIR")
            os.environ["JEV_KERNEL_DATA_DIR"] = tmp
            stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout):
                    code = self.report.main(["--json"])
            finally:
                if previous is None:
                    os.environ.pop("JEV_KERNEL_DATA_DIR", None)
                else:
                    os.environ["JEV_KERNEL_DATA_DIR"] = previous
        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["records"], 2)
        self.assertEqual(summary["scored"], 1)
        self.assertEqual(summary["abstain"], 1)


if __name__ == "__main__":
    unittest.main()
