"""MCP tool: invoca ferramentas de outros servidores via Model Context Protocol.

Usa o SDK oficial `mcp` (Python) direto. `VectoraMCPClient` também é
reutilizado por `backend/workspace/plugins.py` pra resolver as tools MCP
por-usuário (servidores configurados via marketplace).
`VectoraMCPClient` mantém uma `ClientSession` por servidor configurado
(stdio/SSE/streamable_http) e expõe as `mcp.types.Tool` agregadas.

Subprocess stdio nunca herda `os.environ` inteiro do processo pai — só um
allowlist mínimo (`_SAFE_SUBPROCESS_ENV_KEYS`) é repassado, fechando o vazamento
de API keys de LLM/tokens pro servidor MCP local. Um servidor específico pode
declarar variáveis extras necessárias via `env_vars` na conexão (`McpServer.env_vars`
em `backend/workspace/plugins.py` ou `settings.mcp_command_env_vars`) — só essas,
por nome, se somam ao allowlist mínimo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool as MCPTool

from backend.settings import settings
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

#: Variáveis de ambiente sempre repassadas ao subprocess stdio — o mínimo
#: pra um processo Python/Node/etc. rodar (PATH, home dir, locale, temp).
#: Nunca inclui API keys, tokens ou segredos do processo Vectora.
_SAFE_SUBPROCESS_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "APPDATA",
        "LOCALAPPDATA",
    }
)


def _safe_subprocess_env(extra_keys: frozenset[str] | None = None) -> dict[str, str]:
    """Allowlist do ambiente do processo pai — nunca `os.environ` cru.

    `extra_keys` é a lista de variáveis que o servidor MCP específico
    declarou precisar (``McpServer.env_vars`` / `settings.mcp_command_env_vars`)
    — só essas, além do mínimo pra o subprocess rodar, atravessam."""
    keys = _SAFE_SUBPROCESS_ENV_KEYS | (extra_keys or frozenset())
    return {k: v for k, v in os.environ.items() if k in keys}


def stdio_sandbox_available() -> bool:
    """Reports whether the persistent stdio sandbox adapter is configured.

    ``run_sandboxed`` is a one-shot command runner and cannot safely wrap the
    bidirectional MCP protocol. Until a protocol-aware launcher is installed,
    unverified stdio servers fail closed instead of silently running on host.
    """
    return os.environ.get("VECTORA_MCP_STDIO_SANDBOX", "").lower() == "1"


class VectoraMCPClient:
    """Uma `ClientSession` MCP por servidor configurado — gerencia conexões
    e expõe as tools agregadas.
    """

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools_by_name: dict[str, tuple[str, MCPTool]] = {}
        self._connected = False

    async def connect(
        self, connections: dict[str, dict[str, Any]], *, strict: bool = False
    ) -> None:
        """Abre uma sessão por servidor configurado.

        Por padrão, falha de um servidor não impede os demais — cada conexão
        é isolada e logada (uso em produção, múltiplos servidores agregados).
        Com ``strict=True`` a primeira falha propaga — uso em health-check de
        um único servidor, onde o chamador precisa do erro real."""
        for server_name, cfg in connections.items():
            try:
                session = await self._connect_one(server_name, cfg)
            except Exception:
                logger.exception(
                    "mcp: falha ao conectar servidor", extra={"server": server_name}
                )
                if strict:
                    raise
                continue
            self._sessions[server_name] = session
            try:
                listed = await session.list_tools()
            except Exception:
                logger.exception(
                    "mcp: falha ao listar tools", extra={"server": server_name}
                )
                if strict:
                    raise
                continue
            for t in listed.tools:
                self._tools_by_name[t.name] = (server_name, t)
        self._connected = True

    async def _connect_one(
        self, server_name: str, cfg: dict[str, Any]
    ) -> ClientSession:
        transport = cfg["transport"]
        if transport == "stdio":
            if cfg.get("require_sandbox") and not stdio_sandbox_available():
                raise RuntimeError(
                    "sandbox obrigatório para servidor MCP stdio indisponível"
                )
            extra_keys = frozenset(cfg.get("env_vars") or ())
            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args") or [],
                env=_safe_subprocess_env(extra_keys),
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif transport == "sse":
            read, write = await self._stack.enter_async_context(sse_client(cfg["url"]))
        elif transport == "streamable_http":
            read, write = await self._stack.enter_async_context(
                streamable_http_client(cfg["url"])
            )
        else:
            raise ValueError(f"transporte MCP desconhecido: {transport!r}")

        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        logger.info("mcp: servidor conectado", extra={"server": server_name})
        return session

    def tools(self) -> dict[str, MCPTool]:
        return {name: tool for name, (_, tool) in self._tools_by_name.items()}

    def tools_by_server(self) -> dict[str, str]:
        """Nome da tool -> nome do servidor configurado que a expõe."""
        return {name: server for name, (server, _) in self._tools_by_name.items()}

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoca `tool_name` no servidor que a expõe. Levanta `KeyError` se
        a tool não é conhecida — o chamador (`call_mcp_tool`) trata."""
        server_name, _ = self._tools_by_name[tool_name]
        session = self._sessions[server_name]
        t0 = time.perf_counter()
        logger.info(
            "mcp_external_tool_call", extra={"tool": tool_name, "server": server_name}
        )
        try:
            result = await session.call_tool(tool_name, arguments)
        except Exception:
            logger.exception("mcp_external_tool_failed", extra={"tool": tool_name})
            raise
        logger.debug(
            "mcp_external_tool_done",
            extra={"tool": tool_name, "elapsed_s": round(time.perf_counter() - t0, 3)},
        )
        return _render_call_result(result)

    async def aclose(self) -> None:
        """Fecha as sessões/subprocessos abertos — best-effort, nunca propaga.

        O SDK oficial `mcp` tem uma race conhecida no teardown de
        `stdio_client`/`ClientSession` dentro do mesmo `AsyncExitStack`: o
        subprocess pode encerrar enquanto a task de leitura ainda tenta
        escrever no stream já fechado, virando `ExceptionGroup`
        (`anyio.BrokenResourceError`) no `__aexit__`. Nesse ponto a conexão
        já cumpriu seu propósito (tools listadas/tool chamada com sucesso)
        — propagar aqui reportaria como falha uma chamada que já teve
        sucesso, só por ruído de encerramento."""
        try:
            await self._stack.aclose()
        except Exception:
            logger.debug("mcp: erro no teardown do client (best-effort, ignorado)")
        finally:
            self._sessions.clear()
            self._tools_by_name.clear()
            self._connected = False


