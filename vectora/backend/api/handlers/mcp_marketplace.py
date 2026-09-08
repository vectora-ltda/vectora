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
import json
import logging
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.services import registry_client
from backend.services.importers import preview_mcp_config

if TYPE_CHECKING:
    from backend.workspace.plugins import McpServer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-marketplace"])


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


class MCPConnector(BaseModel):
    id: str
    name: str
    description: str
    install_cmd: str = ""
    env_vars: list[str] = []
    homepage: str = ""
    category: str = "general"
    vectora_verified: bool = False
    icon_url: str | None = None


class InstallRequest(BaseModel):
    mcp_id: str
    workspace_id: str | None = None
    scope: Literal["user", "workspace", "project", "runtime"] = "user"
    target: str | None = None


class UninstallRequest(BaseModel):
    mcp_id: str
    workspace_id: str | None = None
    scope: Literal["user", "workspace", "project", "runtime"] = "user"
    target: str | None = None


class ImportPreviewRequest(BaseModel):
    payload: dict


# ---------------------------------------------------------------------------
# Registry embutido — subconjunto curado de MCPs populares
# ---------------------------------------------------------------------------

_REGISTRY: list[MCPConnector] = [
    MCPConnector(
        id="brave-search",
        name="Brave Search",
        description="Pesquisa web via Brave Search API com resultados sem rastreamento.",
        install_cmd="npx -y @modelcontextprotocol/server-brave-search",
        env_vars=["BRAVE_API_KEY"],
        homepage="https://github.com/modelcontextprotocol/servers",
        category="web",
        vectora_verified=True,
    ),
    MCPConnector(
        id="filesystem",
        name="Filesystem",
        description="Acesso seguro ao filesystem local com controle de diretórios permitidos.",
        install_cmd="npx -y @modelcontextprotocol/server-filesystem",
        env_vars=[],
        homepage="https://github.com/modelcontextprotocol/servers",
        category="filesystem",
        vectora_verified=True,
    ),
    MCPConnector(
        id="github",
        name="GitHub",
        description="Integração com GitHub: PRs, issues, código, actions e mais.",
        install_cmd="npx -y @modelcontextprotocol/server-github",
        env_vars=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        homepage="https://github.com/modelcontextprotocol/servers",
        category="devtools",
        vectora_verified=True,
    ),
    MCPConnector(
        id="postgres",
        name="PostgreSQL",
        description="Consultas read-only em banco PostgreSQL.",
        install_cmd="npx -y @modelcontextprotocol/server-postgres",
        env_vars=["POSTGRES_CONNECTION_STRING"],
        homepage="https://github.com/modelcontextprotocol/servers",
        category="database",
        vectora_verified=True,
    ),
    MCPConnector(
        id="slack",
        name="Slack",
        description="Leitura e envio de mensagens no Slack via Bot Token.",
        install_cmd="npx -y @modelcontextprotocol/server-slack",
        env_vars=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        homepage="https://github.com/modelcontextprotocol/servers",
        category="communication",
        vectora_verified=True,
    ),
    MCPConnector(
        id="sequential-thinking",
        name="Sequential Thinking",
        description="Raciocínio passo-a-passo estruturado antes de agir.",
        install_cmd="npx -y @modelcontextprotocol/server-sequential-thinking",
        env_vars=[],
        homepage="https://github.com/modelcontextprotocol/servers",
        category="reasoning",
        vectora_verified=True,
    ),
]


# ---------------------------------------------------------------------------
# Lógica de install/uninstall — grava no MESMO store por-usuário que o agente
# lê (backend.workspace.plugins), não num mcp.json paralelo. Instalar um
# conector faz suas tools aparecerem no toolset via get_user_mcp_tools.
# ---------------------------------------------------------------------------


def _connector_to_server(connector: MCPConnector) -> McpServer:
    """Converte um conector do registry num McpServer stdio do store funcional."""
    from backend.workspace.plugins import McpServer

    parts = connector.install_cmd.split() if connector.install_cmd else ["npx"]
    return McpServer(
        name=connector.id,
        transport="stdio",
        command=parts[0],
        args=parts[1:],
    )


# ---------------------------------------------------------------------------
# Handlers (também usados como funções nos testes)
# ---------------------------------------------------------------------------


