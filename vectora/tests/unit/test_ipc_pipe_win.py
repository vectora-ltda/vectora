"""Tests para ipc_pipe_win: proxy HTTP/SSE via named pipe → TCP loopback.

Testes de protocolo (_PipeSide/_TCPSide) rodam em qualquer plataforma com
mocks. O teste de integração (serve_pipe + ProactorEventLoop) só roda no
Windows e é decorado com skipif.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import sys

import pytest

from backend.services.ipc_pipe_win import (
    PIPE_ENV_VAR,
    _PipeSide,
    _TCPSide,
    pipe_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeWriteTransport(asyncio.WriteTransport):
    def __init__(self) -> None:
        super().__init__()
        self.written: list[bytes] = []
        self._closing = False

    def write(self, data: bytes | bytearray | memoryview) -> None:  # type: ignore[override]
        self.written.append(bytes(data))

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    def get_write_buffer_size(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# pipe_name
# ---------------------------------------------------------------------------


def test_pipe_name_formato_e_pid():
    import os

    name = pipe_name()
    assert name.startswith(r"\\.\pipe\vectora-")
    assert str(os.getpid()) in name


def test_pipe_env_var_constante():
    assert PIPE_ENV_VAR == "VECTORA_IPC_PIPE"


# ---------------------------------------------------------------------------
# _PipeSide: buffering antes do TCP conectar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipe_side_buffers_antes_do_tcp(monkeypatch):
    """Dados recebidos antes do TCP conectar ficam no _pending e são enviados depois."""
    tcp_transport = _FakeWriteTransport()

    async def fake_create_conn(proto_factory, _host, _port):
        proto = proto_factory()
        proto.connection_made(tcp_transport)
        return tcp_transport, proto

    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "create_connection", fake_create_conn)

    pipe_side = _PipeSide("127.0.0.1", 9999)
    fake_pipe = _FakeWriteTransport()
    pipe_side.connection_made(fake_pipe)

    # Dados chegam ANTES do ensure_future rodar (TCP não conectado ainda)
    pipe_side.data_received(b"GET / HTTP/1.1\r\n\r\n")
    assert pipe_side._tcp_transport is None
    assert b"GET / HTTP/1.1\r\n\r\n" in pipe_side._pending

    # Deixa ensure_future executar
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Após TCP conectar, pending deve ter sido enviado e limpo
    assert b"GET / HTTP/1.1\r\n\r\n" in tcp_transport.written
    assert pipe_side._pending == []


@pytest.mark.asyncio
async def test_pipe_side_encaminha_dados_apos_tcp(monkeypatch):
    """Dados recebidos após TCP conectar são repassados diretamente."""
    tcp_transport = _FakeWriteTransport()

    async def fake_create_conn(proto_factory, _host, _port):
        proto = proto_factory()
        proto.connection_made(tcp_transport)
        return tcp_transport, proto

    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "create_connection", fake_create_conn)

    pipe_side = _PipeSide("127.0.0.1", 9999)
    pipe_side.connection_made(_FakeWriteTransport())

    await asyncio.sleep(0)  # _connect_tcp executa
    await asyncio.sleep(0)

    pipe_side.data_received(b"POST /auth HTTP/1.1\r\n")
    pipe_side.data_received(b"Content-Length: 0\r\n\r\n")

    assert b"POST /auth HTTP/1.1\r\n" in tcp_transport.written
    assert b"Content-Length: 0\r\n\r\n" in tcp_transport.written


@pytest.mark.asyncio
async def test_pipe_side_fecha_quando_tcp_falha(monkeypatch):
    """Se o uvicorn TCP não estiver acessível, a pipe fecha graciosamente."""

    async def fail_create_conn(proto_factory, host, port):
        raise OSError("connection refused")

    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "create_connection", fail_create_conn)

    pipe_side = _PipeSide("127.0.0.1", 9999)
    fake_pipe = _FakeWriteTransport()
    pipe_side.connection_made(fake_pipe)

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert fake_pipe._closing


@pytest.mark.asyncio
async def test_pipe_side_fecha_tcp_ao_desconectar(monkeypatch):
    """Quando a pipe fecha, a conexão TCP correspondente também é fechada."""
    tcp_transport = _FakeWriteTransport()

    async def fake_create_conn(proto_factory, _host, _port):
        proto = proto_factory()
        proto.connection_made(tcp_transport)
        return tcp_transport, proto

    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "create_connection", fake_create_conn)

    pipe_side = _PipeSide("127.0.0.1", 9999)
    pipe_side.connection_made(_FakeWriteTransport())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    pipe_side.connection_lost(None)

    assert tcp_transport._closing


@pytest.mark.asyncio
async def test_pipe_side_cancela_conexao_tcp_pendente_ao_desconectar():
    """Fechar a pipe não deixa uma tentativa TCP pendurada no loop."""
    pipe_side = _PipeSide("127.0.0.1", 9999)
    pending = asyncio.create_task(asyncio.sleep(60))
    pipe_side._connect_task = pending

    pipe_side.connection_lost(None)
    with contextlib.suppress(asyncio.CancelledError):
        await pending

    assert pending.cancelled()


# ---------------------------------------------------------------------------
# _TCPSide: resposta do uvicorn volta pela pipe
# ---------------------------------------------------------------------------


def test_tcp_side_encaminha_para_pipe():
    pipe_transport = _FakeWriteTransport()
    pipe_side = _PipeSide.__new__(_PipeSide)
    pipe_side._transport = pipe_transport
    pipe_side._tcp_transport = None
    pipe_side._pending = []

    tcp_side = _TCPSide(pipe_side)
    tcp_side.data_received(b"HTTP/1.1 200 OK\r\n\r\n")

    assert b"HTTP/1.1 200 OK\r\n\r\n" in pipe_transport.written


def test_tcp_side_nao_escreve_em_pipe_fechada():
    pipe_transport = _FakeWriteTransport()
    pipe_transport._closing = True
    pipe_side = _PipeSide.__new__(_PipeSide)
    pipe_side._transport = pipe_transport

    tcp_side = _TCPSide(pipe_side)
    tcp_side.data_received(b"data")

    assert pipe_transport.written == []


def test_tcp_side_fecha_pipe_ao_perder_conexao():
    pipe_transport = _FakeWriteTransport()
    pipe_side = _PipeSide.__new__(_PipeSide)
    pipe_side._transport = pipe_transport

    tcp_side = _TCPSide(pipe_side)
    tcp_side.connection_lost(None)

    assert pipe_transport._closing


# ---------------------------------------------------------------------------
# Integração real: apenas Windows + ProactorEventLoop
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="named pipe só no Windows")
@pytest.mark.asyncio
async def test_serve_pipe_inicia_e_cancela():
    """serve_pipe inicia sem erros e pode ser cancelado graciosamente."""
    from backend.services.ipc_pipe_win import serve_pipe

    _pipe = pipe_name()
    task = asyncio.create_task(serve_pipe(_pipe, "127.0.0.1", 65000))
    await asyncio.sleep(0.05)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert task.done()
    # `srv.close()` no finally do serve_pipe fecha o listener da pipe de forma
    # assíncrona (IOCP) — sem dar tempo ao loop pra completar antes do teste
    # acabar, o transport sobrevive com o handle já inválido até o GC rodar,
    # e aí o __del__ do ProactorBasePipeTransport explode DURANTE outro teste
    # qualquer (PytestUnraisableExceptionWarning). Dá tempo + força o GC aqui,
    # onde o loop ainda está de pé.
    await asyncio.sleep(0.1)
    gc.collect()


@pytest.mark.skipif(sys.platform != "win32", reason="named pipe só no Windows")
@pytest.mark.asyncio
async def test_serve_pipe_proxies_http():
    """Conexão à named pipe recebe resposta do handler TCP real."""
    from backend.services.ipc_pipe_win import serve_pipe

    HTTP_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"

    class _EchoProto(asyncio.Protocol):
        _t: asyncio.BaseTransport | None = None

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            self._t = transport

        def data_received(self, data: bytes) -> None:
            if isinstance(self._t, asyncio.WriteTransport):
                self._t.write(HTTP_OK)
                self._t.close()

    loop = asyncio.get_event_loop()
    tcp_server = await loop.create_server(_EchoProto, "127.0.0.1", 0)
    tcp_port: int = tcp_server.sockets[0].getsockname()[1]

    _pipe = pipe_name()
    pipe_task = asyncio.create_task(serve_pipe(_pipe, "127.0.0.1", tcp_port))
    await asyncio.sleep(0.05)

    received: list[bytes] = []

    class _ClientProto(asyncio.Protocol):
        def data_received(self, data: bytes) -> None:
            received.append(data)

    from typing import Any

    _loop_proactor: Any = (
        loop  # ProactorEventLoop only — não declarado em AbstractEventLoop
    )
    _create_pipe = _loop_proactor.create_pipe_connection
    _dummy_tr, _ = await _create_pipe(asyncio.Protocol, _pipe)
    transport2, _ = await _create_pipe(_ClientProto, _pipe)

    _write = transport2.write
    _write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    transport, protocol = _dummy_tr, _
    await asyncio.sleep(0.2)

    transport.close()
    transport2.close()
    # close() num transport de named pipe do ProactorEventLoop é assíncrono
    # (agenda call_connection_lost via IOCP) — sem esperar, o teste retorna
    # e o event loop desta função é destruído antes do fechamento real
    # terminar. O objeto do transport sobrevive (GC ainda não rodou) com o
    # handle do pipe já inválido; quando o GC finalmente coleta — durante um
    # teste completamente diferente — o __del__ tenta logar um warning e
    # `fileno()` explode com "I/O operation on closed pipe" no MEIO daquele
    # outro teste (PytestUnraisableExceptionWarning).
    await asyncio.sleep(0.1)
    pipe_task.cancel()
    tcp_server.close()
    with contextlib.suppress(asyncio.CancelledError):
        await pipe_task
    # Dá tempo pro lado servidor (transports internos do serve_pipe pra cada
    # conexão aceita) também terminar o close assíncrono, e força o GC aqui —
    # onde o loop ainda está de pé — em vez de deixar pra depois.
    await asyncio.sleep(0.1)
    gc.collect()

    assert any(b"200" in chunk for chunk in received)
