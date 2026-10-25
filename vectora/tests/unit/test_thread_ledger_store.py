"""Testes unitários do Ledger durável e idempotente no SessionStore (SQLite).

Cobre os critérios de aceite:
- Sequência monótona crescente (`seq`).
- Deduplicação por `event_key` (mesma chave não gera nova linha nem altera badges).
- Concorrência de `turn_id` e idempotência.
- Atomicidade de mensagem e `message_created`.
- Atomicidade de claim e `approval_resolved`.
- Exclusão do ledger em `delete_session`.
- Cursor de leitura monótono e limitado por `MAX(seq)`.
- Rascunhos de run (`upsert_run_draft`, `get_run_draft`, `delete_run_draft`).
- Resumo derivado em lote (`get_thread_summaries`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from backend.persistence.native.ledger import (
    ThreadLedgerEvent,
    make_agent_turn_started_key,
    make_approval_requested_key,
    make_approval_resolved_key,
    make_message_created_key,
    make_run_completed_key,
    make_run_failed_key,
    make_tool_finished_key,
    make_tool_started_key,
)
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.vtypes.message import MessageRole, text_message


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore]:
    """Executa a operação store."""
    pool = AsyncConnectionPool(str(tmp_path / "sessions.db"), min_size=1, max_size=5)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


class TestThreadLedgerStore:
    async def test_append_event_monotone_seq_and_idempotency(
        self, store: SessionStore
    ) -> None:
        """seq é monótono e a mesma event_key retorna o evento existente sem duplicar."""
        await store.create_session("thread-1", user_id="alice")

        draft1 = ThreadLedgerEvent(
            thread_id="thread-1",
            event_key=make_agent_turn_started_key("run-1", "turn-1"),
            type="agent_turn_started",
            turn_id="turn-1",
            run_id="run-1",
        )
        ev1 = await store.append_event("thread-1", draft1)
        assert ev1.seq == 1
        assert ev1.event_key == draft1.event_key

        # Re-enviar mesmo draft com mesma event_key -> retorna o mesmo ev1 (seq 1), sem duplicar
        ev1_repeat = await store.append_event("thread-1", draft1)
        assert ev1_repeat.seq == 1
        assert ev1_repeat.event_key == draft1.event_key

        # Próximo evento diferente -> seq 2
        draft2 = ThreadLedgerEvent(
            thread_id="thread-1",
            event_key=make_tool_started_key("run-1", "tool-call-1"),
            type="tool_started",
            run_id="run-1",
            tool_call_id="tool-call-1",
        )
        ev2 = await store.append_event("thread-1", draft2)
        assert ev2.seq == 2

    async def test_append_message_recorded_atomicity(self, store: SessionStore) -> None:
        """append_message_recorded grava mensagem e evento message_created na mesma transação."""
        await store.create_session("thread-1", user_id="alice")

        msg = text_message(MessageRole.ASSISTANT, "Olá! Como posso ajudar?")
        msg_id, seq = await store.append_message_recorded(
            "thread-1",
            msg,
            run_id="run-1",
            turn_id="turn-1",
        )
        assert msg_id > 0
        assert seq == 1

        # Checar se evento message_created existe no ledger
        events = await store.list_events("thread-1", after_seq=0)
        assert len(events) == 1
        assert events[0].type == "message_created"
        assert events[0].message_id == msg_id
        assert events[0].payload.get("role") == "assistant"
        assert events[0].payload.get("content") == "Olá! Como posso ajudar?"

        # append_message tradicional delega sem quebrar chamada
        msg2 = text_message(MessageRole.USER, "Mais uma mensagem")
        msg2_id = await store.append_message("thread-1", msg2)
        assert msg2_id > msg_id

        events2 = await store.list_events("thread-1", after_seq=seq)
        assert len(events2) == 1
        assert events2[0].type == "message_created"
        assert events2[0].message_id == msg2_id
        assert events2[0].seq == 2

    async def test_append_event_backfills_legacy_messages_before_allocating_seq(
        self, store: SessionStore
    ) -> None:
        """Eventos novos seguem as mensagens legadas já existentes no ledger."""
        await store.create_session("thread-legacy", user_id="alice")
        async with store._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO messages (thread_id, role, content_json, "
                "is_branch_head, created_at) VALUES (?, ?, ?, 1, ?)",
                (
                    "thread-legacy",
                    "user",
                    '[{"kind":"text","text":"histórico"}]',
                    "2026-10-01T10:00:00Z",
                ),
            )
            await conn.commit()

        event = await store.append_event(
            "thread-legacy",
            ThreadLedgerEvent(
                thread_id="thread-legacy",
                event_key="run-legacy:started",
                type="agent_turn_started",
                run_id="run-legacy",
            ),
        )

        assert event.seq == 2
        events = await store.list_events("thread-legacy")
        assert [item.type for item in events] == [
            "message_created",
            "agent_turn_started",
        ]

    async def test_pending_approval_atomic_events(self, store: SessionStore) -> None:
        """put_pending_approval grava approval_requested e claim_pending_approval grava approval_resolved."""
        await store.create_session("thread-1", user_id="alice")

        await store.put_pending_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="bash",
            tool_call_id="tc-1",
            run_id="run-1",
            args={"cmd": "ls"},
            reasoning="Verificar arquivos",
        )

        events = await store.list_events("thread-1", after_seq=0)
        assert len(events) == 1
        assert events[0].type == "approval_requested"
        assert events[0].interrupt_id == "int-1"

        # Claim atômico
        claimed = await store.claim_pending_approval("thread-1", interrupt_id="int-1")
        assert claimed is not None
        assert claimed["interrupt_id"] == "int-1"
        assert claimed["run_id"] == "run-1"

        events_after = await store.list_events("thread-1", after_seq=1)
        assert len(events_after) == 1
        assert events_after[0].type == "approval_resolved"
        assert events_after[0].interrupt_id == "int-1"
        assert events_after[0].seq == 2

    async def test_read_cursor_monotone_and_capped(self, store: SessionStore) -> None:
        """mark_events_seen é limitado pelo MAX(seq) da thread e usa MAX(existing, seen_seq)."""
        await store.create_session("thread-1", user_id="alice")

        # Inserir 2 mensagens do assistente (seq 1 e 2)
        await store.append_message_recorded(
            "thread-1", text_message(MessageRole.ASSISTANT, "Msg 1")
        )
        await store.append_message_recorded(
            "thread-1", text_message(MessageRole.ASSISTANT, "Msg 2")
        )

        # Tentar marcar seq 50 (além do max seq da thread que é 2) -> deve limitar a 2
        res1 = await store.mark_events_seen(
            "thread-1", user_id="alice", device_id="dev-1", seen_seq=50
        )
        assert res1["read_seq"] == 2
        assert res1["unread_count"] == 0

        # Tentar retroceder cursor para 1 -> deve manter 2 (MAX(existing, seen_seq))
        res2 = await store.mark_events_seen(
            "thread-1", user_id="alice", device_id="dev-1", seen_seq=1
        )
        assert res2["read_seq"] == 2

    async def test_get_thread_summaries_and_badges(self, store: SessionStore) -> None:
        """get_thread_summaries deriva unread_count, status, last_message e last_seq."""
        await store.create_session("thread-1", user_id="alice")
        await store.create_session("thread-2", user_id="alice")

        # Thread 1: user msg + assistant msg -> unread_count = 1
        await store.append_message_recorded(
            "thread-1", text_message(MessageRole.USER, "Pergunta do usuário")
        )
        await store.append_message_recorded(
            "thread-1", text_message(MessageRole.ASSISTANT, "Resposta do assistente")
        )

        # Thread 2: run_failed -> unread_count = 1
        await store.append_event(
            "thread-2",
            ThreadLedgerEvent(
                thread_id="thread-2",
                event_key=make_run_failed_key("run-err"),
                type="run_failed",
                run_id="run-err",
                payload={"error": "LLM timeout"},
            ),
        )

        summaries = await store.get_thread_summaries(
            ["thread-1", "thread-2"], user_id="alice"
        )
        assert len(summaries) == 2

        s1 = next(s for s in summaries if s.thread_id == "thread-1")
        assert s1.unread_count == 1
        assert s1.last_seq == 2
        assert s1.last_message is not None
        assert s1.last_message.role == "assistant"
        assert s1.last_message.preview == "Resposta do assistente"

        s2 = next(s for s in summaries if s.thread_id == "thread-2")
        assert s2.unread_count == 1
        assert s2.status == "failed"

    async def test_run_draft_crud(self, store: SessionStore) -> None:
        """upsert, get e delete de rascunhos de run."""
        await store.create_session("thread-1", user_id="alice")

        await store.upsert_run_draft("thread-1", "run-1", content="Parcial...")
        draft = await store.get_run_draft("thread-1", "run-1")
        assert draft is not None
        assert draft.content == "Parcial..."

        await store.upsert_run_draft("thread-1", "run-1", content="Parcial atualizado")
        draft2 = await store.get_run_draft("thread-1", "run-1")
        assert draft2 is not None
        assert draft2.content == "Parcial atualizado"

        await store.delete_run_draft("thread-1", "run-1")
        draft3 = await store.get_run_draft("thread-1", "run-1")
        assert draft3 is None

    async def test_delete_session_cleans_ledger(self, store: SessionStore) -> None:
        """delete_session apaga eventos, cursores e rascunhos da thread."""
        await store.create_session("thread-1", user_id="alice")
        await store.append_message_recorded(
            "thread-1", text_message(MessageRole.USER, "Oi")
        )
        await store.mark_events_seen("thread-1", "alice", "dev-1", 1)
        await store.upsert_run_draft("thread-1", "run-1", content="rascunho")

        await store.delete_session("thread-1")

        events = await store.list_events("thread-1")
        assert len(events) == 0

        draft = await store.get_run_draft("thread-1", "run-1")
        assert draft is None

        summaries = await store.get_thread_summaries(["thread-1"], user_id="alice")
        assert summaries[0].last_seq == 0
        assert summaries[0].unread_count == 0
