"""jev-kernel: a mu-style judgment kernel on QwenPaw's tool seam.

Reference implementation: https://github.com/qybaihe/mu — docs/judges.md
and docs/reference/decision-points.md. mu puts a small fast judge at every
decision point of every turn: one bounded typed question about a small
state, answered with a calibrated probability, recorded in a ledger, and —
in active mode — allowed to change what happens next. This plugin ports
that pattern to QwenPaw:

* decision points ``tool.risk`` and ``tool.approval`` sit on the tool seam
  (AgentScope middleware ``on_acting``), the same seam dispatch-guard uses;
* each point has mu's three modes: ``active`` (verdict can deny/escalate),
  ``shadow`` (verdict is ledgered, behaviour unchanged), ``off``;
* judges cascade through ``tiers``/``routes``; later judges only see the
  questions earlier ones left uncertain;
* transports: TypeSafe System One protocol (``POST {model, state,
  questions}``), plain HTTP (``POST {state, questions}`` -> ``{answers}``),
  or any OpenAI-compatible chat model prompted to answer as JSON;
* the built-in ``jev`` tier resolves mu's provider order from environment
  keys (TYPESAFE_API_KEY, MU_JUDGE_OPENROUTER_API_KEY, AI_GATEWAY_API_KEY,
  OPENCODE_API_KEY, CLOUDFLARE_*), falling back to the free OpenCode Zen
  tier when no key is set;
* every verdict lands in ``~/.qwenpaw/plugin-data/jev-kernel/ledger.jsonl``
  with its probability, judge, latency and fallback reason;
* ``jev_ask`` / ``jev_ledger`` tools expose the kernel to the agent.

No judge answering in time means mu's rule: the point falls back to what
it does without a verdict (nothing changes) and the ledger records why.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Callable

try:
    from agentscope.message import TextBlock, ToolResultState
    from agentscope.middleware import MiddlewareBase
    from agentscope.tool import ToolResponse
except ImportError:  # allow unit tests without AgentScope installed
    from dataclasses import dataclass, field

    class MiddlewareBase:  # type: ignore[no-redef]
        pass

    @dataclass
    class TextBlock:  # type: ignore[no-redef]
        type: str = "text"
        text: str = ""

    @dataclass
    class ToolResponse:  # type: ignore[no-redef]
        content: list = field(default_factory=list)
        state: object = "success"

    class ToolResultState:  # type: ignore[no-redef]
        DENIED = "denied"
        SUCCESS = "success"

try:
    from qwenpaw.plugins.api import PluginApi
except ImportError:  # allow unit tests without QwenPaw installed

    class PluginApi:  # type: ignore[no-redef]
        pass

import importlib as _importlib
import sys as _sys

# Hot reload re-executes this module but keeps the package's cached sibling
# submodules, so `from . import judges` would bind stale code. Refresh them in
# place (never this module or the package itself): reload re-executes the
# module object already in sys.modules, so every existing reference — including
# a test suite that imported the siblings first — sees the new code too.
for _mod_name in list(_sys.modules):
    if _mod_name.startswith(f"{__package__}.") and _mod_name != __name__:
        try:
            _importlib.reload(_sys.modules[_mod_name])
        except Exception:  # noqa: BLE001 - unreadable stale module: drop it
            del _sys.modules[_mod_name]

from . import judges, ledger, tools
from .config import load_config, num, point_mode, plugin_data_dir
from .questions import Question

logger = logging.getLogger("jev_kernel.qwenpaw")

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


class JevKernelMiddleware(MiddlewareBase):
    """Asks the kernel at decision points before the tool executes."""

    def __init__(self, workspace_dir: str, agent_config: Any = None) -> None:
        self._ws = str(workspace_dir or "")
        self._agent_config = agent_config

    # -- helpers ---------------------------------------------------------

    def _state_for(self, tool_name: str, raw_input: Any, cfg: dict[str, Any]) -> dict[str, Any]:
        chars = int(cfg.get("stateChars") or 4000)
        blob = raw_input if isinstance(raw_input, str) else str(raw_input)
        if len(blob) > chars:
            blob = blob[:chars] + " …[truncated]"
        return {"tool": tool_name, "input": blob, "workspace": self._ws}

    def _record(
        self,
        point: str,
        mode: str,
        tool_name: str,
        state: dict[str, Any],
        question: Question,
        verdict: Any,
        enforced: bool,
        cfg: dict[str, Any],
    ) -> None:
        record: dict[str, Any] = {
            "point": point,
            "mode": mode,
            "tool": tool_name,
            "question": question.as_dict(),
            "verdict": verdict.as_dict(),
            "enforced": enforced,
            "workspace": self._ws,
        }
        if cfg.get("recordState"):
            record["state"] = state
        ledger.append(record)

    def _deny(self, text: str, tool_name: str) -> ToolResponse:
        logger.info("jev-kernel: denied %s (%s)", tool_name, text[:80])
        return ToolResponse(
            content=[TextBlock(type="text", text=text)],
            state=ToolResultState.DENIED,
        )

    # -- decision points ---------------------------------------------------

    async def _judge(self, cfg: dict[str, Any], tool_name: str, raw_input: Any) -> str | None:
        thresholds = cfg.get("thresholds") or {}
        state = self._state_for(tool_name, raw_input, cfg)

        mode = point_mode(cfg, "tool.risk")
        if mode != "off":
            question = Question("tool.risk", "noul", RISK_QUESTION)
            verdict = await judges.ask_one("tool.risk", state, question, self._agent_config)
            risky = (
                not verdict.fallback
                and verdict.value is True
                and verdict.probability >= num(thresholds.get("risk"), 0.85)
            )
            self._record("tool.risk", mode, tool_name, state, question, verdict,
                         bool(risky and mode == "active"), cfg)
            if mode == "active" and risky:
                return (
                    f"jev-kernel: decision point tool.risk judged this call "
                    f"destructive or irreversible (P(yes)="
                    f"{verdict.probability:.2f}, judge={verdict.judge}, "
                    f"{verdict.latency_ms:.0f}ms). Ask the user for explicit "
                    f"confirmation of exactly this action before retrying, "
                    f"or set points[\"tool.risk\"] to \"shadow\" in jev.json."
                )

        if tool_name in set(cfg.get("approvalTools") or []):
            mode = point_mode(cfg, "tool.approval")
            if mode != "off":
                question = Question("tool.approval", "noul", APPROVAL_QUESTION)
                verdict = await judges.ask_one("tool.approval", state, question, self._agent_config)
                approved = (
                    not verdict.fallback
                    and verdict.value is True
                    and verdict.probability >= num(thresholds.get("yes"), 0.85)
                )
                self._record("tool.approval", mode, tool_name, state, question,
                             verdict, bool(not approved and mode == "active"), cfg)
                if mode == "active" and not approved:
                    return (
                        f"jev-kernel: decision point tool.approval did not "
                        f"auto-approve this call (verdict="
                        f"{verdict.as_dict()['value']}, P="
                        f"{verdict.probability:.2f}, judge="
                        f"{verdict.judge or 'none'}). Present the exact "
                        f"command to the user and wait for confirmation "
                        f"before retrying."
                    )
        return None

    # -- hook ---------------------------------------------------------------

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        tool_call = input_kwargs.get("tool_call")
        name = getattr(tool_call, "name", "") or ""
        raw_input = getattr(tool_call, "input", "") or ""

        try:
            cfg = load_config()
            if name not in set(cfg.get("skipTools") or []):
                denial = await self._judge(cfg, name, raw_input)
                if denial is not None:
                    yield self._deny(denial, name)
                    return
        except Exception:  # noqa: BLE001 - the kernel must never abort the agent loop
            logger.exception("jev-kernel: judgment failed for %s; failing open", name)

        async for event in next_handler():
            yield event


def _factory(ctx: Any, agent_config: Any) -> JevKernelMiddleware | None:
    cfg = load_config()
    if cfg.get("enabled") is False:
        return None
    modes = {point_mode(cfg, p) for p in ("tool.risk", "tool.approval")}
    if modes == {"off"}:
        return None  # nothing to judge: stay out of the loop entirely
    workspace_dir = getattr(ctx, "workspace_dir", None)
    return JevKernelMiddleware(workspace_dir or "", agent_config)


def _inject_bootstrap() -> None:
    """Survive zero-downtime reloads (skill step A6)."""
    try:
        from qwenpaw.plugins.registry import PluginRegistry

        mgr = PluginRegistry().get_workspace_manager()
        if mgr is not None and hasattr(mgr, "_bootstrap_kwargs"):
            funcs = mgr._bootstrap_kwargs.setdefault("builtin_tool_funcs", [])
            for fn in (tools.jev_ask, tools.jev_ledger):
                if fn not in funcs:
                    funcs.append(fn)
    except Exception:  # noqa: BLE001
        logger.exception("jev-kernel: bootstrap injection failed")


def _ensure_dirs() -> None:
    try:
        plugin_data_dir().mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception("jev-kernel: cannot create plugin-data dir")


class JevKernelPlugin:
    def register(self, api: PluginApi) -> None:
        api.register_middleware(_factory, priority=45)

        # governance (skill step A4): our tools take no agent-controlled
        # network target, so they are internal.
        try:
            from qwenpaw.governance.tool_registry import DEFAULT_REGISTRY

            DEFAULT_REGISTRY.register("JevAsk", "internal", "")
            DEFAULT_REGISTRY.register_python_name("jev_ask", "JevAsk")
            DEFAULT_REGISTRY.register("JevLedger", "internal", "")
            DEFAULT_REGISTRY.register_python_name("jev_ledger", "JevLedger")
        except Exception:  # noqa: BLE001
            logger.exception("jev-kernel: governance registration failed")

        # tool registration (skill step A3)
        try:
            api.register_tool(
                tool_name="jev_ask",
                tool_func=tools.jev_ask,
                description=tools.ASK_DESC,
                icon="⚖️",
                enabled=True,
            )
            api.register_tool(
                tool_name="jev_ledger",
                tool_func=tools.jev_ledger,
                description=tools.LEDGER_DESC,
                icon="📒",
                enabled=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("jev-kernel: register_tool failed")

        # workspace ToolRegistry (skill step A5)
        try:
            from qwenpaw.modes.base import AgentMode

            descs = [
                getattr(tools.jev_ask, "_tool_descriptor", None),
                getattr(tools.jev_ledger, "_tool_descriptor", None),
            ]
            descs = [d for d in descs if d is not None]
            if descs:

                class JevKernelMode(AgentMode):
                    name = "jev-kernel-mode"

                    def tools(self):  # noqa: D102
                        return list(descs)

                api.register_mode(JevKernelMode)
        except Exception:  # noqa: BLE001
            logger.exception("jev-kernel: register_mode failed")

        api.register_startup_hook("jev_kernel_bootstrap_inject", _inject_bootstrap, priority=55)
        api.register_startup_hook("jev_kernel_datadir", _ensure_dirs, priority=90)


plugin = JevKernelPlugin()
