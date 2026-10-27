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
* four transports: ``typesafe`` (TypeSafe System One protocol, ``POST
  {model, state, questions}``), ``http`` (plain ``POST {state, questions}``
  -> ``{answers}``), ``systemone`` (raw System One servers — ollama
  decision models — with the dialect translated both ways), or ``llm``
  (any OpenAI-compatible chat model prompted to answer as JSON);
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

from . import decide, tools
from .config import load_config, point_mode, plugin_data_dir

logger = logging.getLogger("jev_kernel.qwenpaw")


class JevKernelMiddleware(MiddlewareBase):
    """Asks the kernel at decision points before the tool executes."""

    def __init__(self, workspace_dir: str, agent_config: Any = None) -> None:
        self._ws = str(workspace_dir or "")
        self._agent_config = agent_config

    def _deny(self, text: str, tool_name: str) -> ToolResponse:
        logger.info("jev-kernel: denied %s (%s)", tool_name, text[:80])
        return ToolResponse(
            content=[TextBlock(type="text", text=text)],
            state=ToolResultState.DENIED,
        )

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
                decision = await decide.judge_tool_call(
                    cfg, name, raw_input, workspace=self._ws,
                    host="qwenpaw", agent_config=self._agent_config,
                )
                if decision is not None:
                    yield self._deny(decision.message, name)
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
            for fn in (tools.jev_ask, tools.jev_route, tools.jev_ledger):
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
            DEFAULT_REGISTRY.register("JevRoute", "internal", "")
            DEFAULT_REGISTRY.register_python_name("jev_route", "JevRoute")
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
                tool_type="internal",
            )
            api.register_tool(
                tool_name="jev_route",
                tool_func=tools.jev_route,
                description=tools.ROUTE_DESC,
                icon="🧭",
                enabled=True,
                tool_type="internal",
            )
            api.register_tool(
                tool_name="jev_ledger",
                tool_func=tools.jev_ledger,
                description=tools.LEDGER_DESC,
                icon="📒",
                enabled=True,
                tool_type="internal",
            )
        except Exception:  # noqa: BLE001
            logger.exception("jev-kernel: register_tool failed")

        # workspace ToolRegistry (skill step A5)
        try:
            from qwenpaw.modes.base import AgentMode

            descs = [
                getattr(tools.jev_ask, "_tool_descriptor", None),
                getattr(tools.jev_route, "_tool_descriptor", None),
                getattr(tools.jev_ledger, "_tool_descriptor", None),
            ]
            descs = [d for d in descs if d is not None]
            if descs:

                class JevKernelMode(AgentMode):
                    name = "jev-kernel-mode"

                    def tools(self):  # noqa: D102
                        return list(descs)

                    def setup(self, workspace):  # noqa: D102
                        # register_tool already injected these descriptors;
                        # replace the same-plugin copies instead of colliding
                        # with them on the mode projection.
                        self._registered = []
                        registry = workspace.plugins.tool_registry
                        for desc in self.tools():
                            if desc.name in registry:
                                registry.unregister(desc.name)
                            registry.register(desc)
                            self._registered.append(("tool", desc))

                api.register_mode(JevKernelMode)
        except Exception:  # noqa: BLE001
            logger.exception("jev-kernel: register_mode failed")

        api.register_startup_hook("jev_kernel_bootstrap_inject", _inject_bootstrap, priority=55)
        api.register_startup_hook("jev_kernel_datadir", _ensure_dirs, priority=90)


plugin = JevKernelPlugin()
