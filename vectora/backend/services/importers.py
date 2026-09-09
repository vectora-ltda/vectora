"""Safe, preview-only adapters for external MCP and skill configuration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _safe_preview_url(value: str) -> str:
    """Strip URL credentials, query parameters, and fragments from previews."""
    parts = urlsplit(value)
    if not parts.scheme and not parts.netloc:
        return value.split("?", 1)[0].split("#", 1)[0]
    try:
        hostname = parts.hostname or ""
        netloc = hostname
        if ":" in hostname and not hostname.startswith("["):
            netloc = f"[{hostname}]"
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
    except ValueError:
        netloc = ""
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def preview_mcp_config(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Parse common ``mcpServers`` JSON without executing or resolving paths."""
    valid: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return {"valid": [], "ignored": [{"reason": "mcpServers ausente"}]}
    for name, raw in servers.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            ignored.append({"name": str(name), "reason": "entrada inválida"})
            continue
        command = raw.get("command")
        url = raw.get("url")
        if not isinstance(command, str) and not isinstance(url, str):
            ignored.append({"name": name, "reason": "command/url ausente"})
            continue
        env = raw.get("env")
        env_vars = sorted(str(key) for key in env) if isinstance(env, dict) else []
        valid.append(
            {
                "name": name,
                "transport": "stdio" if isinstance(command, str) else "http",
                "command": command if isinstance(command, str) else "",
                "args": [
                    str(item) for item in raw.get("args", []) if isinstance(item, str)
                ],
                "url": _safe_preview_url(url) if isinstance(url, str) else "",
                "env_vars": env_vars,
            }
        )
    return {"valid": valid, "ignored": ignored}


def preview_skill_config(payload: Any) -> dict[str, list[dict[str, Any]]]:
    """Preview skill metadata while preserving only environment variable names."""
    entries = (
        payload
        if isinstance(payload, list)
        else payload.get("skills", [])
        if isinstance(payload, dict)
        else []
    )
    valid: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    for raw in entries:
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("source"):
            ignored.append({"reason": "name/source ausente"})
            continue
        valid.append(
            {
                "id": str(raw.get("id") or raw["name"])
                .strip()
                .lower()
                .replace(" ", "-"),
                "name": str(raw["name"]),
                "description": str(raw.get("description", "")),
                "source": _safe_preview_url(str(raw["source"])),
            }
        )
    return {"valid": valid, "ignored": ignored}
