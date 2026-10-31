"""Maintenance cycle for local storage outside the agent critical path."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from backend.services.artifact_retention import ArtifactGcResult, collect_artifacts
from backend.services.maintenance import storage_operation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageMaintenanceResult:
    """Observable result of one maintenance batch."""

    wal_checkpoint: str
    optimized: bool
    incremental_vacuum: bool
    artifact_gc: ArtifactGcResult
    context_records: dict[str, int]
    memory_expired: int
    memory_purged: int


SESSIONS_DB_NAME = "sessions.db"


async def run_storage_maintenance(
    db_path: str | Path,
    *,
    artifact_root: str | Path | None = None,
    referenced_artifacts: tuple[str | Path, ...] = (),
    artifact_dry_run: bool = True,
    context_retention_days: int = 30,
    stop_event: threading.Event | None = None,
) -> StorageMaintenanceResult:
    """Run one bounded maintenance batch.

    The shared ``storage_operation`` gate keeps maintenance coordinated with
    backup/restore and other state-file operations. The GC remains dry-run by
    default and therefore cannot delete user data unless explicitly enabled.
    """
    db = Path(db_path)
    if context_retention_days <= 0:
        raise ValueError("context_retention_days deve ser positivo")
    wal_checkpoint = ""
    optimized = False
    incremental_vacuum = False
    context_records = {"projections": 0, "checkpoints": 0}
    memory_expired = 0
    memory_purged = 0
    async with storage_operation():
        async with aiosqlite.connect(db) as connection:
            cursor = await connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            row = await cursor.fetchone()
            wal_checkpoint = ":".join(str(value) for value in (row or ()))
            await connection.execute("PRAGMA optimize")
            optimized = True
            cursor = await connection.execute("PRAGMA auto_vacuum")
            auto_vacuum = await cursor.fetchone()
            if auto_vacuum and int(auto_vacuum[0]) == 2:
                await connection.execute("PRAGMA incremental_vacuum")
                incremental_vacuum = True
            await connection.commit()

        # Expired memory records are filtered at read time, but must also be
        # physically removed during maintenance so retention is bounded for
        # both the SQLite and PostgreSQL store implementations.  Keep this
        # optional: maintenance can run before the provider factory is ready.
        try:
            from backend.storage.factory import get_store

            store = await get_store()
            purge = getattr(store, "purge_expired", None)
            if callable(purge):
                await purge()
        except Exception:
            logger.warning(
                "storage maintenance: failed to purge expired records", exc_info=True
            )
        try:
            from backend.services.memory_store import get_memory_store

            memory_store = get_memory_store()
            memory_expired = await asyncio.to_thread(memory_store.expire)
            cutoff = (
                datetime.now(UTC) - timedelta(days=context_retention_days)
            ).isoformat()
            memory_purged = await asyncio.to_thread(
                memory_store.purge_invalidated, before=cutoff
            )
        except Exception:
            logger.warning(
                "storage maintenance: failed to expire context memories",
                exc_info=True,
            )
        # ``SessionStore.setup()`` creates and migrates the session schema in the
        # database it opens, so it only runs against the sessions database.
        # Any other file (for example ``vectora.db``) is left untouched.
        if db.name != SESSIONS_DB_NAME:
            logger.info(
                "storage maintenance: %s is not the sessions database; "
                "context purge skipped",
                db.name,
            )
        else:
            try:
                from backend.persistence.native.session_store import SessionStore
                from backend.storage.sqlite.pool import AsyncConnectionPool

                # Maintenance must operate on the database selected by the
                # caller, never on the process-wide chat singleton.  This keeps
                # retention scoped when multiple workspaces/databases coexist.
                async with AsyncConnectionPool.from_path(
                    db, min_size=1, max_size=2
                ) as pool:
                    session_store = SessionStore(pool)
                    await session_store.setup()
                    purge_context = getattr(
                        session_store, "purge_context_records", None
                    )
                    if callable(purge_context):
                        cutoff = (
                            datetime.now(UTC) - timedelta(days=context_retention_days)
                        ).isoformat()
                        context_records = await purge_context(cutoff)
            except Exception:
                logger.warning(
                    "storage maintenance: failed to purge context projections",
                    exc_info=True,
                )

        root = (
            Path(artifact_root)
            if artifact_root is not None
            else db.parent.parent / "artifacts"
        )
        if root.is_dir():
            worker = asyncio.create_task(
                asyncio.to_thread(
                    collect_artifacts,
                    root,
                    referenced=referenced_artifacts,
                    dry_run=artifact_dry_run,
                    stop_event=stop_event,
                )
            )
            try:
                artifact_gc = await asyncio.shield(worker)
            except asyncio.CancelledError:
                if stop_event is not None:
                    stop_event.set()
                # Keep the storage gate held until the worker has observed the
                # cancellation request and returned its bounded result.
                await asyncio.shield(worker)
                raise
        else:
            artifact_gc = ArtifactGcResult()
    return StorageMaintenanceResult(
        wal_checkpoint=wal_checkpoint,
        optimized=optimized,
        incremental_vacuum=incremental_vacuum,
        artifact_gc=artifact_gc,
        context_records=context_records,
        memory_expired=memory_expired,
        memory_purged=memory_purged,
    )


__all__ = ["SESSIONS_DB_NAME", "StorageMaintenanceResult", "run_storage_maintenance"]
