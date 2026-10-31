"""Trusted per-user Decision Model preferences copied into an execution context."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, cast

from backend.llm.systemone.types import DecisionProviderType


def decision_model_preferences(ctx: object) -> dict[str, Any]:
    """Return the validated preferences attached by ``ctx_from_config``."""
    extra = getattr(ctx, "_extra", None)
    value = extra.get("decision_model_preferences") if isinstance(extra, dict) else None
    return value if isinstance(value, dict) else {}


def decision_model_enabled_for(ctx: object, surface: str | tuple[str, ...]) -> bool:
    """Require every applicable surface in the durable preference snapshot."""
    preferences = decision_model_preferences(ctx)
    surfaces = preferences.get("surfaces")
    required = (surface,) if isinstance(surface, str) else surface
    return bool(
        required
        and preferences.get("enabled") is True
        and isinstance(surfaces, dict)
        and all(surfaces.get(name) is True for name in required)
    )


def selected_decision_model_provider(ctx: object) -> DecisionProviderType | None:
    """Return a supported provider id from the trusted context snapshot."""
    provider = decision_model_preferences(ctx).get("provider")
    if provider in {"clef-local", "laya-local", "clef-api", "jev-api"}:
        return cast("DecisionProviderType", provider)
    return None


def decision_model_runtime_fingerprint(provider: str | None) -> str | None:
    """Fingerprint provider routing and credentials without persisting secrets."""
    if provider not in {"clef-local", "laya-local", "clef-api", "jev-api"}:
        return None

    from backend.settings import settings

    if provider == "clef-local":
        config = {
            "base_url": settings.systemone_local_base_url,
            "model": settings.systemone_local_model,
            "credential": os.getenv("LLAMACPP_API_KEY", ""),
        }
    elif provider == "laya-local":
        config = {
            "base_url": settings.systemone_laya_base_url,
            "model": settings.systemone_laya_model,
            "credential": settings.systemone_laya_api_key
            or os.getenv("LAYA_API_KEY", ""),
        }
    elif provider == "clef-api":
        config = {
            "base_url": settings.systemone_clef_api_base_url,
            "account_id": settings.systemone_clef_account_id
            or os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
            or os.getenv("CLEF_ACCOUNT_ID", ""),
            "model": settings.systemone_clef_api_model,
            "credential": settings.clef_api_key or os.getenv("CLEF_API_KEY", ""),
        }
    else:
        config = {
            "base_url": settings.systemone_jev_api_base_url,
            "model": settings.systemone_jev_api_model,
            "credential": settings.jev_api_key or os.getenv("JEV_API_KEY", ""),
        }

    serialized = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def decision_model_surfaces_for_tool(ctx: object, tool_name: str) -> tuple[str, ...]:
    """Require provenance, execution mode and capability flags for a tool call."""
    extra = getattr(ctx, "_extra", None)
    extra = extra if isinstance(extra, dict) else {}
    name = tool_name.lower()
    if name == "call_mcp_tool" or name.startswith("mcp_"):
        surface = "mcp"
    elif name == "write_terminal" or name.startswith("terminal_"):
        surface = "terminal"
    elif name == "http_request" or name in {"web_crawl", "web_map", "fetch_url"}:
        surface = "network"
    elif name.startswith("browser") or name in {"web_search", "browser_search"}:
        surface = "browser"
    elif name.startswith("kanban_"):
        surface = "kanban"
    else:
        surface = "hitl"
    provenance = []
    if extra.get("connector_execution") is True:
        provenance.append("connectors")
    if extra.get("subagent_execution") is True:
        provenance.append("subagents")
    if getattr(ctx, "background_task_id", ""):
        provenance.append("background")
    if getattr(ctx, "permission_mode", None) == "plan":
        return (*provenance, "plan_mode", *((surface,) if surface != "hitl" else ()))
    return (*provenance, surface)
