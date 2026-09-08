"""Registry de servidores MCP por usuário.

Cada usuário tem sua própria lista de servidores MCP (plugins), persistida em
``~/.vectora/mcp/<user_id>.json``. As tools desses servidores entram no
toolset via ``VectoraMCPClient`` (``backend/tools/mcp.py``). O isolamento por
arquivo garante que um usuário não veja os plugins de outro.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, create_model

from backend.rbac import tool_policy
from backend.services import extension_trust, mcp_policy
from backend.tools.mcp import VectoraMCPClient
from backend.tools.registry import ToolExtras, ToolSpec

logger = logging.getLogger(__name__)

_HEALTH_TIMEOUT_S = 10

#: Contador de versão por usuário — bumpado a cada add/remove. Permite invalidar
#: caches downstream (tools MCP resolvidas, LLM bindado) sem reiniciar.
_versions: dict[str, int] = {}

#: Cache das tools MCP resolvidas: user_id -> (version, tools).
_mcp_tools_cache: dict[tuple, tuple[int, int, list[ToolSpec]]] = {}
McpScope = Literal["user", "workspace", "project", "runtime"]
_runtime_servers: dict[str, list[McpServer]] = {}


def _plugins_dir() -> Path:
    """Diretório base dos arquivos de plugins por usuário."""
    return Path.home() / ".vectora" / "mcp"


def tools_version(user_id: str) -> int:
    """Versão atual da configuração MCP do usuário (muda em add/remove)."""
    return _versions.get(user_id, 0)


def _bump_version(user_id: str) -> None:
    _versions[user_id] = _versions.get(user_id, 0) + 1
    # Avisa as demais réplicas — no modo lite é um no-op local.
    import json

    from backend.persistence.kv import publish_soon

    publish_soon(
        "vectora:tools",
        json.dumps({"user_id": user_id, "version": _versions[user_id]}),
    )


def apply_remote_version(user_id: str, version: int) -> None:
    """Aplica um bump de versão vindo de outra réplica (via cache_sync).

    Avança a versão local e descarta o cache de tools do usuário — o LLM
    bindado (``llm_tools._bound_cache``) é invalidado por consequência, pois
    sua chave inclui esta versão.
    """
    if version <= _versions.get(user_id, 0):
        return
    _versions[user_id] = version
    for cache_key in [key for key in _mcp_tools_cache if key[0] == user_id]:
        _mcp_tools_cache.pop(cache_key, None)


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------


class McpServer(BaseModel):
    name: str
    transport: str = "stdio"  # stdio | sse | http
    command: str = ""  # usado por stdio
    args: list[str] = []
    url: str = ""  # usado por sse/http
    env_vars: list[str] = []
    trust: extension_trust.TrustRecord = extension_trust.TrustRecord()
    trust_confirmed: bool = False
    """Nomes de variáveis de ambiente do processo Vectora repassadas ao
    subprocess stdio deste servidor, além do allowlist mínimo (PATH/HOME/
    etc.). Ignorado por sse/http."""


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------


def _safe_target(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_") or "local"


def _user_file(user_id: str) -> Path:
    return _plugins_dir() / f"{_safe_target(user_id)}.json"


def _scope_file(user_id: str, scope: McpScope, target: str | None) -> Path:
    if scope == "user":
        return _user_file(user_id)
    if not target:
        raise ValueError(f"target obrigatório para escopo {scope}")
    if scope == "workspace":
        return (
            _plugins_dir()
            / "workspaces"
            / _safe_target(user_id)
            / f"{_safe_target(target)}.json"
        )
    if scope == "project":
        raw_root = Path(target).expanduser()
        root = raw_root.resolve()
        if not raw_root.is_dir() or raw_root.is_symlink():
            raise ValueError("project deve ser um diretório real autorizado")
        vectora_dir = root / ".vectora"
        if vectora_dir.is_symlink():
            raise ValueError(".vectora não pode ser um symlink")
        return vectora_dir / "mcp.json"
    raise ValueError("runtime não possui persistência")


def list_servers(
    user_id: str,
    scope: McpScope = "user",
    target: str | None = None,
) -> list[McpServer]:
    """Lista servidores de um escopo sem iniciar nenhum servidor MCP."""
    if scope == "runtime":
        if not target:
            raise ValueError("target obrigatório para escopo runtime")
        return list(_runtime_servers.get(f"{user_id}:{target}", []))
    path = _scope_file(user_id, scope, target)
    if path.is_symlink():
        raise ValueError("arquivo MCP não pode ser um symlink")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("plugins: arquivo inválido para %s", user_id)
        return []
    out: list[McpServer] = []
    for item in data.get("servers", []):
        try:
            out.append(McpServer(**item))
        except Exception:
            logger.debug("plugins: servidor inválido ignorado: %s", item)
    return out


def _save(
    user_id: str,
    servers: list[McpServer],
    scope: McpScope = "user",
    target: str | None = None,
) -> None:
    path = _scope_file(user_id, scope, target)
    if path.exists() and path.is_symlink():
        raise ValueError("arquivo MCP não pode ser um symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"servers": [s.model_dump() for s in servers]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def add_server(
    user_id: str,
    server: McpServer,
    scope: McpScope = "user",
    target: str | None = None,
) -> McpServer:
    """Adiciona ou atualiza (por nome) um servidor MCP do usuário."""
    if scope == "runtime" and not target:
        raise ValueError("target obrigatório para escopo runtime")
    key = f"{user_id}:{target}" if scope == "runtime" else target or user_id
    if scope == "runtime":
        servers = [
            s for s in list_servers(user_id, scope, target) if s.name != server.name
        ]
        servers.append(server)
        _runtime_servers[key] = servers
        _bump_version(user_id)
        return server
    servers = [s for s in list_servers(user_id, scope, target) if s.name != server.name]
    servers.append(server)
    _save(user_id, servers, scope, target)
    _bump_version(user_id)
    return server


def remove_server(
    user_id: str,
    name: str,
    scope: McpScope = "user",
    target: str | None = None,
) -> bool:
    """Remove um servidor pelo nome. Retorna True se existia."""
    if scope == "runtime" and not target:
        raise ValueError("target obrigatório para escopo runtime")
    key = f"{user_id}:{target}" if scope == "runtime" else target or user_id
    servers = list_servers(user_id, scope, target)
    remaining = [s for s in servers if s.name != name]
    if len(remaining) == len(servers):
        return False
    if scope == "runtime":
        _runtime_servers[key] = remaining
    else:
        _save(user_id, remaining, scope, target)
    _bump_version(user_id)
    return True


# ---------------------------------------------------------------------------
# Conexão / health-check
# ---------------------------------------------------------------------------


def build_connection(server: McpServer) -> dict:
    """Monta o dict de conexão no formato aceito por
    ``VectoraMCPClient.connect`` (``backend/tools/mcp.py``)."""
    if server.transport == "stdio":
        return {
            "transport": "stdio",
            "command": server.command,
            "args": list(server.args),
            "env_vars": list(server.env_vars),
        }
    if server.transport == "sse":
        return {"transport": "sse", "url": server.url}
    # "http" → streamable_http moderno
    return {"transport": "streamable_http", "url": server.url}


async def health_check(server: McpServer) -> dict:
    """Tenta conectar ao servidor e listar suas tools.

    Retorna ``{ok, tools, error}``. Nunca lança — falhas viram ``ok=False``.
    Conecta em modo ``strict``: a primeira falha (servidor não sobe, timeout,
    etc.) propaga pro ``except`` em vez de virar silenciosamente "0 tools".
    """
    client = VectoraMCPClient()
    try:
        connection = build_connection(server)
        if server.trust.state in {"unsigned", "verification_unavailable"}:
            connection["require_sandbox"] = True
        async with asyncio.timeout(_HEALTH_TIMEOUT_S):
            await client.connect({server.name: connection}, strict=True)
        return {"ok": True, "tools": sorted(client.tools()), "error": ""}
    except Exception as exc:
        return {"ok": False, "tools": [], "error": str(exc)}
    finally:
        await client.aclose()


_JSON_SCHEMA_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _args_model_from_input_schema(
    tool_name: str, input_schema: dict | None
) -> type[BaseModel]:
    """Constrói um ``BaseModel`` dinâmico a partir do JSON Schema que um
    servidor MCP publica em ``Tool.input_schema`` — mapeamento raso (tipos
    primitivos + array/object), o suficiente pro schema que o LLM recebe
    pra chamar a tool remota."""
    properties: dict[str, Any] = (input_schema or {}).get("properties", {}) or {}
    required = set((input_schema or {}).get("required", []) or [])
    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _JSON_SCHEMA_TYPES.get((prop_schema or {}).get("type", ""), Any)
        if prop_name in required:
            fields[prop_name] = (py_type, ...)
        else:
            fields[prop_name] = (py_type | None, (prop_schema or {}).get("default"))
    return create_model(f"{tool_name}Args", **fields)


def _remote_tool_spec(
    server_name: str,
    connection: dict,
    mcp_tool: Any,
    user_id: str,
    workspace_id: str = "",
) -> ToolSpec:
    """Empacota uma ``mcp.types.Tool`` remota como ``ToolSpec`` nativa —
    cada invocação abre uma conexão nova, isolada, só com o servidor dono da
    tool (nenhum estado de sessão é mantido entre chamadas)."""
    tool_name = mcp_tool.name

    async def _handler(**kwargs: Any) -> str:
        decision = mcp_policy.evaluate(server_name, workspace_id or None)
        if not decision.allowed:
            return "Erro: servidor MCP bloqueado pela política."
        if not tool_policy.is_allowed(user_id, tool_name):
            return f"Erro: tool MCP '{tool_name}' desabilitada."
        client = VectoraMCPClient()
        try:
            async with asyncio.timeout(_HEALTH_TIMEOUT_S):
                await client.connect({server_name: connection}, strict=True)
                return await client.call_tool(tool_name, kwargs)
        except TimeoutError:
            return f"Erro: tool MCP '{tool_name}' excedeu {_HEALTH_TIMEOUT_S}s."
        except Exception as exc:
            logger.exception(
                "plugins: falha ao invocar tool MCP remota", extra={"tool": tool_name}
            )
            return f"Erro ao invocar tool MCP '{tool_name}': {exc}"
        finally:
            await client.aclose()

    _handler.__name__ = tool_name

    return ToolSpec(
        name=tool_name,
        description=mcp_tool.description or "",
        args_model=_args_model_from_input_schema(tool_name, mcp_tool.input_schema),
        handler=_handler,
        extras=ToolExtras(render_hint="json", category="mcp", icon="share-2"),
        needs_ctx=False,
    )


async def get_user_mcp_tools(
    user_id: str,
    names: set[str] | frozenset[str] | None = None,
    workspace_id: str = "",
    project_root: str | None = None,
    runtime_id: str | None = None,
) -> list[ToolSpec]:
    """Carrega as tools (``ToolSpec`` nativa) dos servidores MCP do usuário.

    Cacheado por ``(user_id, version)`` — só reconecta quando o usuário muda
    seus servidores. Sem servidores configurados → lista vazia. Falha de um
    servidor degrada para os que responderam (``VectoraMCPClient.connect``
    é tolerante por padrão); uma exceção ao listar todas ainda vira lista
    vazia + log, nunca propaga.
    """
    version = tools_version(user_id)
    policy_version = mcp_policy.policy_version()
    requested = frozenset(names) if names is not None else None
    cache_key = (user_id, requested, workspace_id, project_root, runtime_id)
    cached = _mcp_tools_cache.get(cache_key)
    if cached is not None and cached[0] == version and cached[1] == policy_version:
        return cached[2]

    # Runtime > project > workspace > user. A requested selection still narrows
    # the aggregate before any connection is opened.
    scoped_servers: list[McpServer] = []
    scopes: list[tuple[McpScope, str | None]] = [("user", None)]
    if workspace_id:
        scopes.append(("workspace", workspace_id))
    if project_root:
        scopes.append(("project", project_root))
    if runtime_id:
        scopes.append(("runtime", runtime_id))
    # Higher scopes win by name, while retaining deterministic order.
    by_name: dict[str, McpServer] = {}
    for scope, target in scopes:
        for server in list_servers(user_id, scope, target):
            if not mcp_policy.evaluate(server.name, workspace_id or None).allowed:
                continue
            by_name[server.name] = server
    scoped_servers.extend(by_name.values())
    servers = scoped_servers
    if requested is not None:
        servers = [server for server in servers if server.name in requested]
    if not servers:
        _mcp_tools_cache[cache_key] = (version, policy_version, [])
        return []

    connections = {s.name: build_connection(s) for s in servers}
    for server in servers:
        if server.trust.state in {"unsigned", "verification_unavailable"}:
            connections[server.name]["require_sandbox"] = True
    client = VectoraMCPClient()
    try:
        async with asyncio.timeout(_HEALTH_TIMEOUT_S):
            await client.connect(connections)
        remote_tools = client.tools()
        tools_by_server = client.tools_by_server()
    except Exception:
        logger.warning("plugins: falha ao carregar tools MCP de %s", user_id)
        remote_tools, tools_by_server = {}, {}
    finally:
        await client.aclose()

    tools = [
        _remote_tool_spec(
            tools_by_server[name],
            connections[tools_by_server[name]],
            t,
            user_id,
            workspace_id,
        )
        for name, t in remote_tools.items()
        if tool_policy.is_allowed(user_id, name)
    ]

    _mcp_tools_cache[cache_key] = (version, policy_version, tools)
    return tools
