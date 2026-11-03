"""IPC desktop via Windows named pipe — proxy HTTP para o uvicorn TCP loopback.

Quando VECTORA_DESKTOP=1 no Windows, o uvicorn escuta em TCP loopback (porta
efêmera, sem exposição de rede), e este módulo sobe um servidor de named pipe
que aceita todas as conexões do Electron, fazendo proxy de bytes brutos para o
uvicorn — sem porta TCP visível externamente.

Requer asyncio.ProactorEventLoop (padrão no Windows desde Python 3.8).
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

PIPE_ENV_VAR = "VECTORA_IPC_PIPE"
_TCP_CONNECT_ATTEMPTS = 8
_TCP_CONNECT_INITIAL_DELAY = 0.05


def pipe_name() -> str:
    """Caminho único da named pipe para este processo."""
    return f"\\\\.\\pipe\\vectora-{os.getpid()}"


class _TCPSide(asyncio.Protocol):
    """Recebe dados do uvicorn TCP e os encaminha de volta pela pipe."""

    def __init__(self, pipe_side: _PipeSide) -> None:
        self._pipe = pipe_side

    def data_received(self, data: bytes) -> None:
        transport = self._pipe._transport
        if isinstance(transport, asyncio.WriteTransport) and not transport.is_closing():
            transport.write(data)

    def connection_lost(self, exc: BaseException | None) -> None:
        transport = self._pipe._transport
        if transport and not transport.is_closing():
            transport.close()


class _PipeSide(asyncio.Protocol):
    """Aceita uma conexão na named pipe e faz proxy de bytes para TCP loopback.

    Suporta SSE e qualquer outro stream HTTP longo: os bytes fluem de forma
    totalmente transparente em ambas as direções.
    """

    def __init__(self, tcp_host: str, tcp_port: int) -> None:
        self._tcp_host = tcp_host
        self._tcp_port = tcp_port
        self._transport: asyncio.BaseTransport | None = None
        self._tcp_transport: asyncio.WriteTransport | None = None
        self._pending: list[bytes] = []
        self._connect_task: asyncio.Task[None] | None = None
        self._closed = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport
        self._connect_task = asyncio.ensure_future(self._connect_tcp())

    async def _connect_tcp(self) -> None:
        loop = asyncio.get_running_loop()
        for attempt in range(_TCP_CONNECT_ATTEMPTS):
            try:
                transport, _ = await loop.create_connection(
                    lambda: _TCPSide(self), self._tcp_host, self._tcp_port
                )
                break
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                if self._closed:
                    return
                if attempt == _TCP_CONNECT_ATTEMPTS - 1:
                    logger.warning(
                        "ipc_pipe_win: uvicorn indisponível após %d tentativas: %s",
                        _TCP_CONNECT_ATTEMPTS,
                        exc,
                    )
                    if self._transport and not self._transport.is_closing():
                        self._transport.close()
                    return
                await asyncio.sleep(_TCP_CONNECT_INITIAL_DELAY * (2**attempt))
        if self._closed:
            transport.close()
            return
        if not isinstance(transport, asyncio.WriteTransport):
            transport.close()
            return
        self._tcp_transport = transport
        for chunk in self._pending:
            self._tcp_transport.write(chunk)
        self._pending.clear()

    def data_received(self, data: bytes) -> None:
        if self._tcp_transport:
            self._tcp_transport.write(data)
        else:
            self._pending.append(data)

    def connection_lost(self, exc: BaseException | None) -> None:
        self._closed = True
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
        if self._tcp_transport and not self._tcp_transport.is_closing():
            self._tcp_transport.close()


async def serve_pipe(pipe_path: str, tcp_host: str, tcp_port: int) -> None:
    """Inicia o servidor de named pipe e bloqueia até cancelamento.

    Cada conexão do Electron na pipe vira uma sessão de proxy independente.
    A tarefa retorna somente quando é cancelada (asyncio.CancelledError).

    Args:
        pipe_path: Caminho da named pipe (``\\\\.\\pipe\\vectora-<pid>``).
        tcp_host: Host do uvicorn TCP (sempre ``127.0.0.1`` no desktop).
        tcp_port: Porta do uvicorn TCP.
    """
    loop = asyncio.get_running_loop()
    if not hasattr(loop, "start_serving_pipe"):
        raise RuntimeError(
            "serve_pipe requer asyncio.ProactorEventLoop. "
            f"Loop atual: {type(loop).__name__}"
        )
    from typing import Any

    _loop_proactor: Any = (
        loop  # ProactorEventLoop — AbstractEventLoop não expõe a API de pipe
    )
    _start = _loop_proactor.start_serving_pipe
    servers: list = await _start(
        lambda: _PipeSide(tcp_host, tcp_port),
        pipe_path,
    )
    logger.info("ipc_pipe_win: named pipe %s → %s:%d", pipe_path, tcp_host, tcp_port)
    try:
        await asyncio.get_running_loop().create_future()
    finally:
        for srv in servers:
            srv.close()
        await asyncio.gather(
            *(srv.wait_closed() for srv in servers if hasattr(srv, "wait_closed")),
            return_exceptions=True,
        )
