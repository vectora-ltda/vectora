"""Proxy de saída do jail, fora dele.

O jail com rede filtrada não tem rota de rede: ele só alcança um socket Unix,
montado de dentro do jail, onde este proxy escuta. O proxy aceita apenas
`CONNECT host:porta`, decide pela allowlist do workspace e pelas regras de SSRF
de `backend.services.egress_policy` (IP privado, loopback, metadados) e conecta
ao IP já validado, sem resolver o nome de novo (DNS rebinding). Cada decisão
fica registrada para auditoria.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.sandbox import audit
from backend.sandbox.policy import (
    SandboxPolicy,
    effective_allow_hosts,
    effective_network,
)
from backend.services.egress_policy import EgressPolicy, async_is_url_allowed

logger = logging.getLogger(__name__)

#: Nome do socket dentro da pasta montada no jail.
SOCKET_NAME = "egress.sock"
#: Onde a pasta do socket aparece dentro do jail.
JAIL_SOCKET_DIR = "/run/vectora-egress"
#: Porta (no loopback do próprio jail) em que a ponte escuta.
JAIL_PROXY_PORT = 3128

_HEADER_LIMIT = 16 * 1024
_HEADER_TIMEOUT_S = 10.0
_CONNECT_TIMEOUT_S = 15.0
_IDLE_TIMEOUT_S = 600.0
MAX_CONNECTIONS = 128
_RECENT_DECISIONS = 500


@dataclass(frozen=True)
class EgressDecision:
    """Uma tentativa de conexão e o que o proxy decidiu."""

    host: str
    port: int
    allowed: bool
    reason: str
    at: float


class EgressProxy:
    """Proxy CONNECT sobre socket Unix, com a allowlist de um workspace."""

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        workspace_id: str | None = None,
        on_decision: Callable[[EgressDecision], None] | None = None,
    ) -> None:
        self._egress = _egress_policy_for(policy, workspace_id)
        self._workspace_id = workspace_id
        self._on_decision = on_decision
        self._server: asyncio.AbstractServer | None = None
        self._directory: str | None = None
        self._connections = 0
        self.decisions: deque[EgressDecision] = deque(maxlen=_RECENT_DECISIONS)

    @property
    def directory(self) -> str:
        """Pasta (no host) que contém o socket; é a que o jail monta."""
        if self._directory is None:
            raise RuntimeError("proxy de egress não iniciado")
        return self._directory

    async def start(self) -> str:
        """Cria o socket (modo 0700, pasta própria) e começa a escutar."""
        self._directory = tempfile.mkdtemp(prefix="vgr-")
        Path(self._directory).chmod(0o700)
        path = Path(self._directory) / SOCKET_NAME
        self._server = await asyncio.start_unix_server(self._handle, path=str(path))
        path.chmod(0o600)
        return self._directory

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    # ── decisão ─────────────────────────────────────────────────────────────

    async def decide(self, host: str, port: int) -> tuple[bool, str, str | None]:
        """(permitido, motivo, IP validado) para `host:porta`."""
        allowed, reason, pinned = await async_is_url_allowed(
            f"https://{host}:{port}", self._egress
        )
        decision = EgressDecision(host, port, allowed, reason, time.time())
        self.decisions.append(decision)
        audit.record(
            self._workspace_id or "",
            "egress",
            {"host": host, "port": port, "allowed": allowed, "reason": reason},
        )
        if self._on_decision is not None:
            try:
                self._on_decision(decision)
            except Exception:
                logger.exception("egress: falha ao registrar a decisão")
        if not allowed:
            logger.info("egress: bloqueado %s:%s — %s", host, port, reason)
        return allowed, reason, pinned

    # ── protocolo ───────────────────────────────────────────────────────────

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._connections >= MAX_CONNECTIONS:
            await self._refuse(writer, 503, "muitas conexões simultâneas")
            return
        self._connections += 1
        try:
            await self._serve(reader, writer)
        except Exception:
            logger.debug("egress: conexão encerrada com erro", exc_info=True)
        finally:
            self._connections -= 1
            with contextlib.suppress(Exception):
                writer.close()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=_HEADER_TIMEOUT_S
            )
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await self._refuse(writer, 400, "requisição inválida")
            return
        if len(head) > _HEADER_LIMIT:
            await self._refuse(writer, 431, "cabeçalho grande demais")
            return
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
        parts = request_line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            await self._refuse(
                writer, 403, "o jail só aceita HTTPS (CONNECT) pelo proxy de saída"
            )
            return
        host, port = _split_authority(parts[1])
        if host is None or port is None:
            await self._refuse(writer, 400, "destino inválido")
            return

        allowed, reason, pinned = await self.decide(host, port)
        if not allowed or pinned is None:
            await self._refuse(
                writer, 403, f"bloqueado pela política de saída do workspace: {reason}"
            )
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(pinned, port), timeout=_CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError):
            await self._refuse(writer, 502, f"não foi possível conectar a {host}")
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        try:
            await asyncio.gather(
                _pipe(reader, upstream_writer), _pipe(upstream_reader, writer)
            )
        finally:
            with contextlib.suppress(Exception):
                upstream_writer.close()

    @staticmethod
    async def _refuse(writer: asyncio.StreamWriter, status: int, message: str) -> None:
        body = message.encode("utf-8")
        writer.write(
            f"HTTP/1.1 {status} Refused\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "X-Vectora-Egress: blocked\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode("latin-1")
            + body
        )
        with contextlib.suppress(Exception):
            await writer.drain()


def _egress_policy_for(policy: SandboxPolicy, workspace_id: str | None) -> EgressPolicy:
    """Regras do proxy: a allowlist do sandbox e, por cima, a negação do
    workspace (`denied_hosts`), sempre com a proteção de IP privado."""
    workspace = EgressPolicy.for_workspace(workspace_id)
    return EgressPolicy(
        workspace_id=workspace_id,
        block_private_ips=True,
        allowed_hosts=list(effective_allow_hosts(policy)),
        denied_hosts=list(workspace.denied_hosts),
        allowed_ports=[80, 443],
    )


def _split_authority(authority: str) -> tuple[str | None, int | None]:
    """`host:porta` ou `[v6]:porta` de uma linha CONNECT."""
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        port_text = rest.removeprefix(":")
    else:
        host, _, port_text = authority.rpartition(":")
    if not host or not port_text.isdigit():
        return None, None
    port = int(port_text)
    return (host, port) if 0 < port < 65536 else (None, None)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=_IDLE_TIMEOUT_S)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.write_eof()


# ── um proxy por workspace ──────────────────────────────────────────────────


class EgressRegistry:
    """Um proxy por workspace, compartilhado pelo worker e pelo terminal
    interativo. Recriado quando a política de rede muda."""

    def __init__(self) -> None:
        self._proxies: dict[str, tuple[SandboxPolicy, EgressProxy]] = {}
        self._lock = asyncio.Lock()

    async def ensure(
        self,
        workspace_id: str,
        policy: SandboxPolicy,
        *,
        on_decision: Callable[[EgressDecision], None] | None = None,
    ) -> EgressProxy:
        async with self._lock:
            current = self._proxies.get(workspace_id)
            if current is not None and _same_network(current[0], policy):
                return current[1]
            if current is not None:
                await current[1].stop()
            proxy = EgressProxy(
                policy, workspace_id=workspace_id, on_decision=on_decision
            )
            await proxy.start()
            self._proxies[workspace_id] = (policy, proxy)
            return proxy

    def get(self, workspace_id: str) -> EgressProxy | None:
        entry = self._proxies.get(workspace_id)
        return entry[1] if entry else None

    async def close(self, workspace_id: str) -> None:
        entry = self._proxies.pop(workspace_id, None)
        if entry is not None:
            await entry[1].stop()

    async def close_all(self) -> None:
        for workspace_id in list(self._proxies):
            await self.close(workspace_id)


def _same_network(a: SandboxPolicy, b: SandboxPolicy) -> bool:
    return (
        a.network == b.network
        and a.lockdown == b.lockdown
        and a.allow_hosts == b.allow_hosts
        and a.registry_hosts == b.registry_hosts
    )


egress_registry = EgressRegistry()


async def egress_dir_for(workspace_id: str, policy: SandboxPolicy) -> str | None:
    """Pasta do socket do proxy para um jail com rede filtrada (sobe o proxy se
    preciso); `None` quando o jail não é local ou a rede não é filtrada."""
    if not policy.enabled or policy.backend != "local":
        return None
    if effective_network(policy) != "filtered":
        return None
    return (await egress_registry.ensure(workspace_id, policy)).directory
