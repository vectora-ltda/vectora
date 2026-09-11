"""Fixtures compartilhadas para testes de integração — session 1212.

Todos os testes de integração usam a session 1212 como thread_id fixo.
A fixture `integration_cleanup` limpa os dados desta session antes da suite.
"""

from __future__ import annotations

import asyncio
import logging
import os

import pytest

logger = logging.getLogger(__name__)

# ============================================================================
# Constants exportadas — use em test_mcp_tools.py
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
# Limpeza da session 1212 — roda UMA VEZ antes da suite de integração
# ============================================================================


async def _cleanup_session_1212() -> None:
    """Remove dados de teste da session 1212: checkpoints + LanceDB + traces."""
    from backend.settings import settings

    # 1. Limpa checkpoints SQLite legados (tabelas checkpoints + writes, se existirem)
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

    # 2. Limpa coleção LanceDB de teste
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

    # 3. Limpa traces da session 1212
    try:
        from backend.persistence.tracer import tracer

        removed = await tracer.clear_session(TEST_SESSION_ID)
        logger.info(f"Traces da session 1212 removidos: {removed}")
    except Exception as e:
        logger.warning(f"Não foi possível limpar traces: {e}")


@pytest.fixture(scope="session", autouse=False)
def integration_cleanup() -> None:  # type: ignore[return]
    """Limpeza síncrona (scope=session) — chame explicitamente nos testes de integração.

    Usa asyncio.run() para não conflitar com o event loop do pytest-asyncio.
    """
    asyncio.run(_cleanup_session_1212())


# ============================================================================
# Helpers exportados — importáveis nos arquivos de teste
# ============================================================================


# ============================================================================
# Storage integration fixtures — Postgres, Redis, Qdrant
# ============================================================================


@pytest.fixture(scope="session")
def _storage_stack_ok() -> bool:
    """True se Postgres (5432), Redis (6379) e Qdrant (6333) respondem na porta."""
    import socket

    from backend.storage.dev_stack import _docker_available, stack_up

    if _docker_available() and not os.getenv("CI"):
        stack_up()  # best-effort: sobe se parado, ignora erros

    for port in (5432, 6379, 6333):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                pass
        except OSError:
            return False
    return True


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    from backend.storage.dev_stack import DEFAULT_POSTGRES_DSN

    return os.getenv(
        "VECTORA_TEST_POSTGRES_DSN",
        DEFAULT_POSTGRES_DSN.replace("postgresql+asyncpg://", "postgresql://"),
    )


@pytest.fixture(scope="session")
def redis_url() -> str:
    from backend.storage.dev_stack import DEFAULT_REDIS_URL

    return os.getenv("VECTORA_TEST_REDIS_URL", DEFAULT_REDIS_URL)


@pytest.fixture(scope="session")
def qdrant_url() -> str:
    from backend.storage.dev_stack import DEFAULT_QDRANT_URL

    return os.getenv("VECTORA_TEST_QDRANT_URL", DEFAULT_QDRANT_URL)


@pytest.fixture
async def pg_pool(_storage_stack_ok: bool, pg_dsn: str):
    """Pool asyncpg para testes de integração. Skip se Postgres indisponível."""
    if not _storage_stack_ok:
        pytest.skip("Docker indisponível — Postgres não iniciado")

    import asyncio

    import asyncpg

    pool = None
    for attempt in range(15):
        try:
            pool = await asyncpg.create_pool(
                pg_dsn, min_size=1, max_size=3, command_timeout=10
            )
            break
        except Exception:
            if attempt == 14:
                pytest.skip("Postgres não respondeu após 15 tentativas")
            await asyncio.sleep(1)

    assert pool is not None
    yield pool
    await pool.close()


@pytest.fixture
async def pg_conn(pg_pool):
    """Conexão asyncpg isolada por teste.

    Envolve o teste numa transação que é sempre revertida no fim — DDL é
    transacional no Postgres, então migrations aplicadas dentro do teste
    (CREATE TABLE, etc.) nunca vazam pro próximo teste via o container
    compartilhado (que persiste entre execuções, já que ``stack_up()``
    reaproveita o volume nomeado em vez de recriar o banco do zero).
    """
    async with pg_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest.fixture
async def redis_client(_storage_stack_ok: bool, redis_url: str):
    """Cliente redis.asyncio para testes de integração. Skip se Redis indisponível."""
    if not _storage_stack_ok:
        pytest.skip("Docker indisponível — Redis não iniciado")

    import asyncio

    import redis.asyncio as aredis

    client = aredis.from_url(redis_url)
    for attempt in range(15):
        try:
            await client.ping()  # redis stub retorna Awaitable[bool]|bool
            break
        except Exception:
            if attempt == 14:
                await client.aclose()
                pytest.skip("Redis não respondeu após 15 tentativas")
            await asyncio.sleep(1)

    yield client
    await client.aclose()


@pytest.fixture
def qdrant_client(_storage_stack_ok: bool, qdrant_url: str):
    """QdrantClient síncrono para testes de integração. Skip se Qdrant indisponível."""
    if not _storage_stack_ok:
        pytest.skip("Docker indisponível — Qdrant não iniciado")

    import time

    from qdrant_client import QdrantClient

    client = QdrantClient(url=qdrant_url, api_key="vectora", timeout=10)
    for attempt in range(15):
        try:
            client.get_collections()
            break
        except Exception:
            if attempt == 14:
                pytest.skip("Qdrant não respondeu após 15 tentativas")
            time.sleep(1)

    return client


# ============================================================================
# Helpers exportados — importáveis nos arquivos de teste
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
