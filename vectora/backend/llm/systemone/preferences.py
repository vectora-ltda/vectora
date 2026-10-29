"""Trusted per-user Decision Model preferences copied into an execution context."""

from __future__ import annotations

from typing import Any, cast

from backend.llm.systemone.types import DecisionProviderType


def decision_model_preferences(ctx: object) -> dict[str, Any]:
    """Return the validated preferences attached by ``ctx_from_config``."""
    extra = getattr(ctx, "_extra", None)
    value = extra.get("decision_model_preferences") if isinstance(extra, dict) else None
    return value if isinstance(value, dict) else {}


def decision_model_enabled_for(ctx: object, surface: str) -> bool:
    """Check the durable master switch and a specific backend-enforced surface."""
    preferences = decision_model_preferences(ctx)
    surfaces = preferences.get("surfaces")
    return (
        preferences.get("enabled") is True
        and isinstance(surfaces, dict)
        and surfaces.get(surface) is True
    )


def selected_decision_model_provider(ctx: object) -> DecisionProviderType | None:
    """Return a supported provider id from the trusted context snapshot."""
    provider = decision_model_preferences(ctx).get("provider")
    if provider in {"clef-local", "laya-local", "clef-api", "jev-api"}:
        return cast("DecisionProviderType", provider)
    return None


def decision_model_surface_for_tool(ctx: object, tool_name: str) -> str:
    """Map execution provenance and tool capability to its independent toggle."""
    extra = getattr(ctx, "_extra", None)
    extra = extra if isinstance(extra, dict) else {}
    name = tool_name.lower()
    if extra.get("subagent_execution") is True:
        surface = "subagents"
    elif getattr(ctx, "background_task_id", ""):
        surface = "background"
    elif name == "call_mcp_tool" or name.startswith("mcp_"):
        surface = "mcp"
    elif name == "write_terminal" or name.startswith("terminal_"):
        surface = "terminal"
    elif name == "http_request" or name in {"web_crawl", "web_map", "fetch_url"}:
        surface = "network"
    elif name.startswith("browser") or name in {"web_search", "browser_search"}:
        surface = "browser"
    else:
        surface = "hitl"
    return surface
