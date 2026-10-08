"""Configuration for the jev judgment kernel.

Resolution order (first hit wins), hot-reloaded by mtime like
dispatch-guard's routes.json:

1. ``$JEV_KERNEL_CONFIG``            — explicit override (tests, one-offs)
2. ``<plugin dir>/jev.json``         — operator override checked first
3. ``~/.qwenpaw/plugin-data/jev-kernel/jev.json`` — durable home that
   survives plugin reinstalls (the installer rebuilds the plugin dir)
4. built-in defaults

The schema mirrors mu's ``~/.mu/agent/mu.json`` judge section
(docs/judges.md): ``tiers`` cascade, per-point ``routes``, named ``judges``
with ``type``/``baseUrl``/``model``/``apiKeyEnv``/``timeoutMs``, plus mu's
decision-point modes (``active`` | ``shadow`` | ``off``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("jev_kernel.config")

PLUGIN_DIR = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    # decision point -> mode (active | shadow | off); mu's three modes.
    # shadow records verdicts in the ledger but never changes behaviour;
    # active lets the verdict deny/escalate; off skips the judge entirely.
    "points": {"tool.risk": "shadow", "tool.approval": "off"},
    # cascade order: later judges only see questions earlier ones left
    # uncertain (mu's tiers semantics). Empty by default: no judge is
    # contacted until the operator configures one (or exports a provider
    # key and sets tiers to ["jev"]) — mu's free tier would otherwise send
    # judged state to a third party on every tool call without consent.
    "tiers": [],
    # per decision point override of tiers.
    "routes": {},
    # named judge specs: {name: {type, baseUrl, model, apiKeyEnv, timeoutMs}}
    "judges": {},
    "thresholds": {"yes": 0.85, "no": 0.15, "minProbability": 0.6, "risk": 0.85},
    # tool.approval only intercepts these tools.
    "approvalTools": ["execute_shell_command", "run_command"],
    # never judge our own tools (no recursion).
    "skipTools": ["jev_ask", "jev_ledger"],
    "timeoutMs": 4000,
    # keep the judged state in ledger records (mu's recordState; off by
    # default because states hold user content).
    "recordState": False,
    "stateChars": 4000,
    "ledgerTail": 20,
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"path": None, "mtime": 0.0, "cfg": None}


def plugin_data_dir() -> Path:
    override = os.environ.get("JEV_KERNEL_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".qwenpaw" / "plugin-data" / "jev-kernel"


def config_path() -> Path | None:
    env = os.environ.get("JEV_KERNEL_CONFIG")
    if env:
        p = Path(env)
        return p if p.exists() else None
    local = PLUGIN_DIR / "jev.json"
    if local.exists():
        return local
    durable = plugin_data_dir() / "jev.json"
    if durable.exists():
        return durable
    return None


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config() -> dict[str, Any]:
    """Current config, re-read when the source file's mtime changes."""
    path = config_path()
    try:
        mtime = path.stat().st_mtime if path else 0.0
    except OSError:
        mtime = 0.0
    with _LOCK:
        if (
            _CACHE["cfg"] is not None
            and _CACHE["path"] == path
            and _CACHE["mtime"] == mtime
        ):
            return _CACHE["cfg"]
        cfg = json.loads(json.dumps(DEFAULTS))
        if path is not None:
            try:
                user = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(user, dict):
                    cfg = _deep_merge(cfg, user)
                else:
                    logger.warning("jev-kernel: %s is not an object; using defaults", path)
            except Exception:  # noqa: BLE001 - a bad config must never wedge the agent
                logger.exception("jev-kernel: bad config %s; using defaults", path)
        _CACHE.update(path=path, mtime=mtime, cfg=cfg)
        return cfg


def point_mode(cfg: dict[str, Any], point: str) -> str:
    mode = (cfg.get("points") or {}).get(point, "off")
    return mode if mode in ("active", "shadow", "off") else "off"


