"""Maintenance cycle for local storage outside the agent critical path."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
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


async def run_storage_maintenance(
    db_path: str | Path,
    *,
    artifact_root: str | Path | None = None,
    referenced_artifacts: tuple[str | Path, ...] = (),
    artifact_dry_run: bool = True,
    stop_event: threading.Event | None = None,
) -> StorageMaintenanceResult:
    """Run one bounded maintenance batch.

    The shared ``storage_operation`` gate keeps maintenance coordinated with
    backup/restore and other state-file operations. The GC remains dry-run by
    default and therefore cannot delete user data unless explicitly enabled.
    """
    db = Path(db_path)
    wal_checkpoint = ""
    optimized = False
    incremental_vacuum = False
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
    )


__all__ = ["StorageMaintenanceResult", "run_storage_maintenance"]
