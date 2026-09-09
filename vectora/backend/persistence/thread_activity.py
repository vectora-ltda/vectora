"""Durable, privacy-preserving activity metadata for conversation threads.

Only the authenticated user, an opaque device id, the thread id and a
timestamp are stored.  Postgres is used when the complete storage backend is
available; SQLite provides a local fallback that never interrupts chat.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

REMOTE_ACTIVITY_WINDOW = timedelta(minutes=30)
ACTIVITY_RETENTION = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


async def _sqlite_connection() -> Any:
    import aiosqlite

    from backend.settings import settings

    path = settings.db_dsn
    if not path:
        raise RuntimeError("db_dsn não configurado")
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA busy_timeout=30000")
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS vectora_thread_activity (
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            last_active_at TEXT NOT NULL,
            PRIMARY KEY (user_id, device_id, thread_id)
        )"""
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thread_activity_user_thread "
        "ON vectora_thread_activity(user_id, thread_id)"
    )
    await conn.commit()
    return conn


async def _use_postgres() -> bool:
    from backend.services.license import get_effective_storage_mode

    return get_effective_storage_mode() == "complete"


async def record_activity(user_id: str, device_id: str, thread_id: str) -> None:
    """Record activity idempotently, degrading to local SQLite on failure."""
    stamp = _now()
    if await _use_postgres():
        try:
            from backend.storage.factory import get_pg_pool

            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO vectora_thread_activity
                    (user_id, device_id, thread_id, last_active_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, device_id, thread_id)
                    DO UPDATE SET last_active_at = GREATEST(
                        vectora_thread_activity.last_active_at,
                        EXCLUDED.last_active_at
                    )""",
                    user_id,
                    device_id,
                    thread_id,
                    stamp,
                )
            return
        except Exception:
            logger.warning("thread activity: Postgres indisponível; usando SQLite")

    try:
        conn = await _sqlite_connection()
        try:
            await conn.execute(
                """INSERT INTO vectora_thread_activity
                (user_id, device_id, thread_id, last_active_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, device_id, thread_id)
                DO UPDATE SET last_active_at = MAX(
                    vectora_thread_activity.last_active_at,
                    excluded.last_active_at
                )""",
                (user_id, device_id, thread_id, _iso(stamp)),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception:
        logger.warning("thread activity: falha ao persistir atividade", exc_info=True)


async def get_remote_activity(
    user_id: str,
    thread_ids: list[str],
    current_device_id: str,
    *,
    since: datetime | None = None,
) -> dict[str, str]:
    """Return the newest recent activity from other devices by thread."""
    if not thread_ids:
        return {}
    # Lite/offline installations have no shared source of truth. They still
    # record local activity for diagnostics, but never infer cross-device
    # presence from a machine-local database.
    if not await _use_postgres():
        return {}
    cutoff = since or (_now() - REMOTE_ACTIVITY_WINDOW)
    try:
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT thread_id, MAX(last_active_at) AS last_active_at
                FROM vectora_thread_activity
                WHERE user_id = $1 AND device_id <> $2
                  AND thread_id = ANY($3::text[]) AND last_active_at >= $4
                GROUP BY thread_id""",
                user_id,
                current_device_id,
                thread_ids,
                cutoff,
            )
        return {
            str(row["thread_id"]): row["last_active_at"].isoformat() for row in rows
        }
    except Exception:
        logger.warning("thread activity: consulta compartilhada indisponível")
        return {}


async def prune(before: datetime | None = None) -> None:
    """Delete old activity metadata without affecting conversation history."""
    cutoff = before or (_now() - ACTIVITY_RETENTION)
    try:
        if await _use_postgres():
            from backend.storage.factory import get_pg_pool

            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM vectora_thread_activity WHERE last_active_at < $1",
                    cutoff,
                )
            return
        conn = await _sqlite_connection()
        try:
            await conn.execute(
                "DELETE FROM vectora_thread_activity WHERE last_active_at < ?",
                (_iso(cutoff),),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception:
        logger.debug("thread activity: falha ao podar registros", exc_info=True)


async def revoke_device_activity(user_id: str, device_id: str) -> None:
    """Remove all activity records for one authenticated installation."""
    try:
        if await _use_postgres():
            from backend.storage.factory import get_pg_pool

            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM vectora_thread_activity WHERE user_id = $1 AND device_id = $2",
                    user_id,
                    device_id,
                )
            return
        conn = await _sqlite_connection()
        try:
            await conn.execute(
                "DELETE FROM vectora_thread_activity WHERE user_id = ? AND device_id = ?",
                (user_id, device_id),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception:
        logger.warning("thread activity: falha ao revogar dispositivo", exc_info=True)