def resolve_judge_name(
    cfg: dict[str, Any], name: str, agent_config: Any = None
) -> list[dict[str, Any]]:
    """Expand a tier/route entry into concrete judge specs.

    ``jev`` expands to mu's built-in provider order (docs/judges.md): the
    first service whose key is set wins, and with no key at all the free
    OpenCode Zen tier is used. ``session`` inherits the model the QwenPaw
    session itself runs on (mu's ``mu setup --judge model``). Named entries
    come from ``judges``.
    """
    if name == "jev":
        return _builtin_jev()
    if name == "session":
        from . import session_model

        spec = session_model.session_spec(agent_config)
        return [spec] if spec else []
    spec = (cfg.get("judges") or {}).get(name)
    if isinstance(spec, dict):
        return [_normalize(name, spec)]
    return []


def _normalize(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "type": str(spec.get("type", "http")),
        "baseUrl": str(spec.get("baseUrl", "")).rstrip("/"),
        "model": str(spec.get("model", "")),
        "apiKeyEnv": str(spec.get("apiKeyEnv", "")),
        "timeoutMs": int(spec.get("timeoutMs", 0) or 0),
        "freeTier": bool(spec.get("freeTier", False)),
    }


def _builtin_jev() -> list[dict[str, Any]]:
    env = os.environ
    order: list[tuple[str, dict[str, Any]]] = []
    if env.get("TYPESAFE_API_KEY"):
        order.append((
            "jev-direct",
            {"type": "typesafe", "baseUrl": "https://api.typesafe.ai/v1/systemone",
             "model": "jev-1.13", "apiKeyEnv": "TYPESAFE_API_KEY"},
        ))
    if env.get("MU_JUDGE_OPENROUTER_API_KEY"):
        order.append((
            "jev-openrouter",
            {"type": "typesafe", "baseUrl": "https://openrouter.ai/api/v1/systemone",
             "model": "jev-1.13", "apiKeyEnv": "MU_JUDGE_OPENROUTER_API_KEY"},
        ))
    if env.get("AI_GATEWAY_API_KEY"):
        order.append((
            "jev-gateway",
            {"type": "typesafe", "baseUrl": "https://ai-gateway.vercel.sh/v4/ai",
             "model": "jev-1.13", "apiKeyEnv": "AI_GATEWAY_API_KEY"},
        ))
    if env.get("OPENCODE_API_KEY"):
        order.append((
            "jev-opencode",
            {"type": "typesafe", "baseUrl": "https://opencode.ai/zen/v1",
             "model": "jev-1.13", "apiKeyEnv": "OPENCODE_API_KEY"},
        ))
    if env.get("CLOUDFLARE_API_KEY") and env.get("CLOUDFLARE_ACCOUNT_ID"):
        acct = env["CLOUDFLARE_ACCOUNT_ID"]
        order.append((
            "jev-cloudflare",
            {"type": "http",
             "baseUrl": f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/@typesafe/jev-1.13",
             "model": "", "apiKeyEnv": "CLOUDFLARE_API_KEY"},
        ))
    if not order:
        # mu: "With no key at all, Jev 1.13 on OpenCode Zen answers."
        order.append((
            "jev-opencode-free",
            {"type": "typesafe", "baseUrl": "https://opencode.ai/zen/v1",
             "model": "jev-1.13-free", "apiKeyEnv": "", "freeTier": True},
        ))
    return [_normalize(n, s) for n, s in order]


def judges_for_point(
    cfg: dict[str, Any], point: str, agent_config: Any = None
) -> list[dict[str, Any]]:
    routes = cfg.get("routes") or {}
    names = routes.get(point)
    if not isinstance(names, list) or not names:
        names = cfg.get("tiers") or []
    specs: list[dict[str, Any]] = []
    for name in names:
        specs.extend(resolve_judge_name(cfg, str(name), agent_config))
    return specs
