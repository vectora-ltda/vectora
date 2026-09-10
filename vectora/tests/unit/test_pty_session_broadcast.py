"""Testes do fan-out de PtySession (broadcast pra múltiplos WS no mesmo terminal).

Antes desta mudança, `read()` drenava uma única `asyncio.Queue` — dois
clientes WS abrindo o mesmo `terminal_id` competiam pelos mesmos itens
(round-robin), então o segundo cliente nunca via a saída completa. Agora
cada consumidor chama `subscribe()` e ganha sua própria fila; o read-loop
faz broadcast pra todas.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services.pty_session import PtySession


class _FakeProc:
    """Simula o backend PTY: devolve os chunks de `outputs` e então EOF."""

    def __init__(self, outputs: list[bytes]) -> None:
        self._outputs = list(outputs)

    def read(self, _n: int) -> bytes:
        if not self._outputs:
            raise EOFError
        return self._outputs.pop(0)


def _make_session(outputs: list[bytes]) -> PtySession:
    proc = _FakeProc(outputs)
    return PtySession(
        terminal_id="term-1", workspace_id="ws-1", thread_id="t1", proc=proc
    )


async def _drain(q: asyncio.Queue[bytes | None]) -> list[bytes]:
    chunks: list[bytes] = []
    while True:
        item = await q.get()
        if item is None:
            return chunks
        chunks.append(item)


@pytest.mark.asyncio
async def test_dois_subscribers_recebem_todos_os_chunks() -> None:
    session = _make_session([b"a", b"b", b"c"])
    q1 = session.subscribe()
    q2 = session.subscribe()

    await session._read_loop()

    assert await _drain(q1) == [b"a", b"b", b"c"]
    assert await _drain(q2) == [b"a", b"b", b"c"]


@pytest.mark.asyncio
async def test_subscriber_tardio_recebe_scrollback_acumulado() -> None:
    """subscribe() depois do read-loop já ter rodado recebe o scroll-back
    acumulado como primeiro item da fila — cobre o caso de reconexão (reload
    de página) a uma sessão PTY que já produziu output. Borda: o buffer é
    limitado a `SCROLLBACK_MAX_BYTES`, então output que excede o limite é
    truncado a partir do início (mantém só o mais recente)."""
    session = _make_session([b"a", b"b"])
    q1 = session.subscribe()
    await session._read_loop()
    await _drain(q1)

    q2 = session.subscribe()
    assert await q2.get() == b"ab"

    scrollback_max_bytes = 64 * 1024
    big_session = _make_session([b"x" * scrollback_max_bytes, b"y"])
    q3 = big_session.subscribe()
    await big_session._read_loop()
    await _drain(q3)

    q4 = big_session.subscribe()
    replayed = await q4.get()
    assert replayed is not None
    assert len(replayed) == scrollback_max_bytes
    assert replayed.endswith(b"y")
    assert not replayed.startswith(b"x" * scrollback_max_bytes)


@pytest.mark.asyncio
async def test_unsubscribe_para_de_receber_broadcast() -> None:
    session = _make_session([b"x"])
    q1 = session.subscribe()
    session.unsubscribe(q1)

    await session._read_loop()

    assert q1.empty()


def test_unsubscribe_de_fila_desconhecida_nao_quebra() -> None:
    session = _make_session([])
    q = asyncio.Queue()
    session.unsubscribe(q)  # não deve levantar


@pytest.mark.asyncio
async def test_sem_subscribers_read_loop_nao_quebra() -> None:
    session = _make_session([b"a"])
    await session._read_loop()  # não deve levantar mesmo sem consumidores


@pytest.mark.asyncio
async def test_leitura_incremental_preserva_broadcast_e_sinaliza_continuacao() -> None:
    session = _make_session([b"abc", b"def"])
    subscriber = session.subscribe()
    await session._read_loop()

    first = session.read_since(None, max_bytes=4)
    assert first["data"] == b"abcd"
    assert first["has_more"] is True
    second = session.read_since(first["cursor"], max_bytes=4)
    assert second["data"] == b"ef"
    assert second["truncated"] is False
    assert await _drain(subscriber) == [b"abc", b"def"]


@pytest.mark.asyncio
async def test_cursor_antigo_indica_truncamento() -> None:
    session = _make_session([b"x" * (64 * 1024), b"y"])
    await session._read_loop()

    result = session.read_since(0, max_bytes=64 * 1024)
    assert result["truncated"] is True
    assert result["data"].endswith(b"y")


def test_write_input_eh_idempotente_e_observa_falha() -> None:
    class Proc:
        def isalive(self) -> bool:
            return True

        def write(self, _data: bytes) -> None:
            return None

    session = PtySession("term-1", "ws-1", "t1", Proc())
    assert session.write_input(b"ok", "req-1")["status"] == "accepted"
    assert session.write_input(b"ok", "req-1")["status"] == "duplicate"

    class Broken(Proc):
        def write(self, _data: bytes) -> None:
            raise OSError("closed")

    broken = PtySession("term-2", "ws-1", "t1", Broken())
    assert broken.write_input(b"x", "req-2")["code"] == "write_failed"