def _remote_entry_to_connector(entry: dict) -> MCPConnector | None:
    try:
        env_vars = entry.get("env_vars", [])
        if isinstance(env_vars, str):
            env_vars = json.loads(env_vars)
        return MCPConnector(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            description=entry.get("description", ""),
            install_cmd=entry.get("install_cmd", ""),
            env_vars=list(env_vars) if env_vars else [],
            homepage=entry.get("homepage") or "",
            category=entry.get("category", "general"),
            vectora_verified=bool(entry.get("vectora_verified")),
            icon_url=entry.get("icon_url") or None,
        )
    except Exception:
        logger.warning("mcp_marketplace: entrada remota malformada ignorada: %r", entry)
        return None


async def list_registry() -> list[MCPConnector]:
    """Mescla três fontes, nessa ordem de prioridade (id repetido: a
    primeira que aparece vence):

    1. Registry próprio da Vectora (D1, `services/src/registry/routes.ts`)
       — curado, entradas com `vectora_verified`.
    2. Registry oficial de MCP (`registry.modelcontextprotocol.io`,
       mantido pela comunidade/Anthropic) — catálogo amplo, só servers com
       pacote npm/stdio (único transporte que `_connector_to_server`
       suporta hoje).
    3. Fallback hardcoded local (`_REGISTRY`) — só entra se nem 1 nem 2
       responderem (sem rede/cache), nunca deixa a lista vazia.

    As duas primeiras fontes são buscadas em paralelo (cada uma já é
    cache-first dentro de `registry_client`) — nenhuma depende da outra.
    A lista final ordena verificados primeiro (curados, `vectora_verified`),
    resto em ordem alfabética por nome — nunca inventa métrica de
    popularidade que a fonte não tem.
    """
    remote, enterprise, official = await asyncio.gather(
        registry_client.fetch_catalog("mcp"),
        registry_client.fetch_enterprise_catalog("mcp"),
        registry_client.fetch_official_mcp_registry(),
    )
    connectors: dict[str, MCPConnector] = {}
    for entry in remote:
        connector = _remote_entry_to_connector(entry)
        if connector is not None:
            connectors[connector.id] = connector
    for entry in enterprise:
        connector = _remote_entry_to_connector(entry)
        if connector is not None:
            connectors.setdefault(connector.id, connector)
    for entry in official:
        connector = _remote_entry_to_connector(entry)
        if connector is not None:
            connectors.setdefault(connector.id, connector)
    for connector in _REGISTRY:
        connectors.setdefault(connector.id, connector)
    return sorted(
        connectors.values(), key=lambda c: (not c.vectora_verified, c.name.lower())
    )


async def install_mcp(req: InstallRequest, user_id: str = "local") -> dict:
    connector = next((c for c in await list_registry() if c.id == req.mcp_id), None)
    if connector is None:
        return {
            "status": "error",
            "error": f"conector '{req.mcp_id}' não encontrado no registry",
        }
    try:
        from backend.workspace import plugins

        scope = (
            req.scope
            if req.scope != "user"
            else ("workspace" if req.workspace_id else "user")
        )
        target = req.target or req.workspace_id
        plugins.add_server(user_id, _connector_to_server(connector), scope, target)
        logger.info("mcp_marketplace: instalado %s (user=%s)", connector.id, user_id)
        return {"status": "installed", "mcp_id": connector.id}
    except Exception as exc:
        logger.exception("mcp_marketplace: falha ao instalar %s", req.mcp_id)
        return {"status": "error", "error": str(exc)}


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
        return {"status": "error", "error": str(exc)}


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
        raise ValueError("workspace_id obrigatório para escopo não-usuário")
    ws = require_workspace_access(ws_id, request)
    if ws is None:
        raise ValueError("workspace não encontrado")
    if scope == "project":
        return str(ws.cwd)
    if scope == "workspace":
        return ws_id
    return target or getattr(request.state, "thread_id", None) or ws_id


def _filter_registry(
    connectors: list[MCPConnector], *, q: str | None, category: str | None
) -> list[MCPConnector]:
    """Filtro em memória sobre o catálogo já mesclado — as 3 fontes de
    `list_registry` (D1, registry oficial, fallback local) não têm um
    `?q=`/`?category=` comum pra repassar adiante, então o filtro roda
    aqui, depois do merge, não em cada fonte."""
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
    return await install_mcp(req, _req_user_id(request))


@router.post("/uninstall")
async def post_uninstall(req: UninstallRequest, request: Request) -> dict:
    req.target = _authorized_target(request, req.scope, req.target, req.workspace_id)
    return await uninstall_mcp(req, _req_user_id(request))


@router.post("/import/preview")
async def preview_import(req: ImportPreviewRequest) -> dict:
    """Return a non-executing import preview; secrets and commands are data only."""
    return preview_mcp_config(req.payload)
