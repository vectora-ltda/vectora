"""Persistência mínima e agregação do uso de tools por usuário."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.settings import settings
from backend.storage.sqlite.pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_sqlite_pools: dict[str, AsyncConnectionPool] = {}


async def _sqlite() -> AsyncConnectionPool:
    """Retorna o pool SQLite compartilhado para o banco de uso atual.

    O pool aplica WAL e ``busy_timeout`` em todas as conexões, evitando que
    gravações paralelas do lote de tools sejam descartadas por ``SQLITE_BUSY``.
    O cache é indexado pelo caminho para manter testes e bancos configuráveis
    isolados no mesmo processo.
    """
    path = settings.db_dsn or str(settings.vectora_home / "data" / "backend.db")
    pool = _sqlite_pools.get(path)
    if pool is None:
        pool = AsyncConnectionPool(path, min_size=1, max_size=4)
        await pool.open()
        _sqlite_pools[path] = pool

    async with pool.acquire() as conn:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS tool_usage_events (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, tool_name TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_usage_user_time_name "
            "ON tool_usage_events(user_id, created_at, tool_name)"
        )
        await conn.commit()
    return pool


async def record_tool_usage(user_id: str, tool_name: str, status: str) -> None:
    """Registra somente a conclusão de uma execução efetiva, sem argumentos."""
    now = datetime.now(UTC).isoformat()
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    try:
        from backend.services.license import get_effective_storage_mode

        if get_effective_storage_mode() == "complete" and settings.postgres_dsn:
            from backend.storage.factory import get_pg_pool

            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM tool_usage_events WHERE created_at < $1", cutoff
                )
                await conn.execute(
                    "INSERT INTO tool_usage_events "
                    "(id,user_id,tool_name,status,created_at) VALUES ($1,$2,$3,$4,$5)",
                    str(uuid4()),
                    user_id or "local",
                    tool_name,
                    status,
                    now,
                )
            return
        pool = await _sqlite()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM tool_usage_events WHERE created_at < ?", (cutoff,)
            )
            await conn.execute(
                "INSERT INTO tool_usage_events "
                "(id,user_id,tool_name,status,created_at) VALUES (?,?,?,?,?)",
                (str(uuid4()), user_id or "local", tool_name, status, now),
            )
            await conn.commit()
    except Exception:
        logger.exception(
            "tool_usage: falha ao registrar execução", extra={"tool": tool_name}
        )


async def aggregate_last_7d(
    user_id: str, *, now: datetime | None = None
) -> dict[str, int]:
    """Agrega execuções concluídas na janela móvel de sete dias."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=7)
    try:
        from backend.services.license import get_effective_storage_mode

        if get_effective_storage_mode() == "complete" and settings.postgres_dsn:
            from backend.storage.factory import get_pg_pool

            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT tool_name, COUNT(*) AS count FROM tool_usage_events "
                    "WHERE user_id=$1 AND created_at >= $2 GROUP BY tool_name",
                    user_id or "local",
                    cutoff,
                )
            return {str(row["tool_name"]): int(row["count"]) for row in rows}
        pool = await _sqlite()
        async with pool.acquire() as conn:
            cursor = await conn.execute(
                "SELECT tool_name, COUNT(*) AS count FROM tool_usage_events "
                "WHERE user_id=? AND created_at >= ? GROUP BY tool_name",
                (user_id or "local", cutoff.isoformat()),
            )
            rows = await cursor.fetchall()
            return {str(row[0]): int(row[1]) for row in rows}
    except Exception:
        logger.exception(
            "tool_usage: falha ao agregar execução", extra={"user_id": user_id}
        )
        raise
