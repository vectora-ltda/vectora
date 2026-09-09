"""Handler de plugins MCP — gestão de servidores MCP por usuário.

Endpoints (todos exigem autenticação via middleware):
    GET    /plugins                 — lista servidores MCP do usuário
    POST   /plugins                 — adiciona/atualiza um servidor
    DELETE /plugins/{name}          — remove um servidor
    POST   /plugins/{name}/verify   — health-check (conecta e lista tools)

O user_id vem de ``request.state.user`` (injetado pelo AuthMiddleware); em modo
CLI/root local, usa ``"local"``. Cada usuário só enxerga os próprios servidores.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from backend.api.handlers.workspaces import require_workspace_access
from backend.services import extension_trust, mcp_policy
from backend.workspace.plugins import (
    McpServer,
    add_server,
    health_check,
    list_servers,
    remove_server,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return "local"


def _authorized_workspace(request: Request) -> str | None:
    """Validate a client-supplied workspace before using it in policy checks."""
    workspace_id = request.query_params.get("workspace_id")
    if workspace_id:
        require_workspace_access(workspace_id, request)
    return workspace_id


@router.get("")
async def list_plugins(request: Request) -> dict:
    """Lista os servidores MCP do usuário autenticado."""
    workspace_id = _authorized_workspace(request)
    servers = [
        server
        for server in list_servers(_user_id(request))
        if mcp_policy.evaluate(server.name, workspace_id).allowed
    ]
    return {"servers": [s.model_dump() for s in servers], "total": len(servers)}


@router.post("")
async def add_plugin(request: Request, body: McpServer) -> dict:
    """Adiciona ou atualiza (por nome) um servidor MCP do usuário."""
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nome é obrigatório.")
    if body.transport not in {"stdio", "sse", "http"}:
        raise HTTPException(
            status_code=400, detail="transport deve ser stdio, sse ou http."
        )
    if body.transport == "stdio" and not body.command.strip():
        raise HTTPException(status_code=400, detail="stdio exige 'command'.")
    if body.transport in {"sse", "http"} and not body.url.strip():
        raise HTTPException(status_code=400, detail="sse/http exige 'url'.")

    workspace_id = _authorized_workspace(request)
    if not mcp_policy.evaluate(body.name, workspace_id).allowed:
        raise HTTPException(status_code=403, detail="Servidor bloqueado pela política.")
    # Trust is derived from installed material. Never accept publisher, digest,
    # signature, or verification fields supplied by a manual client payload.
    source = body.url if body.transport in {"sse", "http"} else body.command
    body = body.model_copy(
        update={
            "trust": extension_trust.unsigned_record(source),
            "trust_confirmed": body.trust_confirmed,
        }
    )
    try:
        extension_trust.validate_record(body.trust, confirmed=body.trust_confirmed)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    saved = add_server(_user_id(request), body)
    return {"status": "ok", "server": saved.model_dump()}


@router.delete("/{name}")
async def delete_plugin(request: Request, name: str) -> dict:
    """Remove um servidor MCP do usuário."""
    removed = remove_server(_user_id(request), name)
    if not removed:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    return {"status": "removed", "name": name}


@router.post("/{name}/verify")
async def verify_plugin(request: Request, name: str) -> dict:
    """Health-check: conecta ao servidor e lista suas tools."""
    workspace_id = _authorized_workspace(request)
    server = next((s for s in list_servers(_user_id(request)) if s.name == name), None)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    if not mcp_policy.evaluate(server.name, workspace_id).allowed:
        raise HTTPException(status_code=403, detail="Servidor bloqueado pela política.")
    try:
        extension_trust.validate_record(server.trust, confirmed=server.trust_confirmed)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await health_check(server)
