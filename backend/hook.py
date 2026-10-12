"""PreToolUse hook entry for hook-based agent hosts.

Usage (registered as a ``command`` hook in the host's config):

    python3 backend/hook.py --host zcode        # also: claude, qoder, codex

The host pipes one JSON event on stdin; the kernel answers on stdout in the
host's verdict dialect. All four supported hosts read the Claude-Code-style
envelope ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
"permissionDecision": "allow|deny|ask", ...}}`` — verified against the
qodercli 1.1.64 binary, ZCode's app.asar (outcome enum
success|blocked|failed|cancelled|timed_out + the deny envelope openviking's
uri-guard already ships), and codex 0.159.2's wire schema (which additionally
honours a top-level ``{"decision": "block", "reason": ...}``). Codex deny
emits both shapes; ZCode's strict schema validator silently discards any
document carrying a key it does not know, so every other host gets exactly
the hookSpecificOutput envelope and nothing else.

Fail-open is the mu rule on this seam too: any error — bad stdin, dead
judge, bug in the kernel — exits 0 with no stdout, which every host reads
as "no opinion", and the reason lands in the ledger instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # direct `python3 backend/hook.py` invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "backend"

from . import decide  # noqa: E402
from .config import load_config  # noqa: E402

HOSTS = ("claude", "qoder", "zcode", "codex")


def tool_call_of(payload: dict[str, Any]) -> tuple[str, Any]:
    """Tool name and input under any of the spellings the hosts use."""
    name = (
        payload.get("tool_name")
        or payload.get("toolName")
        or payload.get("name")
        or payload.get("tool")
        or ""
    )
    tool_input = (
        payload.get("tool_input")
        if "tool_input" in payload
        else payload.get("toolInput", payload.get("input", ""))
    )
    return str(name), tool_input


def envelope_for(host: str, decision: decide.Decision) -> dict[str, Any]:
    """The verdict in the host's dialect. Only called when intervening."""
    envelope: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.action,
            "permissionDecisionReason": decision.message,
        }
    }
    if host == "codex" and decision.action == "deny":
        # Codex deny emits both shapes: the envelope every host reads, plus
        # the top-level block codex additionally honours.
        envelope["decision"] = "block"
        envelope["reason"] = decision.message
    return envelope


async def _run(host: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_name, tool_input = tool_call_of(payload)
    if not tool_name:
        return None
    cfg = load_config()
    if tool_name in set(cfg.get("skipTools") or []):
        return None
    workspace = str(payload.get("cwd") or "")
    decision = await decide.judge_tool_call(
        cfg, tool_name, tool_input, workspace=workspace, host=host
    )
    if decision is None:
        return None
    return envelope_for(host, decision)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    host = "claude"
    if "--host" in args:
        i = args.index("--host")
        if i + 1 < len(args):
            host = args[i + 1]
    if host not in HOSTS:
        print(f"jev-kernel: unknown host {host!r} (one of {', '.join(HOSTS)})",
              file=sys.stderr)
        return 2

    # Deployed installs set JEV_KERNEL_DATA_DIR to the host's own folder
    # (~/.zcode/jev-kernel etc. — code, jev.json and ledger co-live there).
    # Running straight from the repo falls back to a shared ~/.jev-kernel.
    os.environ.setdefault("JEV_KERNEL_DATA_DIR", str(Path.home() / ".jev-kernel"))

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        verdict = asyncio.run(_run(host, payload))
        if verdict:
            json.dump(verdict, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
    except Exception as e:  # noqa: BLE001 - fail open: a dead kernel never blocks a tool
        print(f"jev-kernel: hook failed open ({type(e).__name__}: {e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
