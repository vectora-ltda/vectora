"""MCP Marketplace.

Endpoints REST para descoberta e gerenciamento de MCP servers de terceiros.
O registry embutido lista conectores curados; instalação grava em
~/.vectora/mcp.json que o servidor MCP lê no próximo boot.

Routes (montadas em server.py):
    GET  /mcp/registry  — lista conectores disponíveis
    POST /mcp/install   — adiciona conector ao mcp.json
    POST /mcp/uninstall — remove conector do mcp.json
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.context_graph.security import validate_url
from backend.services import extension_trust, mcp_policy, registry_client
from backend.services.importers import preview_mcp_config

if TYPE_CHECKING:
    from backend.workspace.plugins import McpServer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-marketplace"])


async def _audit_policy(request: Request, action: str, success: bool) -> None:
    try:
        from backend.rbac.auth import get_db_for_audit, write_audit

        user_id = _req_user_id(request)
        db = await get_db_for_audit()
        await write_audit(
            db,
            user_id,
            f"mcp_policy.{action}",
            success=success,
            metadata={"workspace_id": request.query_params.get("workspace_id", "")},
            target_type="mcp_policy",
        )
    except Exception as exc:
        logger.debug("mcp_policy: auditoria indisponível", extra={"action": action})


async def _audit_mcp_decision(
    request: Request,
    *,
    action: str,
    mcp_id: str,
    scope: str,
    allowed: bool,
) -> None:
    """Registra apenas o principal, ação, escopo e ID estável do MCP."""
    try:
        from backend.rbac.auth import get_db_for_audit, write_audit

        db = await get_db_for_audit()
        await write_audit(
            db,
            _req_user_id(request),
            f"mcp.{action}",
            success=allowed,
            metadata={"mcp_id": mcp_id, "scope": scope},
            target_type="mcp",
        )
    except Exception:
        logger.debug("mcp: auditoria de decisão indisponível", extra={"action": action})


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


class MCPConnector(BaseModel):
    """Public marketplace contract for a validated MCP catalog connector.

    The catalog carries installation transport and command metadata, optional
    remote-server URL, publisher attribution, ranking counters, and trust
    state.  It is the only shape exposed to the API and frontend.
    """

    id: str
    name: str
    description: str
    install_cmd: str = ""
    env_vars: list[str] = []
    homepage: str = ""
    category: str = "general"
    vectora_verified: bool = False
    icon_url: str | None = None
    publisher: str | None = None
    publisher_url: str | None = None
    stars_count: int = 0
    downloads_count: int = 0
    transport: str = "stdio"
    runtime_hint: str | None = None
    package_identifier: str | None = None
    server_url: str | None = None
    trust_state: extension_trust.TrustState = "unsigned"
    trust_reason: str = "verification_unavailable"


class InstallRequest(BaseModel):
    mcp_id: str
    workspace_id: str | None = None
    scope: Literal["user", "workspace", "project", "runtime"] = "user"
    target: str | None = None
    confirm_unverified: bool = False


class UninstallRequest(BaseModel):
    mcp_id: str
    workspace_id: str | None = None
    scope: Literal["user", "workspace", "project", "runtime"] = "user"
    target: str | None = None


class ImportPreviewRequest(BaseModel):
    payload: dict


class PolicyRequest(BaseModel):
    scope: Literal["instance", "workspace"]
    workspace_id: str | None = None
    allowlist: list[str] = []


# ---------------------------------------------------------------------------
# Lógica de install/uninstall — grava no MESMO store por-usuário que o agente
# lê (backend.workspace.plugins), não num mcp.json paralelo. Instalar um
# conector faz suas tools aparecerem no toolset via get_user_mcp_tools.
# ---------------------------------------------------------------------------


def _connector_to_server(connector: MCPConnector) -> McpServer:
    """Converte a opção de instalação tipada do catálogo em um servidor."""
    from backend.workspace.plugins import McpServer

    if connector.transport in {"http", "sse"} and connector.server_url:
        validate_url(connector.server_url)
        command, args = "", []
    else:
        parts = connector.install_cmd.split() if connector.install_cmd else ["npx"]
        command, args = parts[0], parts[1:]
    trust = extension_trust.TrustRecord(
        source=f"marketplace:{connector.id}",
        state=connector.trust_state,
        reason=connector.trust_reason,
    )
    return McpServer(
        name=connector.id,
        transport=connector.transport,
        command=command,
        args=args,
        url=connector.server_url or "",
        env_vars=connector.env_vars,
        trust=trust,
    )


# ---------------------------------------------------------------------------
# Handlers (também usados como funções nos testes)
# ---------------------------------------------------------------------------


def _remote_entry_to_connector(
    entry: registry_client.McpCatalogEntry,
) -> MCPConnector:
    """Map the validated registry contract to the public API model."""
    return MCPConnector.model_validate(entry.model_dump())


async def list_registry() -> list[MCPConnector]:
    """Lista exclusivamente o catálogo MCP canônico já agregado pelo registry."""
    remote = await registry_client.fetch_catalog("mcp")
    connectors: dict[str, MCPConnector] = {}
    for entry in registry_client.validate_mcp_catalog_entries(remote):
        if entry.catalog_source != "official":
            continue
        connector = _remote_entry_to_connector(entry)
        connectors[connector.id] = connector
    return sorted(
        connectors.values(),
        key=lambda c: (-c.stars_count, -c.downloads_count, c.name.lower()),
    )


async def install_mcp(
    req: InstallRequest, user_id: str = "local", request: Request | None = None
) -> dict:
    connector = next((c for c in await list_registry() if c.id == req.mcp_id), None)
    if connector is None:
        return {
            "status": "error",
            "error": f"conector '{req.mcp_id}' não encontrado no registry",
        }
    try:
        from backend.workspace import plugins

        decision = mcp_policy.evaluate(connector.id, req.workspace_id)
        if not decision.allowed:
            if request is not None:
                await _audit_mcp_decision(
                    request,
                    action="install",
                    mcp_id=connector.id,
                    scope=req.scope,
                    allowed=False,
                )
            return {
                "status": "error",
                "code": (
                    "policy_unavailable"
                    if decision.code == "policy_unavailable"
                    else "policy_blocked"
                ),
                "error": "servidor bloqueado pela política",
            }
        server = await asyncio.to_thread(_connector_to_server, connector)
        # Catalog metadata only describes required names. Values cross the
        # boundary when the user explicitly saved them for this account.
        from backend.rbac.auth import get_env_overrides

        overrides = await get_env_overrides(user_id)
        explicit_env = {
            key: overrides[key] for key in connector.env_vars if overrides.get(key)
        }
        server = server.model_copy(update={"env": explicit_env})
        try:
            extension_trust.validate_record(
                server.trust, confirmed=req.confirm_unverified
            )
        except PermissionError as exc:
            return {
                "status": "error",
                "code": "confirmation_required",
                "trust_state": server.trust.state,
                "trust_reason": server.trust.reason,
                "error": str(exc),
            }

        scope = (
            req.scope
            if req.scope != "user"
            else ("workspace" if req.workspace_id else "user")
        )
        target = req.target or req.workspace_id
        plugins.add_server(user_id, server, scope, target)
        logger.info("mcp_marketplace: instalado %s (user=%s)", connector.id, user_id)
        return {"status": "installed", "mcp_id": connector.id}
    except Exception as exc:
        logger.exception("mcp_marketplace: falha ao instalar %s", req.mcp_id)
        return {
            "status": "error",
            "code": "install_failed",
            "error": str(exc),
        }


async def uninstall_mcp(req: UninstallRequest, user_id: str = "local") -> dict:
    try:
        from backend.workspace import plugins

        scope = (
            req.scope
            if req.scope != "user"
            else ("workspace" if req.workspace_id else "user")
        )
        target = req.target or req.workspace_id
        removed = plugins.remove_server(user_id, req.mcp_id, scope, target)
        if removed:
            logger.info(
                "mcp_marketplace: desinstalado %s (user=%s)", req.mcp_id, user_id
            )
            return {"status": "removed", "mcp_id": req.mcp_id}
        return {"status": "not_found", "mcp_id": req.mcp_id}
    except Exception as exc:
        logger.exception("mcp_marketplace: falha ao desinstalar %s", req.mcp_id)
        return {
            "status": "error",
            "code": "uninstall_failed",
            "error": str(exc),
        }


def _require_policy_admin(request: Request) -> str:
    from backend.rbac.permissions import require_min_role

    user = getattr(request.state, "user", None)
    require_min_role(user, "admin")
    return str(getattr(user, "id", "local"))


@router.get("/policy")
async def get_policy(request: Request) -> dict:
    _require_policy_admin(request)
    return {
        "version": mcp_policy.policy_version(),
        "rules": [rule.model_dump() for rule in mcp_policy.list_rules()],
    }


@router.put("/policy")
async def put_policy(body: PolicyRequest, request: Request) -> dict:
    user_id = _require_policy_admin(request)
    if body.scope == "workspace":
        from backend.api.handlers.workspaces import require_workspace_access

        if not body.workspace_id:
            raise ValueError("workspace_id obrigatório")
        require_workspace_access(body.workspace_id, request)
    try:
        rule = mcp_policy.set_rule(
            body.scope,
            body.allowlist,
            workspace_id=body.workspace_id,
            updated_by=user_id,
        )
    except ValueError as exc:
        await _audit_policy(request, "update", False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit_policy(request, "update", True)
    return {
        "status": "updated",
        "version": mcp_policy.policy_version(),
        "rule": rule.model_dump(),
    }


@router.delete("/policy")
async def delete_policy(body: PolicyRequest, request: Request) -> dict:
    _require_policy_admin(request)
    removed = mcp_policy.remove_rule(body.scope, workspace_id=body.workspace_id)
    await _audit_policy(request, "remove", removed)
    return {
        "status": "removed" if removed else "not_found",
        "version": mcp_policy.policy_version(),
    }


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------


def _req_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return str(user.id) if user is not None else "local"


def _authorized_target(
    request: Request, scope: str, target: str | None, workspace_id: str | None
) -> str | None:
    """Resolve scoped targets through the authorized workspace registry."""
    if scope == "user":
        return None
    from backend.api.handlers.workspaces import require_workspace_access

    ws_id = workspace_id or target
    if not ws_id:
        raise HTTPException(
            status_code=400, detail="workspace_id obrigatório para escopo não-usuário"
        )
    ws = require_workspace_access(ws_id, request)
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace não encontrado")
    if scope == "project":
        return str(ws.cwd)
    if scope == "workspace":
        return ws_id
    return target or getattr(request.state, "thread_id", None) or ws_id


def _filter_registry(
    connectors: list[MCPConnector], *, q: str | None, category: str | None
) -> list[MCPConnector]:
    """Filtra em memória o catálogo já carregado."""
    result = connectors
    if q:
        needle = q.strip().lower()
        if needle:
            result = [
                c
                for c in result
                if needle in c.name.lower() or needle in c.description.lower()
            ]
    if category:
        result = [c for c in result if c.category == category]
    return result


@router.get("/registry", response_model=list[MCPConnector])
async def get_registry(
    q: str | None = None, category: str | None = None
) -> list[MCPConnector]:
    return _filter_registry(await list_registry(), q=q, category=category)


@router.post("/install")
async def post_install(req: InstallRequest, request: Request) -> dict:
    req.target = _authorized_target(request, req.scope, req.target, req.workspace_id)
    return await install_mcp(req, _req_user_id(request), request)


@router.post("/uninstall")
async def post_uninstall(req: UninstallRequest, request: Request) -> dict:
    req.target = _authorized_target(request, req.scope, req.target, req.workspace_id)
    return await uninstall_mcp(req, _req_user_id(request))


@router.post("/import/preview")
async def preview_import(req: ImportPreviewRequest) -> dict:
    """Return a non-executing import preview; secrets and commands are data only."""
    return preview_mcp_config(req.payload)
