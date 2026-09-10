"""Testes de integração — ``PostgresSessionStore``.

Requer Postgres rodando (vectora-postgres via docker) — `pg_pool` pula os
testes automaticamente se Docker estiver indisponível (mesmo padrão de
`test_storage_postgres.py`). Diferente de `pg_conn` (transação revertida por
teste), `pg_pool` persiste entre execuções — cada teste usa um `thread_id`
único (`uuid4`) pra não colidir com dados de rodadas anteriores no mesmo
container compartilhado.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.persistence.native.postgres_session_store import PostgresSessionStore
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


def _thread_id() -> str:
    return f"pg-session-store-{uuid4()}"


class TestCreateSession:
    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_cria_sessao_com_campos_obrigatorios(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()

        await store.create_session(thread_id, user_id="alice")

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id, mode, permission_mode FROM vectora_sessions "
                "WHERE thread_id = $1",
                thread_id,
            )
        assert row is not None
        assert (row["user_id"], row["mode"], row["permission_mode"]) == (
            "alice",
            "chat",
            "ask",
        )

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_criar_duas_vezes_a_mesma_thread_nao_falha(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()

        await store.create_session(thread_id, user_id="alice")
        await store.create_session(thread_id, user_id="alice")  # idempotente

        async with pg_pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM vectora_sessions WHERE thread_id = $1",
                thread_id,
            )
        assert total == 1


class TestAppendMessageEGetHistory:
    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_round_trip_preserva_ordem_e_conteudo(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        id1 = await store.append_message(
            thread_id, text_message(MessageRole.USER, "oi")
        )
        await store.append_message(
            thread_id,
            text_message(MessageRole.ASSISTANT, "olá!"),
            parent_message_id=id1,
        )

        historico = await store.get_history(thread_id)

        assert [m.text() for m in historico] == ["oi", "olá!"]
        assert [m.role for m in historico] == [MessageRole.USER, MessageRole.ASSISTANT]

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_turn_id_persistido_e_idempotente(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        first = await store.append_message(
            thread_id,
            text_message(MessageRole.USER, "retry seguro"),
            turn_id="turn-persistido",
        )
        second = await store.append_message(
            thread_id,
            text_message(MessageRole.USER, "não duplicar"),
            turn_id="turn-persistido",
        )

        assert second == first
        assert (
            await store.get_message_id_by_turn_id(thread_id, "turn-persistido") == first
        )
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT turn_id FROM vectora_native_messages WHERE id = $1", first
            )
        assert row["turn_id"] == "turn-persistido"

    async def test_thread_sem_mensagem_devolve_lista_vazia(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        assert await store.get_history(thread_id) == []

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_preserva_tool_calls_e_tool_call_id(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        assistente = VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text="chamando tool")],
            tool_calls=[ToolCall(id="call_1", name="somar", args={"a": 1})],
        )
        id_assistente = await store.append_message(thread_id, assistente)
        await store.append_message(
            thread_id,
            VMessage(
                role=MessageRole.TOOL,
                content=[ContentBlock(kind="text", text="2")],
                tool_call_id="call_1",
            ),
            parent_message_id=id_assistente,
        )

        historico = await store.get_history(thread_id)

        assert historico[0].tool_calls == [
            ToolCall(id="call_1", name="somar", args={"a": 1})
        ]
        assert historico[1].tool_call_id == "call_1"

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_fork_via_up_to_message_id_rele_branch_antiga_sem_apagar(
        self, pg_pool
    ):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        id1 = await store.append_message(
            thread_id, text_message(MessageRole.USER, "primeira pergunta")
        )
        await store.append_message(
            thread_id,
            text_message(MessageRole.ASSISTANT, "primeira resposta"),
            parent_message_id=id1,
        )
        await store.append_message(
            thread_id,
            text_message(MessageRole.USER, "pergunta editada"),
            parent_message_id=id1,
        )

        branch_ativa = await store.get_history(thread_id)
        branch_antiga = await store.get_history(thread_id, up_to_message_id=id1)

        assert [m.text() for m in branch_ativa] == [
            "primeira pergunta",
            "pergunta editada",
        ]
        assert [m.text() for m in branch_antiga] == ["primeira pergunta"]


class TestGetBranchHeadId:
    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_thread_sem_mensagem_devolve_none(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        assert await store.get_branch_head_id(thread_id) is None

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_devolve_o_id_da_ultima_mensagem_apendada(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        id1 = await store.append_message(
            thread_id, text_message(MessageRole.USER, "oi")
        )
        id2 = await store.append_message(
            thread_id,
            text_message(MessageRole.ASSISTANT, "olá"),
            parent_message_id=id1,
        )

        assert await store.get_branch_head_id(thread_id) == id2


class TestPendingApprovals:
    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_round_trip_put_get_clear(self, pg_pool):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        await store.put_pending_approval(
            thread_id,
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={"path": "a.py", "content": "x"},
            reasoning="editar arquivo",
        )

        pendente = await store.get_pending_approval(thread_id)
        assert pendente is not None
        assert pendente["tool_name"] == "file_write"
        assert pendente["args"] == {"path": "a.py", "content": "x"}

        await store.clear_pending_approval(thread_id)
        assert await store.get_pending_approval(thread_id) is None

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_segunda_chamada_de_put_sobrescreve_a_pendencia_anterior(
        self, pg_pool
    ):
        store = PostgresSessionStore(pg_pool)
        thread_id = _thread_id()
        await store.create_session(thread_id, user_id="alice")

        await store.put_pending_approval(
            thread_id,
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={},
        )
        await store.put_pending_approval(
            thread_id,
            interrupt_id="int-2",
            tool_name="terminal",
            tool_call_id="call_2",
            args={"cmd": "ls"},
        )

        pendente = await store.get_pending_approval(thread_id)
        assert pendente is not None
        assert pendente["interrupt_id"] == "int-2"
        assert pendente["tool_name"] == "terminal"
