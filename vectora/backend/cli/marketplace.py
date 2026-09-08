"""CLI para descoberta e ciclo de vida de MCPs e skills.

Este módulo é um adaptador fino sobre os mesmos serviços usados pelos
handlers REST. Ele não executa servidores MCP durante operações de consulta e
mantém um envelope JSON estável para automação.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from backend.api.handlers import mcp_marketplace
from backend.api.handlers import skills as skills_handler
from backend.services import license, registry_client
from backend.workspace import plugins
from backend.workspace.skills import (
    InstallSkillRequest,
    install_skill,
    list_skills,
    remove_skill,
    verify_skill,
)

SCHEMA_VERSION = "1"
SUCCESS = 0
OPERATION_ERROR = 1
USAGE_ERROR = 2


def _envelope(
    status: str, data: Any = None, error: str | None = None
) -> dict[str, Any]:
    """Create the versioned output contract shared by all subcommands."""
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "data": data,
    }
    if error is not None:
        result["error"] = error
    return result


def _public_skill(skill: Any) -> dict[str, Any]:
    """Remove filesystem and user details from CLI output."""
    if hasattr(skill, "model_dump"):
        value = skill.model_dump()
    elif isinstance(skill, dict):
        value = dict(skill)
    else:
        value = {"value": str(skill)}
    for key in ("path", "source", "installed_by"):
        value.pop(key, None)
    return value


def _public_server(server: Any) -> dict[str, Any]:
    value = server.model_dump() if hasattr(server, "model_dump") else dict(server)
    # Environment variable names are safe metadata; values are never stored here.
    return {
        key: value[key]
        for key in ("name", "transport", "command", "args", "url", "env_vars")
        if key in value
    }


def _emit(result: dict[str, Any], output: str) -> int:
    if output == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        if result["status"] == "error":
            print(f"Erro: {result.get('error', 'falha operacional')}", file=sys.stderr)
            return OPERATION_ERROR
        data = result.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    print(" | ".join(f"{key}: {value}" for key, value in item.items()))
                else:
                    print(item)
        elif isinstance(data, dict):
            for key, value in data.items():
                print(f"{key}: {value}")
        elif data is not None:
            print(data)
    return SUCCESS if result["status"] != "error" else OPERATION_ERROR


def _match(items: list[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    if not query:
        return items
    needle = query.strip().lower()
    return [
        item
        for item in items
        if needle in str(item.get("id", "")).lower()
        or needle in str(item.get("name", "")).lower()
        or needle in str(item.get("description", "")).lower()
    ]


async def _mcp(args: Any) -> dict[str, Any]:
    installed = [_public_server(server) for server in plugins.list_servers("local")]
    if args.action == "list":
        return _envelope("ok", installed)
    registry = [
        connector.model_dump() for connector in await mcp_marketplace.list_registry()
    ]
    if args.action == "search":
        return _envelope("ok", _match(registry, args.query))
    if args.action == "info":
        found = _match(registry, args.identifier)
        return _envelope(
            "ok", found[0] if found else None, None if found else "MCP não encontrado"
        )
    if args.action == "install":
        result = await mcp_marketplace.install_mcp(
            mcp_marketplace.InstallRequest(mcp_id=args.identifier), "local"
        )
        return _envelope(
            "ok" if result.get("status") == "installed" else "error",
            result,
            result.get("error"),
        )
    if args.action == "remove":
        result = await mcp_marketplace.uninstall_mcp(
            mcp_marketplace.UninstallRequest(mcp_id=args.identifier), "local"
        )
        return _envelope(
            "ok" if result.get("status") == "removed" else "error",
            result,
            "MCP não instalado"
            if result.get("status") == "not_found"
            else result.get("error"),
        )
    return _envelope("error", error="Comando MCP indisponível")


async def _skills(args: Any) -> dict[str, Any]:  # noqa: PLR0911
    if args.action == "list":
        return _envelope("ok", [_public_skill(skill) for skill in list_skills("local")])
    if args.action in {"search", "info"}:
        entries = await skills_handler.get_skills_catalog()
        catalog = entries.get("entries", [])
        if args.action == "info":
            catalog = [_public_skill(skill) for skill in list_skills("local")] + catalog
        found = _match(
            catalog, getattr(args, "query", None) or getattr(args, "identifier", None)
        )
        if args.action == "search":
            return _envelope("ok", found)
        return _envelope(
            "ok", found[0] if found else None, None if found else "Skill não encontrada"
        )
    if args.action == "install":
        try:
            skill = install_skill(
                "local", InstallSkillRequest(source=args.source).source
            )
        except ValueError as exc:
            return _envelope("error", error=str(exc))
        return _envelope("ok", _public_skill(skill))
    if args.action == "remove":
        removed = remove_skill("local", args.identifier)
        return _envelope(
            "ok",
            {"id": args.identifier, "removed": removed},
            None if removed else "Skill não encontrada",
        )
    if args.action == "validate":
        result = verify_skill("local", args.identifier)
        return _envelope(
            "ok" if result.get("status") != "error" else "error",
            result,
            result.get("error"),
        )
    if args.action == "publish":
        token = license._get_token()
        if not token:
            return _envelope("error", error="Autenticação vectora.company ausente")
        try:
            skill_id = await registry_client.publish_skill(
                args.name,
                args.description,
                args.source,
                category=args.category,
                tags=args.tags,
                session_token=token,
            )
        except registry_client.RegistryClientError as exc:
            return _envelope("error", error=str(exc))
        return _envelope("ok", {"skill_id": skill_id})
    return _envelope("error", error="Comando de skill indisponível")


def run_marketplace(args: Any) -> None:
    """Execute a marketplace command and terminate with its documented code."""
    try:
        result = asyncio.run(_mcp(args) if args.resource == "mcp" else _skills(args))
    except (OSError, ValueError, registry_client.RegistryClientError) as exc:
        result = _envelope("error", error=str(exc))
    raise SystemExit(_emit(result, args.output))
