"""ListThreads filtra threads vazias (message_count=0) e cleanup_empty_threads
apaga as antigas o suficiente — evita sessões fantasma na sidebar (resíduo
de threads nunca usadas).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from backend.api.handlers.threads import (
    ListThreadsRequest,
    _ensure_schema,
    cleanup_empty_threads,
    list_threads,
)


@pytest.fixture
async def mem_db():
    import aiosqlite

    db = await aiosqlite.connect(":memory:")
    await _ensure_schema(db)
    return db


@pytest.fixture(autouse=True)
def patch_get_db(mem_db):
    with patch(
        "backend.api.handlers.threads._get_db", new=AsyncMock(return_value=mem_db)
    ):
        yield mem_db


async def _insert_session(
    db, thread_id: str, message_count: int, created_at: str
) -> None:
    await db.execute(
        "INSERT INTO vectora_sessions "
        "(thread_id, created_at, last_activity, message_count, extra) "
        "VALUES (?, ?, ?, ?, ?)",
        (thread_id, created_at, created_at, message_count, "{}"),
    )
    await db.commit()


class TestListThreadsFiltersEmpty:
    @pytest.mark.asyncio
    async def test_omits_threads_with_zero_messages(self, mem_db):
        now = datetime.now(UTC).isoformat()
        await _insert_session(mem_db, "empty1", 0, now)
        await _insert_session(mem_db, "real1", 2, now)

        out = await list_threads(ListThreadsRequest(limit=50))

        ids = [t.id for t in out.threads]
        assert ids == ["real1"]

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_list(self, mem_db):
        out = await list_threads(ListThreadsRequest(limit=50))
        assert out.threads == []


class TestCleanupEmptyThreads:
    @pytest.mark.asyncio
    async def test_deletes_only_old_empty_threads(self, mem_db):
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        await _insert_session(mem_db, "ghost-old", 0, old)
        await _insert_session(mem_db, "ghost-recent", 0, recent)
        await _insert_session(mem_db, "real-old", 3, old)

        deleted = await cleanup_empty_threads(max_age_hours=1.0)

        async with mem_db.execute(
            "SELECT thread_id FROM vectora_sessions ORDER BY thread_id"
        ) as cur:
            remaining = {row[0] for row in await cur.fetchall()}

        assert deleted == 1
        assert remaining == {"ghost-recent", "real-old"}

    @pytest.mark.asyncio
    async def test_no_empty_threads_deletes_nothing(self, mem_db):
        now = datetime.now(UTC).isoformat()
        await _insert_session(mem_db, "real1", 1, now)

        deleted = await cleanup_empty_threads(max_age_hours=1.0)

        assert deleted == 0


class TestCleanupNuncaApagaThreadRealSemCheckpointLegado:
    """Regressão do bug real corrigido em 2026-08-30: cleanup_empty_threads
    tinha uma 2ª passada que apagava qualquer thread com message_count > 0
    e mais de 1h de idade sem registro na tabela LEGADA `checkpoints` (do
    grafo compilado antigo). O motor nativo (conversation_loop.py) nunca
    escreve nessa tabela — então TODA thread real virava alvo dessa
    limpeza, rodando a cada boot + a cada hora. Confirmado num banco de
    usuário real: vectora_sessions com só 2 linhas contra dezenas de
    threads reais e intactas em sessions.db (SessionStore). A thread
    "sumia" da sidebar sem nenhum erro visível, mesmo com a conversa
    inteira preservada na fonte de verdade.
    """

    @staticmethod
    async def _create_legacy_checkpoints_table(db: aiosqlite.Connection) -> None:
        # Simula um banco com resíduo do antigo AsyncSqliteSaver — a
        # tabela pode existir (migração incompleta/dado antigo) mesmo que
        # nada mais escreva nela.
        await db.execute(
            "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT)"
        )
        await db.commit()

    @pytest.mark.asyncio
    async def test_thread_real_antiga_sem_checkpoint_legado_nunca_e_apagada(
        self, mem_db: aiosqlite.Connection
    ) -> None:
        await self._create_legacy_checkpoints_table(mem_db)
        old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        await _insert_session(mem_db, "conversa-real-antiga", 82, old)

        deleted = await cleanup_empty_threads(max_age_hours=1.0)

        async with mem_db.execute("SELECT thread_id FROM vectora_sessions") as cur:
            remaining = {row[0] for row in await cur.fetchall()}
        assert deleted == 0
        assert remaining == {"conversa-real-antiga"}

    @pytest.mark.asyncio
    async def test_thread_real_sobrevive_mesmo_sem_a_tabela_checkpoints_existir(
        self, mem_db: aiosqlite.Connection
    ) -> None:
        # Banco sem resíduo nenhum do grafo antigo (instalação nova) —
        # continua não apagando threads reais, é o caso comum hoje.
        old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        await _insert_session(mem_db, "conversa-real-antiga", 5, old)

        deleted = await cleanup_empty_threads(max_age_hours=1.0)

        async with mem_db.execute("SELECT thread_id FROM vectora_sessions") as cur:
            remaining = {row[0] for row in await cur.fetchall()}
        assert deleted == 0
        assert remaining == {"conversa-real-antiga"}
