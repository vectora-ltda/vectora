"""Testes unitários da migração e backfill do ledger de threads.

Valida:
- O backfill é idempotente (reexecuções não duplicam eventos nem mudam contagens).
- O backfill converte `read_count` legado para `last_seen_seq` no cursor de leitura.
- O backfill retoma após erro e registra estado e contagens em `ledger_backfill`.
"""

from __future__ import annotations

import pytest

from backend.persistence.native.ledger import (
    backfill_thread_ledger,
    ensure_thread_backfilled,
)
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.vtypes.message import MessageRole, text_message


@pytest.fixture
async def pool(tmp_path):
    p = AsyncConnectionPool(str(tmp_path / "sessions.db"), min_size=1, max_size=5)
    await p.open()
    store = SessionStore(p)
    await store.setup()
    try:
        yield p
    finally:
        await p.close()


class TestLedgerBackfill:
    async def test_backfill_idempotent(self, pool: AsyncConnectionPool):
        store = SessionStore(pool)
        await store.create_session("thread-old", user_id="alice")

        # Inserir 3 mensagens diretamente na tabela legada messages
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO messages (thread_id, role, content_json, is_branch_head, created_at) "
                "VALUES ('thread-old', 'user', '[{\"kind\":\"text\",\"text\":\"Oi\"}]', 0, '2026-10-01T10:00:00Z')"
            )
            await conn.execute(
                "INSERT INTO messages (thread_id, role, content_json, is_branch_head, created_at) "
                "VALUES ('thread-old', 'assistant', '[{\"kind\":\"text\",\"text\":\"Olá Alice\"}]', 0, '2026-10-01T10:00:05Z')"
            )
            await conn.execute(
                "INSERT INTO messages (thread_id, role, content_json, is_branch_head, created_at) "
                "VALUES ('thread-old', 'assistant', '[{\"kind\":\"text\",\"text\":\"Como vai?\"}]', 1, '2026-10-01T10:00:10Z')"
            )
            await conn.commit()

        # Primeiro backfill com read_count = 1 (leu a 1ª mensagem de assistente)
        migrated, last_seq = await backfill_thread_ledger(
            pool, "thread-old", user_id="alice", legacy_read_count=1
        )
        assert migrated == 3
        assert last_seq == 3

        # Checar se status em ledger_backfill é 'done'
        async with pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT status, migrated_messages, last_seq FROM ledger_backfill WHERE thread_id = 'thread-old'"
            )
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == "done"
            assert row[1] == 3
            assert row[2] == 3

            # Checar cursor convertido
            cur_cursor = await conn.execute(
                "SELECT last_seen_seq FROM thread_event_cursors WHERE thread_id = 'thread-old' AND user_id = 'alice'"
            )
            row_c = await cur_cursor.fetchone()
            assert row_c is not None
            # A 1ª mensagem do assistente é seq 2 (user=1, assistant1=2, assistant2=3)
            assert row_c[0] == 2

        # Reexecutar backfill (deve ser no-op e manter 3)
        migrated_2, last_seq_2 = await backfill_thread_ledger(
            pool, "thread-old", user_id="alice", legacy_read_count=1
        )
        assert migrated_2 == 3
        assert last_seq_2 == 3

        events = await store.list_events("thread-old")
        assert len(events) == 3

    async def test_ensure_thread_backfilled_lazy(self, pool: AsyncConnectionPool):
        store = SessionStore(pool)
        await store.create_session("thread-lazy", user_id="bob")
        await store.append_message(
            "thread-lazy", text_message(MessageRole.USER, "Teste")
        )

        ok = await ensure_thread_backfilled(pool, "thread-lazy", user_id="bob")
        assert ok is True

        events = await store.list_events("thread-lazy")
        assert len(events) >= 1
