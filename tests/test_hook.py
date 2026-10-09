"""Unit tests for the multi-host PreToolUse hook (backend/hook.py).

Covers: payload spellings across hosts (snake_case and camelCase), the three
verdict dialects (claude/qoder/zcode share the hookSpecificOutput envelope,
codex adds a top-level block decision), ZCode's strict-schema key set,
shadow pass-through, approval escalation as "ask", fail-open on garbage
stdin, and ledger writes carrying the host field.

Runnable with plain python3 (no agent host needed).
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
TMP = Path(__file__).resolve().parent / "tmp_hook"


class StubJudge(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        questions = payload.get("questions") or {}
        key = next(iter(questions), "q0")
        path = urlsplit(self.path).path
        if path == "/yes":
            answers = {key: {"type": "boolean", "probability": 0.97}}
        elif path == "/no":
            answers = {key: {"type": "boolean", "probability": 0.02}}
        else:
            answers = {}
        body = json.dumps({"answers": answers}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def load_pkg():
    pkg_name = "jev_kernel_hook_test_pkg"
    if pkg_name in sys.modules:
        return sys.modules[pkg_name]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(REPO / "backend")]
    sys.modules[pkg_name] = pkg
    return pkg


class HookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubJudge)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        TMP.mkdir(parents=True, exist_ok=True)
        cls.cfg_path = TMP / "jev.json"
        cls.data_dir = TMP / "data"
        os.environ["JEV_KERNEL_CONFIG"] = str(cls.cfg_path)
        os.environ["JEV_KERNEL_DATA_DIR"] = str(cls.data_dir)

        pkg = load_pkg()
        cls.config = importlib.import_module(pkg.__name__ + ".config")
        cls.hook = importlib.import_module(pkg.__name__ + ".hook")
        cls.write_cfg(
            points={"tool.risk": "active", "tool.approval": "active"},
            tiers=["yesj"],
            judges={
                "yesj": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/yes"},
                "noj": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/no"},
            },
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    @classmethod
    def write_cfg(cls, **over):
        cfg = {
            "points": {"tool.risk": "shadow", "tool.approval": "off"},
            "tiers": ["yesj"],
            "judges": {
                "yesj": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/yes"},
                "noj": {"type": "http", "baseUrl": f"http://127.0.0.1:{cls.port}/no"},
            },
        }
        cfg.update(over)
        cls.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        os.utime(cls.cfg_path, (cls.cfg_path.stat().st_atime,
                                cls.cfg_path.stat().st_mtime + 1))
        cls.config._CACHE.update(path=None, mtime=0.0, cfg=None)

    def run_hook(self, host: str, payload: dict) -> tuple[int, str, str]:
        stdin = io.StringIO(json.dumps(payload))
        stdout, stderr = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = stdin
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = self.hook.main(["--host", host])
        finally:
            sys.stdin = old_stdin
        return rc, stdout.getvalue(), stderr.getvalue()

    def ledger_records(self):
        path = self.data_dir / "ledger.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    # -- verdict dialects --------------------------------------------------

    def test_claude_deny_envelope(self):
        rc, out, _ = self.run_hook("claude", {
            "session_id": "s1", "cwd": "/work", "hook_event_name": "PreToolUse",
            "tool_name": "Bash", "tool_input": {"command": "rm -rf ~/Documents"},
        })
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(set(doc), {"hookSpecificOutput"})
        inner = doc["hookSpecificOutput"]
        self.assertEqual(
            set(inner),
            {"hookEventName", "permissionDecision", "permissionDecisionReason"},
        )
        self.assertEqual(inner["hookEventName"], "PreToolUse")
        self.assertEqual(inner["permissionDecision"], "deny")
        self.assertIn("tool.risk", inner["permissionDecisionReason"])

    def test_zcode_camelcase_payload_same_envelope(self):
        rc, out, _ = self.run_hook("zcode", {
            "sessionId": "s2", "cwd": "/work", "hookEventName": "PreToolUse",
            "toolName": "Write", "toolInput": {"file_path": "/etc/passwd", "content": "x"},
        })
        self.assertEqual(rc, 0)
        inner = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(inner["permissionDecision"], "deny")
        # ZCode's strict schema silently discards documents with unknown keys:
        # the envelope must carry exactly the keys above, nothing else.
        self.assertEqual(
            set(inner),
            {"hookEventName", "permissionDecision", "permissionDecisionReason"},
        )

    def test_codex_deny_top_level_block(self):
        rc, out, _ = self.run_hook("codex", {
            "session_id": "s3", "turn_id": "t1", "hook_event_name": "PreToolUse",
            "tool_name": "shell", "tool_input": {"command": "git push --force"},
        })
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["decision"], "block")
        self.assertIn("tool.risk", doc["reason"])

    def test_approval_point_escalates_to_ask(self):
        self.write_cfg(
            points={"tool.risk": "off", "tool.approval": "active"},
            tiers=["noj"],
            approvalTools=["Bash"],
        )
        try:
            rc, out, _ = self.run_hook("qoder", {
                "cwd": "/work", "tool_name": "Bash",
                "tool_input": {"command": "make deploy"},
            })
            self.assertEqual(rc, 0)
            inner = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(inner["permissionDecision"], "ask")
            self.assertIn("tool.approval", inner["permissionDecisionReason"])
        finally:
            self.write_cfg(points={"tool.risk": "active", "tool.approval": "active"})

    # -- pass-through and fail-open ----------------------------------------

    def test_shadow_mode_prints_nothing(self):
        self.write_cfg(points={"tool.risk": "shadow", "tool.approval": "off"})
        try:
            rc, out, _ = self.run_hook("claude", {
                "tool_name": "Bash", "tool_input": {"command": "rm -rf /"},
            })
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")
        finally:
            self.write_cfg(points={"tool.risk": "active", "tool.approval": "active"})

    def test_no_judge_falls_open(self):
        self.write_cfg(tiers=[])
        try:
            rc, out, _ = self.run_hook("zcode", {
                "tool_name": "Bash", "tool_input": {"command": "rm -rf /"},
            })
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")
        finally:
            self.write_cfg(tiers=["yesj"])

    def test_garbage_stdin_fails_open(self):
        stdin = io.StringIO("not json at all")
        stdout = io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = stdin
        try:
            with contextlib.redirect_stdout(stdout):
                rc = self.hook.main(["--host", "claude"])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue(), "")

    def test_unknown_host_rejected(self):
        self.assertEqual(self.hook.main(["--host", "emacs"]), 2)

    def test_skip_tools_never_judged(self):
        rc, out, _ = self.run_hook("claude", {
            "tool_name": "jev_ask", "tool_input": {"question": "rm -rf /?"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    # -- ledger ------------------------------------------------------------

    def test_ledger_records_host(self):
        before = len(self.ledger_records())
        self.run_hook("zcode", {
            "cwd": "/work", "toolName": "Bash", "toolInput": {"command": "rm -rf ~"},
        })
        records = self.ledger_records()
        self.assertGreater(len(records), before)
        last = records[-1]
        self.assertEqual(last["host"], "zcode")
        self.assertEqual(last["tool"], "Bash")
        self.assertTrue(last["enforced"])

    # -- the installed invocation shape ------------------------------------

    def test_direct_script_invocation(self):
        """The registered command is `python3 backend/hook.py --host …` —
        run exactly that, with no package context on sys.path."""
        env = dict(os.environ)
        env["JEV_KERNEL_CONFIG"] = str(self.cfg_path)
        env["JEV_KERNEL_DATA_DIR"] = str(self.data_dir)
        proc = subprocess.run(
            [sys.executable, str(REPO / "backend" / "hook.py"), "--host", "qoder"],
            input=json.dumps({
                "cwd": "/work", "tool_name": "Bash",
                "tool_input": {"command": "rm -rf ~/Documents"},
            }),
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(
            doc["hookSpecificOutput"]["permissionDecision"], "deny",
        )


if __name__ == "__main__":
    unittest.main()
