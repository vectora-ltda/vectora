"""Fixtures compartilhadas para testes de stress — infra local, sem APIs externas.

Constantes da session 1212 e helpers de limpeza/embedding direto reusados pela
suite de stress (filas, contagem de tokens, concorrência).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

logger = logging.getLogger(__name__)

# ============================================================================
# Constants exportadas
# ============================================================================

TEST_THREAD_ID = "1212"
TEST_SESSION_ID = 1212
TEST_COLLECTION = "test_lifecycle_1212"

# Texto determinístico — sempre o mesmo para garantir reprodutibilidade
KNOWN_TEXT = (
    "O Vectora usa LanceDB como banco vetorial e SQLite para checkpoints de sessão."
)
KNOWN_KEYWORD = "LanceDB banco vetorial"


# ============================================================================
# Limpeza da session 1212
# ============================================================================


async def _cleanup_session_1212() -> None:
    """Remove dados de teste da session 1212: checkpoints + LanceDB + traces."""
    from backend.settings import settings

    try:
        import aiosqlite

        if settings.db_dsn is None:
            raise RuntimeError("db_dsn not configured")
        async with aiosqlite.connect(settings.db_dsn) as db:
            await db.execute(
                "DELETE FROM checkpoints WHERE thread_id=?", (TEST_THREAD_ID,)
            )
            await db.execute("DELETE FROM writes WHERE thread_id=?", (TEST_THREAD_ID,))
            await db.commit()
        logger.info("Checkpoints da session 1212 limpos")
    except Exception as e:
        logger.warning(f"Não foi possível limpar checkpoints: {e}")

    try:
        import lancedb

        from backend.settings import settings as s

        db_lance = await lancedb.connect_async(str(s.lancedb_dir))
        tables = (await db_lance.list_tables()).tables
        if TEST_COLLECTION in tables:
            await db_lance.drop_table(TEST_COLLECTION)
            logger.info(f"Coleção LanceDB '{TEST_COLLECTION}' removida")
    except Exception as e:
        logger.warning(f"Não foi possível limpar LanceDB: {e}")

    try:
        from backend.persistence.tracer import tracer

        removed = await tracer.clear_session(TEST_SESSION_ID)
        logger.info(f"Traces da session 1212 removidos: {removed}")
    except Exception as e:
        logger.warning(f"Não foi possível limpar traces: {e}")


@pytest.fixture(scope="session", autouse=False)
def integration_cleanup() -> None:  # type: ignore[return]
    """Limpeza síncrona (scope=session) — chame explicitamente nos testes."""
    asyncio.run(_cleanup_session_1212())


# ============================================================================
# Helpers exportados
# ============================================================================


async def embed_direct(text: str, collection: str) -> None:
    """Embeda texto diretamente no LanceDB (bypass da fila — só para testes).

    Usa BackgroundEmbeddingWorker internamente para garantir o mesmo schema
    que o sistema real usa em produção.
    """
    from uuid import uuid4

    from backend.embedding.background import BackgroundEmbeddingWorker
    from backend.embedding.queue import EmbeddingQueueRecord

    worker = BackgroundEmbeddingWorker()
    vector = await worker._generate_embedding(text)

    record = EmbeddingQueueRecord(
        queue_id=str(uuid4()),
        text=text,
        collection=collection,
        doc_metadata="{}",
    )
    await worker._write_to_vector_store(record, vector)
