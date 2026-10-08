"""Inherit the session's model as judge — mu's ``mu setup --judge model``.

mu lets the chat model of the running session answer the kernel's bounded
questions ("it needs nothing beyond the model you already use"). QwenPaw
keeps exactly that information per agent: ``agent.json -> active_model
{provider_id, model}``, and the provider manager resolves a provider id to
an OpenAI-compatible ``base_url`` plus its stored credential.

Resolution chain (first hit wins):

1. the requesting agent's ``AgentProfileConfig.active_model`` (the
   middleware factory receives it at agent-build time);
2. the host-level active slot (``ProviderManager.get_active_model()``);
3. the default workspace's ``agent.json`` (tools have no agent context).

The credential is read in memory and handed to the llm transport as a
direct ``apiKey``; it is never written to jev.json, the ledger or logs.
Only OpenAI-compatible providers can speak the llm transport; anything
else resolves to None and the cascade records ``unresolved:session``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("jev_kernel.session_model")

OPENAI_COMPATIBLE_CLASSES = (
    "OpenAIProvider",
    "OpenAIChatProvider",
    "OpenRouterProvider",
    "DashScopeProvider",
    "ModelScopeProvider",
    "OllamaProvider",
    "LMStudioProvider",
)


def _slot_of(provider_id: Any, model: Any) -> dict[str, str] | None:
    pid = str(provider_id or "").strip()
    name = str(model or "").strip()
    if pid and name:
        return {"provider_id": pid, "model": name}
    return None


def slot_from_agent_config(agent_config: Any) -> dict[str, str] | None:
    slot = getattr(agent_config, "active_model", None)
    if slot is None:
        return None
    if isinstance(slot, dict):
        return _slot_of(slot.get("provider_id"), slot.get("model"))
    return _slot_of(getattr(slot, "provider_id", ""), getattr(slot, "model", ""))


def slot_from_provider_manager() -> dict[str, str] | None:
    try:
        from qwenpaw.providers.provider_manager import ProviderManager

        slot = ProviderManager.get_instance().get_active_model()
    except Exception:  # noqa: BLE001 - no QwenPaw runtime (tests) or no slot
        return None
    if slot is None:
        return None
    return _slot_of(getattr(slot, "provider_id", ""), getattr(slot, "model", ""))


def slot_from_default_agent_json() -> dict[str, str] | None:
    path = Path.home() / ".qwenpaw" / "workspaces" / "default" / "agent.json"
    try:
        active = (json.loads(path.read_text(encoding="utf-8")) or {}).get("active_model") or {}
    except Exception:  # noqa: BLE001
        return None
    if isinstance(active, dict):
        return _slot_of(active.get("provider_id"), active.get("model"))
    return None


def resolve_slot(agent_config: Any = None) -> dict[str, str] | None:
    return (
        slot_from_agent_config(agent_config)
        or slot_from_provider_manager()
        or slot_from_default_agent_json()
    )


def session_spec(agent_config: Any = None) -> dict[str, Any] | None:
    """An llm judge spec reusing the session's provider, in memory only."""
    slot = resolve_slot(agent_config)
    if not slot:
        return None
    try:
        from qwenpaw.providers.provider_manager import ProviderManager

        provider = ProviderManager.get_instance().get_provider(slot["provider_id"])
    except Exception:  # noqa: BLE001
        logger.warning("jev-kernel: provider manager unavailable for session judge")
        return None
    if provider is None:
        return None
    base_url = str(getattr(provider, "base_url", "") or "")
    api_key = str(getattr(provider, "api_key", "") or "")
    if not base_url:
        return None
    if type(provider).__name__ not in OPENAI_COMPATIBLE_CLASSES:
        logger.info(
            "jev-kernel: session provider %s (%s) is not OpenAI-compatible; "
            "session judge unavailable",
            slot["provider_id"],
            type(provider).__name__,
        )
        return None
    if getattr(provider, "require_api_key", False) and not api_key:
        return None
    return {
        "name": f"session:{slot['provider_id']}/{slot['model']}",
        "type": "llm",
        "baseUrl": base_url,
        "model": slot["model"],
        "apiKeyEnv": "",
        "apiKey": api_key,
        # thinking models blow the judge latency budget (measured: 7.2 s
        # with thinking vs 2.0 s without on qwen3.8-max); mu's judge is a
        # fast System-One model, so the session judge mimics that profile.
        "extraBody": {"enable_thinking": False},
        "timeoutMs": 15000,
        "freeTier": False,
    }
