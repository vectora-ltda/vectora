"""MCP tool: invoca ferramentas de outros servidores via Model Context Protocol.

Usa o SDK oficial `mcp` (Python) direto. `VectoraMCPClient` também é
reutilizado por `backend/workspace/plugins.py` pra resolver as tools MCP
por-usuário (servidores configurados via marketplace).
`VectoraMCPClient` mantém uma `ClientSession` por servidor configurado
(stdio/SSE/streamable_http) e expõe as `mcp.types.Tool` agregadas.

Subprocess stdio nunca herda `os.environ` inteiro do processo pai — só um
allowlist mínimo (`_SAFE_SUBPROCESS_ENV_KEYS`) é repassado, fechando o vazamento
de API keys de LLM/tokens pro servidor MCP local. Um servidor específico pode
declarar nomes de variáveis obrigatórias via `env_vars`, mas os valores só entram
quando foram atribuídos explicitamente no campo `env` da conexão
(`McpServer.env` ou `settings.mcp_command_env_vars`).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool as MCPTool

from backend.settings import settings
from backend.tools.context import ToolContext
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


def _safe_subprocess_env(
    explicit_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Allowlist do ambiente do processo pai — nunca `os.environ` cru.

    `explicit_env` contém somente valores atribuídos explicitamente ao servidor
    pelo usuário. Nomes declarados pelo catálogo (`env_vars`) nunca autorizam
    leitura automática de `os.environ`."""
    environment = {
        k: v for k, v in os.environ.items() if k in _SAFE_SUBPROCESS_ENV_KEYS
    }
    environment.update(
        {
            key: value
            for key, value in (explicit_env or {}).items()
            if key and isinstance(value, str)
        }
    )
    return environment


def stdio_sandbox_available(launcher: str | None = None) -> bool:
    """Reports whether a protocol-aware stdio sandbox is available.

    ``run_sandboxed`` is a one-shot command runner and cannot safely wrap the
    bidirectional MCP protocol. A server runs isolated either through the
    configured external launcher or through the built-in jail (``bwrap``);
    with neither, unverified stdio servers fail closed instead of silently
    running on the host.
    """
    candidate = launcher or os.environ.get("VECTORA_MCP_STDIO_SANDBOX_LAUNCHER", "")
    external = bool(
        os.environ.get("VECTORA_MCP_STDIO_SANDBOX", "").lower() == "1"
        and candidate
        and shutil.which(candidate)
    )
    return external or builtin_stdio_sandbox_available()


def builtin_stdio_sandbox_available() -> bool:
    """O jail embutido (bwrap, Linux) está disponível para servidores stdio."""
    from backend.sandbox.mcp_launcher import jail_available

    return jail_available()


class VectoraMCPClient:
    """Uma `ClientSession` MCP por servidor configurado — gerencia conexões
    e expõe as tools agregadas.
    """

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools_by_name: dict[str, tuple[str, MCPTool]] = {}
        self._connected = False
        #: Servidores iniciados no jail embutido (para encerrar o proxy de saída).
        self._jails: list[Any] = []

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
            launcher = str(
                cfg.get("sandbox_launcher")
                or os.environ.get("VECTORA_MCP_STDIO_SANDBOX_LAUNCHER", "")
            )
            if cfg.get("require_sandbox") and not stdio_sandbox_available(launcher):
                raise RuntimeError(
                    "sandbox protocol-aware obrigatório para servidor MCP stdio indisponível"
                )
            explicit_env = cfg.get("env") or {}
            command = cfg["command"]
            args = list(cfg.get("args") or [])
            if cfg.get("require_sandbox"):
                if launcher and shutil.which(launcher):
                    command, args = launcher, [command, *args]
                else:
                    from backend.sandbox import mcp_launcher

                    jailed = await mcp_launcher.build_jailed_launch(
                        server_name,
                        command,
                        args,
                        allow_hosts=tuple(cfg.get("allow_hosts") or ()),
                    )
                    self._jails.append(jailed)
                    command, args = jailed.command, jailed.args
            params = StdioServerParameters(
                command=command,
                args=args,
                env=_safe_subprocess_env(explicit_env),
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

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        ctx: ToolContext | None = None,
    ) -> str:
        """Invoca `tool_name` no servidor que a expõe. Levanta `KeyError` se
        a tool não é conhecida — o chamador (`call_mcp_tool`) trata. Com
        ``ctx``, imagens do resultado são salvas e anexadas para o modelo."""
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
        return _render_call_result(result, ctx, await _transcribe_audio_blocks(result))

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
            from backend.sandbox import mcp_launcher

            for jailed in self._jails:
                try:
                    await mcp_launcher.release(jailed)
                except Exception:
                    logger.debug("mcp: erro ao encerrar o proxy de saída do jail")
            self._jails.clear()
            self._sessions.clear()
            self._tools_by_name.clear()
            self._connected = False


#: Áudio maior que isto não é transcrito (a nota diz que foi ignorado).
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
#: Teto da transcrição de um áudio no texto do resultado.
_MAX_AUDIO_TRANSCRIPT_CHARS = 12_000