def _render_call_result(result: Any) -> str:
    """Concatena os blocos de conteúdo de um `CallToolResult` em texto —
    mesmo shape de string que `call_mcp_tool` sempre devolveu."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(block))
    rendered = "\n".join(parts) if parts else str(result)
    if getattr(result, "isError", False):
        return f"Erro da tool MCP: {rendered}"
    return rendered


# ---------------------------------------------------------------------------
# Configuração de conexões + cache global
# ---------------------------------------------------------------------------

_mcp_client: VectoraMCPClient | None = None
_mcp_lock: asyncio.Lock = asyncio.Lock()


def _build_connections() -> dict[str, dict[str, Any]]:
    """Monta o dict de conexões a partir das settings — mesmo shape de
    antes (`transport` + campos específicos por tipo)."""
    connections: dict[str, dict[str, Any]] = {}

    if settings.mcp_server_url:
        transport = "sse" if settings.mcp_transport_type == "sse" else "streamable_http"
        connections["default"] = {
            "transport": transport,
            "url": settings.mcp_server_url,
        }

    if settings.mcp_command:
        connections["local"] = {
            "transport": "stdio",
            "command": settings.mcp_command,
            "args": settings.mcp_command_args or [],
            "env_vars": settings.mcp_command_env_vars or [],
        }

    return connections


async def _get_mcp_client() -> VectoraMCPClient | None:
    """Obtém ou cria a instância global do `VectoraMCPClient`."""
    global _mcp_client

    if _mcp_client is not None:
        return _mcp_client

    connections = _build_connections()
    if not connections:
        logger.debug("Nenhum servidor MCP configurado (mcp_server_url/mcp_command)")
        return None

    async with _mcp_lock:
        if _mcp_client is not None:  # double-check após o lock
            return _mcp_client
        client = VectoraMCPClient()
        try:
            await client.connect(connections)
        except Exception:
            logger.exception("Falha ao inicializar MCP client")
            await client.aclose()
            return None
        _mcp_client = client
        logger.info(
            "MCP client inicializado", extra={"servers": list(connections.keys())}
        )

    return _mcp_client


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="mcp",
        destructive=False,
        icon="share-2",
    )
)
async def call_mcp_tool(tool_name: str, arguments: str) -> str:
    """Invoca uma ferramenta de outro servidor MCP via Model Context Protocol.

    Use para encadear capacidades de servidores MCP externos registrados em
    settings (`mcp_server_url` ou `mcp_command`).

    Args:
        tool_name: Nome da ferramenta no servidor MCP
        arguments: Argumentos em formato JSON string (ex: '{"query": "x"}')

    Returns:
        Resposta da ferramenta MCP, ou mensagem de erro descritiva
    """
    if not settings.enable_mcp:
        return "MCP desabilitado. Defina ENABLE_MCP=true para usar servidores externos."

    try:
        args_dict: dict[str, Any] = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as e:
        return f"Erro: 'arguments' deve ser JSON válido — {e}"

    client = await _get_mcp_client()
    if client is None:
        return (
            "Nenhuma tool MCP disponível. Verifique mcp_server_url / mcp_command "
            "nas configurações."
        )

    tools = client.tools()
    if tool_name not in tools:
        return (
            f"Tool MCP '{tool_name}' não encontrada. "
            f"Disponíveis: {', '.join(sorted(tools))}"
        )

    try:
        async with asyncio.timeout(settings.mcp_timeout):
            return await client.call_tool(tool_name, args_dict)
    except TimeoutError:
        logger.warning("call_mcp_tool timeout", extra={"tool": tool_name})
        return f"Erro: tool MCP '{tool_name}' excedeu {settings.mcp_timeout}s."
    except Exception as e:
        logger.exception("call_mcp_tool_failed", extra={"tool": tool_name})
        return f"Erro ao invocar tool MCP '{tool_name}': {e!s}"
