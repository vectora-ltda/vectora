"""Efeitos gravados por um turno de chat e cursores do stream.

Complementa ``test_native_stream_dedup_e2e.py``: em vez do que o cliente
recebe, confere o que fica persistido depois do stream e que o retry do mesmo
``turn_id`` não duplica nada.
"""

from __future__ import annotations

import json

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.conversation_loop import LoopConfig, run_conversation
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import MessageRole, VMessageChunk, text_message

THREAD = "thread-efeitos"


@pytest.fixture
async def store(tmp_path):
    pool = AsyncConnectionPool(str(tmp_path / "efeitos.db"), min_size=1, max_size=2)
    await pool.open()
    session_store = SessionStore(pool)
    await session_store.setup()
    await session_store.create_session(THREAD, user_id="local")
    try:
        yield session_store
    finally:
        await pool.close()


class _Client:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        for token in self._tokens:
            yield VMessageChunk(delta_text=token)

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        msg = "não usado (astream-only)"
        raise NotImplementedError(msg)


async def _turno(store: SessionStore, tokens: list[str], turn_id: str) -> list[dict]:
    parent = await store.append_message(
        THREAD, text_message(MessageRole.USER, "oi"), turn_id=turn_id
    )
    assert parent

    async def _run(on_event) -> str:
        result = await run_conversation(
            session_store=store,
            chat_client=_Client(tokens),
            tool_registry=ToolRegistry(),
            ctx=ToolContext(user_id="local"),
            thread_id=THREAD,
            config=LoopConfig(max_iterations=5),
            on_event=on_event,
        )
        return result.stopped_reason

    lines = [
        line
        async for line in stream_engine_events(_run, thread_id=THREAD, user_id="local")
    ]
    return [json.loads(line[len("data: ") :]) for line in lines]


async def test_turno_concluido_grava_uma_resposta_com_o_texto_completo(
    store: SessionStore,
) -> None:
    out = await _turno(store, ["Olá", " ", "mundo"], "turn-1")

    historico = await store.get_history(THREAD)
    assistentes = [m for m in historico if m.role == MessageRole.ASSISTANT]
    assert len(assistentes) == 1
    assert assistentes[0].text() == "Olá mundo"
    assert out[-1]["type"] == "done"
    assert [m.role for m in historico] == [MessageRole.USER, MessageRole.ASSISTANT]


async def test_repetir_o_turn_id_nao_duplica_a_mensagem_do_usuario(
    store: SessionStore,
) -> None:
    primeiro = await store.append_message(
        THREAD, text_message(MessageRole.USER, "oi"), turn_id="turn-dup"
    )
    existente = await store.get_message_id_by_turn_id(THREAD, "turn-dup")
    assert existente == primeiro

    segundo = await store.append_message(
        THREAD, text_message(MessageRole.USER, "oi"), turn_id="turn-dup"
    )
    assert segundo == primeiro
    historico = await store.get_history(THREAD)
    usuarios = [m for m in historico if m.role == MessageRole.USER]
    assert len(usuarios) == 1


async def test_cursores_do_stream_sao_contiguos_e_apontam_para_o_run_gravado(
    store: SessionStore,
) -> None:
    out = await _turno(store, ["a", "b", "c"], "turn-cursor")

    assert [e["seq"] for e in out] == list(range(1, len(out) + 1))
    run_ids = {e["event_id"].rsplit(":", 1)[0] for e in out}
    assert len(run_ids) == 1
    assert out[-1]["event_id"] == f"{run_ids.pop()}:{len(out)}"
    assert out[-1]["type"] == "done"