async def _transcribe_audio_blocks(result: Any) -> dict[int, str]:
    """Transcreve localmente cada ``AudioContent`` do resultado.

    Devolve o texto por posição do bloco; áudio inválido, grande demais ou sem
    motor local fica de fora e o resultado traz só a nota. Nada sai do
    computador: não há envio ao provider.
    """
    from backend.services.speech_to_text import (
        SpeechToTextError,
        transcribe_local,
    )

    transcripts: dict[int, str] = {}
    for index, block in enumerate(getattr(result, "content", []) or []):
        if getattr(block, "type", None) != "audio":
            continue
        try:
            payload = base64.b64decode(getattr(block, "data", "") or "", validate=True)
        except (ValueError, binascii.Error):
            continue
        if not payload or len(payload) > _MAX_AUDIO_BYTES:
            continue
        mime_type = str(getattr(block, "mimeType", "") or "")
        suffix = (
            mimetypes.guess_extension(mime_type.split(";", maxsplit=1)[0].strip())
            or ".bin"
        )
        with tempfile.TemporaryDirectory(prefix="vectora-mcp-audio-") as workdir:
            source = Path(workdir) / f"audio{suffix}"
            source.write_bytes(payload)
            try:
                transcript = await transcribe_local(source)
            except SpeechToTextError as exc:
                logger.warning("mcp: áudio da tool não transcrito: %s", exc)
                continue
        text = transcript.text.strip()
        if text:
            language = f", idioma: {transcript.language}" if transcript.language else ""
            transcripts[index] = (
                f"[áudio retornado pela tool ({mime_type}), transcrição local"
                f"{language}]\n{text[:_MAX_AUDIO_TRANSCRIPT_CHARS]}"
            )
    return transcripts


def _render_call_result(
    result: Any,
    ctx: ToolContext | None = None,
    audio_transcripts: dict[int, str] | None = None,
) -> str:
    """Converte um `CallToolResult` em texto para o modelo.

    Texto é concatenado como antes. Imagens (`ImageContent`) são salvas como
    asset da sessão e anexadas ao resultado para o modelo (mesmo caminho dos
    screenshots); o base64 nunca entra no texto. Áudio entra como a
    transcrição local (``audio_transcripts``, por posição do bloco) e, sem
    ela, como uma nota; recursos binários viram uma nota curta.
    """
    parts: list[str] = []
    images: list[dict[str, Any]] = []
    urls: list[str] = []
    for index, block in enumerate(getattr(result, "content", []) or []):
        kind = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
            continue
        if kind == "image":
            saved = _save_mcp_image(block, ctx, index)
            if saved is None:
                parts.append(
                    f"[imagem retornada pela tool ({getattr(block, 'mimeType', '')}) "
                    "não pôde ser salva]"
                )
            else:
                images.append(saved["model_image"])
                urls.append(saved["url"])
                parts.append(f"[imagem anexada: {saved['model_image']['name']}]")
            continue
        if kind == "audio":
            transcript = (audio_transcripts or {}).get(index)
            parts.append(
                transcript
                if transcript is not None
                else (
                    f"[áudio retornado pela tool ({getattr(block, 'mimeType', '')}) "
                    "não transcrito e não enviado ao modelo]"
                )
            )
            continue
        if kind == "resource":
            resource = getattr(block, "resource", None)
            resource_text = getattr(resource, "text", None)
            parts.append(
                resource_text
                if resource_text is not None
                else f"[recurso binário: {getattr(resource, 'uri', '')}]"
            )
            continue
        if kind == "resource_link":
            parts.append(f"[recurso: {getattr(block, 'uri', '')}]")
            continue
        parts.append(str(block))
    rendered = "\n".join(parts) if parts else str(result)
    if getattr(result, "isError", False):
        return f"Erro da tool MCP: {rendered}"
    if not images:
        return rendered
    from backend.services.tool_media import MODEL_IMAGE_KEY, MODEL_IMAGES_KEY

    return json.dumps(
        {
            "text": rendered,
            "url": urls[0],
            "urls": urls,
            MODEL_IMAGE_KEY: images[0],
            MODEL_IMAGES_KEY: images,
        },
        ensure_ascii=False,
    )


def _save_mcp_image(
    block: Any, ctx: ToolContext | None, index: int
) -> dict[str, Any] | None:
    """Decodifica e salva uma `ImageContent` do MCP como asset da sessão."""
    if ctx is None or not ctx.thread_id:
        return None
    try:
        payload = base64.b64decode(getattr(block, "data", "") or "", validate=True)
    except (ValueError, binascii.Error):
        return None
    from backend.services.tool_media import save_tool_image

    return save_tool_image(ctx, payload, name=f"mcp-imagem-{index + 1}.png")


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
            "env": {
                key: os.environ[key]
                for key in settings.mcp_command_env_vars or []
                if key in os.environ
            },
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
async def call_mcp_tool(
    tool_name: str, arguments: str, ctx: ToolContext | None = None
) -> str:
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
            return await client.call_tool(tool_name, args_dict, ctx)
    except TimeoutError:
        logger.warning("call_mcp_tool timeout", extra={"tool": tool_name})
        return f"Erro: tool MCP '{tool_name}' excedeu {settings.mcp_timeout}s."
    except Exception as e:
        logger.exception("call_mcp_tool_failed", extra={"tool": tool_name})
        return f"Erro ao invocar tool MCP '{tool_name}': {e!s}"
