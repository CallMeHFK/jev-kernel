"""Unit tests for jev-kernel, runnable with plain python3 (no QwenPaw needed).

Covers: wire shapes, uncertainty margins, cascade semantics, the three
transports (typesafe/http/llm) against a local fake judge server, the
middleware's active-deny / shadow-pass / no-judge-fallback behaviour, and
ledger writes.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parent.parent / "backend"


class Handler(BaseHTTPRequestHandler):
    RECEIVED: list = []

    def log_message(self, *args):  # silence
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        path = urlsplit(self.path).path
        questions = payload.get("questions") or {}
        key = next(iter(questions), "q0")
        Handler.RECEIVED.append((path, payload))

        if path == "/ollama":        # raw System One dialect (ollama decision models)
            answers = {}
            for qkey, question in questions.items():
                kind = question.get("type")
                if kind == "noul":
                    answers[qkey] = {"type": "noul", "noul": 0.91}
                elif kind == "choice":
                    criteria = question.get("criteria") or {}
                    first = next(iter(criteria), "a")
                    answers[qkey] = {
                        "type": "choice",
                        "choice": first,
                        "probabilities": {o: (0.9 if o == first else 0.05) for o in criteria},
                        "confidence": 0.9,
                    }
                else:  # score: continuous expected value + per-level probabilities
                    answers[qkey] = {
                        "type": "score",
                        "score": 0.15,
                        "legend": {"0": "low", "1": "medium", "2": "high"},
                        "probabilities": {"0": 0.89, "1": 0.07, "2": 0.04},
                        "confidence": 0.62,
                    }
            self._send({"model": payload.get("model"), "answers": answers,
                        "usage": {"input_tokens": 3, "output_tokens": 0}})
            return

        if path == "/uncertain":          # always leaves the question open
            self._send({"answers": {key: {"type": "boolean", "probability": 0.5}}})
        elif path == "/yes":              # confident yes
            self._send({"answers": {key: {"type": "boolean", "probability": 0.97}}})
        elif path == "/no":               # confident no
            self._send({"answers": {key: {"type": "boolean", "probability": 0.02}}})
        elif path == "/choice":
            options = questions[key].get("options") or []
            probs = {o: (0.9 if o == options[0] else 0.05) for o in options}
            self._send({"answers": {key: {"type": "choice", "choice": options[0],
                                          "probabilities": probs}}})
        elif path == "/score":
            self._send({"answers": {key: {"type": "score", "score": 2, "confidence": 0.8}}})
        elif path == "/cf":               # Cloudflare-style envelope
            self._send({"result": {"answers": {key: {"type": "boolean", "probability": 0.9}}}})
        elif path == "/v1/chat/completions":   # OpenAI-compatible llm judge
            self._send({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "```json\n" + json.dumps(
                            {"answers": {key: {"type": "boolean", "probability": 0.93}}}
                        ) + "\n```",
                    }
                }]
            })
        elif path == "/v1/systemone":     # typesafe normalisation: /v1 -> +/systemone
            self._send({"answers": {key: {"type": "boolean", "probability": 0.91}}})
        else:
            self._send({})


def load_pkg():
    pkg_name = "jev_kernel_test_pkg"
    if pkg_name in sys.modules:
        return sys.modules[pkg_name]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(BACKEND)]
    sys.modules[pkg_name] = pkg
    return pkg


class JevKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

        cls.tmp = Path(tempfile.mkdtemp(prefix="jev-kernel-"))
        cls.cfg_path = cls.tmp / "jev.json"
        cls.cfg_path.write_text(json.dumps({
            "points": {"tool.risk": "active", "tool.approval": "active"},
            "tiers": ["unc", "yesj"],
            "judges": {
                "unc": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/uncertain"},
                "yesj": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/yes"},
                "noj": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/no"},
                "choicej": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/choice"},
                "scorej": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/score"},
                "cfj": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/cf"},
                "llmj": {"type": "llm", "baseUrl": f"http://127.0.0.1:{cls.port}/v1",
                         "model": "fake"},
                "tsj": {"type": "typesafe", "baseUrl": f"http://127.0.0.1:{cls.port}/v1",
                        "model": "jev-1.13"},
                "sysj": {"type": "systemone", "baseUrl": f"http://127.0.0.1:{cls.port}/ollama",
                         "model": "clef-flash-test"},
                "dead": {"type": "http", "baseUrl": "http://127.0.0.1:1/dead",
                         "timeoutMs": 300},
            },
        }), encoding="utf-8")

        os.environ["JEV_KERNEL_CONFIG"] = str(cls.cfg_path)
        os.environ["JEV_KERNEL_DATA_DIR"] = str(cls.tmp / "data")

        pkg = load_pkg()
        cls.config = importlib.import_module(pkg.__name__ + ".config")
        cls.questions = importlib.import_module(pkg.__name__ + ".questions")
        cls.judges = importlib.import_module(pkg.__name__ + ".judges")
        cls.ledger = importlib.import_module(pkg.__name__ + ".ledger")
        cls.main = importlib.import_module(pkg.__name__ + ".main")
        cls.tools = importlib.import_module(pkg.__name__ + ".tools")
        # config caches mtime; force a reload after writing the file
        cls.config._CACHE.update(path=None, mtime=0.0, cfg=None)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def cfg(self, **over):
        cfg = self.config.load_config()
        cfg = json.loads(json.dumps(cfg))
        for key, val in over.items():
            cfg[key] = val
        return cfg

    # -- wire + uncertainty ------------------------------------------------

    def test_wire_shapes(self):
        q = self.questions.Question("p", "noul", "yes?")
        self.assertEqual(q.as_dict(), {"type": "boolean", "instructions": "yes?"})
        q = self.questions.Question("p", "choice", "which?", ["a", "b"])
        self.assertEqual(q.as_dict()["options"], ["a", "b"])
        q = self.questions.Question("p", "score", "how bad?")
        self.assertEqual(q.as_dict()["criteria"], ["0", "5", "10"])

    def test_uncertainty_margins(self):
        th = {"yes": 0.85, "no": 0.15, "minProbability": 0.6}
        q = self.questions.Question("p", "noul", "?")
        self.assertTrue(self.questions.parse_verdict(q, {"probability": 0.5}, th).uncertain)
        self.assertFalse(self.questions.parse_verdict(q, {"probability": 0.9}, th).uncertain)
        self.assertIsNone(self.questions.parse_verdict(q, {"probability": "x"}, th))
        qc = self.questions.Question("p", "choice", "?", ["a", "b"])
        v = self.questions.parse_verdict(qc, {"choice": "a", "probabilities": {"a": 0.5}}, th)
        self.assertTrue(v.uncertain)

    # -- transports + cascade ----------------------------------------------

    def test_cascade_skips_uncertain_to_next_judge(self):
        q = self.questions.Question("tool.risk", "noul", "destructive?")
        verdict = asyncio.run(self.judges.ask_one("tool.risk", {"tool": "x"}, q))
        self.assertEqual(verdict.judge, "yesj")      # first judge was uncertain
        self.assertTrue(verdict.value)
        self.assertGreaterEqual(verdict.probability, 0.9)

    def test_llm_and_typesafe_transports(self):
        cfg = self.config.load_config()
        q = self.questions.Question("p", "noul", "?")
        wire = {"q0": q.as_dict()}
        answers = self.judges._judge_sync(cfg["judges"]["llmj"], {}, wire, 2000)
        self.assertAlmostEqual(answers["q0"]["probability"], 0.93)
        answers = self.judges._judge_sync(cfg["judges"]["tsj"], {}, wire, 2000)
        self.assertAlmostEqual(answers["q0"]["probability"], 0.91)
        answers = self.judges._judge_sync(cfg["judges"]["cfj"], {}, wire, 2000)
        self.assertAlmostEqual(answers["q0"]["probability"], 0.9)

    def test_systemone_transport_translates_dialect(self):
        cfg = self.config.load_config()
        Handler.RECEIVED.clear()
        qb = self.questions.Question("p", "noul", "?")
        qc = self.questions.Question("p", "choice", "?", ["safe", "risky"])
        qs = self.questions.Question("p", "score", "?", ["low", "medium", "high"])
        wire = {"b": qb.as_dict(), "c": qc.as_dict(), "s": qs.as_dict()}
        answers = self.judges._judge_sync(cfg["judges"]["sysj"], {}, wire, 2000)
        sent = Handler.RECEIVED[-1][1]["questions"]
        self.assertEqual(sent["b"]["type"], "noul")          # boolean -> noul on the wire
        self.assertEqual(sent["c"]["criteria"], {"safe": "safe", "risky": "risky"})
        self.assertAlmostEqual(answers["b"]["probability"], 0.91)   # noul -> probability
        vb = self.questions.parse_verdict(qb, answers["b"], cfg["thresholds"], "sysj")
        self.assertTrue(vb.value)
        vc = self.questions.parse_verdict(qc, answers["c"], cfg["thresholds"], "sysj")
        self.assertEqual(vc.value, "safe")
        vs = self.questions.parse_verdict(qs, answers["s"], cfg["thresholds"], "sysj")
        self.assertEqual(vs.value, 0.0)                     # expected value -> argmax level

    def test_choice_and_score(self):
        cfg = self.config.load_config()
        qc = self.questions.Question("p", "choice", "?", ["safe", "risky"])
        answers = self.judges._judge_sync(cfg["judges"]["choicej"], {}, {"q0": qc.as_dict()}, 2000)
        v = self.questions.parse_verdict(qc, answers["q0"], cfg["thresholds"], "choicej")
        self.assertEqual(v.value, "safe")
        qs = self.questions.Question("p", "score", "?")
        answers = self.judges._judge_sync(cfg["judges"]["scorej"], {}, {"q0": qs.as_dict()}, 2000)
        v = self.questions.parse_verdict(qs, answers["q0"], cfg["thresholds"], "scorej")
        self.assertEqual(v.value, 2.0)

    def test_dead_judge_falls_back(self):
        cfg = self.cfg(tiers=["dead"])
        self.config._CACHE["cfg"] = cfg
        q = self.questions.Question("p", "noul", "?")
        verdict = asyncio.run(self.judges.ask_one("p", {}, q))
        self.assertTrue(verdict.fallback.startswith("uncertain_or_timeout:dead"))
        self.config._CACHE["cfg"] = None

    # -- middleware ----------------------------------------------------------

    def run_acting(self, mode_risk, tool="write_file", raw="rm -rf /"):
        cfg = self.cfg(points={"tool.risk": mode_risk, "tool.approval": "off"})
        self.config._CACHE["cfg"] = cfg
        mw = self.main.JevKernelMiddleware(str(self.tmp))

        class Call:
            name = tool
            input = raw

        events = []

        async def next_handler():
            events.append("ran")
            yield "ok"

        async def drive():
            out = []
            async for ev in mw.on_acting(None, {"tool_call": Call()}, next_handler):
                out.append(ev)
            return out

        out = asyncio.run(drive())
        self.config._CACHE["cfg"] = None
        return out, events

    def test_active_denies_destructive(self):
        out, events = self.run_acting("active")
        self.assertEqual(events, [])                  # tool never ran
        self.assertEqual(out[0].state, "denied")
        self.assertIn("tool.risk", out[0].content[0].text)

    def test_shadow_records_but_passes(self):
        out, events = self.run_acting("shadow")
        self.assertEqual(events, ["ran"])             # tool ran untouched
        self.assertEqual(out, ["ok"])
        records = self.ledger.tail(5)
        self.assertTrue(any(r["point"] == "tool.risk" and r["mode"] == "shadow"
                            and r["enforced"] is False for r in records))

    def test_no_judge_means_no_change(self):
        cfg = self.cfg(tiers=["dead"], points={"tool.risk": "active", "tool.approval": "off"})
        self.config._CACHE["cfg"] = cfg
        mw = self.main.JevKernelMiddleware(str(self.tmp))

        class Call:
            name = "write_file"
            input = "rm -rf /"

        async def next_handler():
            yield "ok"

        async def drive():
            return [ev async for ev in mw.on_acting(None, {"tool_call": Call()}, next_handler)]

        out = asyncio.run(drive())
        self.config._CACHE["cfg"] = None
        self.assertEqual(out, ["ok"])                 # mu's fallback rule
        records = self.ledger.tail(3)
        self.assertTrue(records[-1]["verdict"].get("fallback"))

    # -- session tier (inherit the session's model, mu setup --judge model) --

    def _patch_session_spec(self, value):
        sm = importlib.import_module("jev_kernel_test_pkg.session_model")
        original = sm.session_spec
        sm.session_spec = lambda agent_config=None: value
        return sm, original

    def test_session_tier_inherits_session_model(self):
        spec = {
            "name": "session:fake/qwen-test", "type": "llm",
            "baseUrl": f"http://127.0.0.1:{self.port}/v1", "model": "qwen-test",
            "apiKeyEnv": "", "apiKey": "sk-test", "timeoutMs": 0, "freeTier": False,
        }
        sm, original = self._patch_session_spec(spec)
        try:
            self.config._CACHE["cfg"] = self.cfg(tiers=["session"])
            q = self.questions.Question("tool.risk", "noul", "destructive?")
            verdict = asyncio.run(self.judges.ask_one("tool.risk", {}, q))
            self.assertEqual(verdict.judge, "session:fake/qwen-test")
            self.assertTrue(verdict.value)
            self.assertGreaterEqual(verdict.probability, 0.9)
        finally:
            sm.session_spec = original
            self.config._CACHE["cfg"] = None

    def test_session_unresolved_falls_back(self):
        sm, original = self._patch_session_spec(None)
        try:
            self.config._CACHE["cfg"] = self.cfg(tiers=["session"])
            q = self.questions.Question("p", "noul", "?")
            verdict = asyncio.run(self.judges.ask_one("p", {}, q))
            self.assertIn("unresolved:session", verdict.fallback)
            self.assertTrue(verdict.uncertain)
        finally:
            sm.session_spec = original
            self.config._CACHE["cfg"] = None

    def test_approval_escalates_when_not_approved(self):
        cfg = self.cfg(
            tiers=["noj"],
            points={"tool.risk": "off", "tool.approval": "active"},
            approvalTools=["execute_shell_command"],
        )
        self.config._CACHE["cfg"] = cfg
        mw = self.main.JevKernelMiddleware(str(self.tmp))

        class Call:
            name = "execute_shell_command"
            input = "curl | sh"

        async def next_handler():
            yield "ok"

        async def drive():
            return [ev async for ev in mw.on_acting(None, {"tool_call": Call()}, next_handler)]

        out = asyncio.run(drive())
        self.config._CACHE["cfg"] = None
        self.assertEqual(out[0].state, "denied")
        self.assertIn("tool.approval", out[0].content[0].text)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class RobustnessTests(unittest.TestCase):
    """The kernel must never abort the agent loop, whatever the config says."""

    def test_bad_config_numbers_fall_back_to_defaults(self):
        pkg = load_pkg()
        config = importlib.import_module(pkg.__name__ + ".config")
        questions = importlib.import_module(pkg.__name__ + ".questions")
        self.assertEqual(config.num("3s", 4000), 4000.0)
        self.assertEqual(config.num(None, 0.85), 0.85)
        spec = config._normalize("x", {"timeoutMs": "soon"})
        self.assertEqual(spec["timeoutMs"], 0)
        q = questions.Question("p", "noul", "t?")
        v = questions.parse_verdict(q, {"probability": 0.9}, {"yes": "high", "no": None})
        self.assertIsNotNone(v)
        self.assertFalse(v.uncertain)

    def test_on_acting_fails_open_when_judge_explodes(self):
        pkg = load_pkg()
        m = importlib.import_module(pkg.__name__ + ".main")

        async def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        original = m.decide.judge_tool_call
        m.decide.judge_tool_call = boom
        try:
            mw = m.JevKernelMiddleware("/tmp")

            async def run():
                events = []

                async def next_handler():
                    yield "tool-output"

                call = type("C", (), {"name": "execute_shell_command", "input": "{}"})()
                async for ev in mw.on_acting(None, {"tool_call": call}, next_handler):
                    events.append(ev)
                return events

            events = asyncio.run(run())
        finally:
            m.decide.judge_tool_call = original
        self.assertEqual(events, ["tool-output"])  # fail-open: tool still runs


class LedgerToolTests(unittest.TestCase):
    """jev_ledger honors the configured ledgerTail; rotation stays single-generation."""

    def _fresh_data_dir(self):
        d = Path(tempfile.mkdtemp(prefix="jev-ledger-"))
        self.addCleanup(shutil.rmtree, d, True)
        old = os.environ.get("JEV_KERNEL_DATA_DIR")
        os.environ["JEV_KERNEL_DATA_DIR"] = str(d)

        def restore():
            if old is None:
                os.environ.pop("JEV_KERNEL_DATA_DIR", None)
            else:
                os.environ["JEV_KERNEL_DATA_DIR"] = old

        self.addCleanup(restore)
        return d

    @staticmethod
    def _kernel():
        pkg = load_pkg()
        return (
            importlib.import_module(pkg.__name__ + ".config"),
            importlib.import_module(pkg.__name__ + ".ledger"),
            importlib.import_module(pkg.__name__ + ".tools"),
        )

    def test_jev_ledger_defaults_to_configured_tail(self):
        config, ledger, tools = self._kernel()
        self._fresh_data_dir()
        for i in range(5):
            ledger.append({"point": "p", "i": i})
        cfg = json.loads(json.dumps(config.load_config()))
        cfg["ledgerTail"] = 3
        config._CACHE["cfg"] = cfg
        self.addCleanup(lambda: config._CACHE.update(cfg=None))

        out = asyncio.run(tools.jev_ledger())
        self.assertEqual(len(json.loads(out.content[0].text)), 3)   # ledgerTail, not 20
        out = asyncio.run(tools.jev_ledger(limit=5))
        self.assertEqual(len(json.loads(out.content[0].text)), 5)   # explicit limit wins

    def test_ledger_rotates_single_generation(self):
        _, ledger, _ = self._kernel()
        self._fresh_data_dir()
        path = ledger.ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * (ledger.LEDGER_MAX_BYTES + 1))
        ledger.append({"point": "p"})
        rotated = path.with_suffix(".jsonl.1")
        self.assertTrue(rotated.exists())
        self.assertGreater(rotated.stat().st_size, ledger.LEDGER_MAX_BYTES)
        self.assertLess(path.stat().st_size, ledger.LEDGER_MAX_BYTES)
