import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from backend.services.storage_maintenance import run_storage_maintenance


def _old_artifact(path: Path) -> None:
    path.write_text("old", encoding="utf-8")
    timestamp = (datetime.now(UTC) - timedelta(days=60)).timestamp()
    import os

    os.utime(path, (timestamp, timestamp))
    path.with_suffix(".artifact.json").write_text(
        '{"retention_class":"run"}', encoding="utf-8"
    )


async def test_maintenance_runs_sqlite_pragmas_and_dry_run_gc(tmp_path: Path) -> None:
    database = tmp_path / "data" / "vectora.db"
    database.parent.mkdir()
    async with aiosqlite.connect(database) as connection:
        await connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        await connection.commit()
    artifact_root = tmp_path / "artifacts" / "thread"
    artifact_root.mkdir(parents=True)
    artifact = artifact_root / "old.md"
    _old_artifact(artifact)

    result = await run_storage_maintenance(
        database, artifact_root=tmp_path / "artifacts", artifact_dry_run=True
    )

    assert result.optimized is True
    assert result.wal_checkpoint
    assert result.artifact_gc.eligible == 1
    assert result.artifact_gc.deleted == 0
    assert artifact.exists()


async def test_maintenance_runs_incremental_vacuum_when_enabled(tmp_path: Path) -> None:
    database = tmp_path / "vectora.db"
    async with aiosqlite.connect(database) as connection:
        await connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        await connection.commit()

    result = await run_storage_maintenance(database)

    assert result.incremental_vacuum is True


async def test_maintenance_cancellation_stops_artifact_worker(
    tmp_path: Path, monkeypatch
) -> None:
    import backend.services.storage_maintenance as maintenance_module
    from backend.services.artifact_retention import ArtifactGcResult

    started = threading.Event()
    stopped = threading.Event()

    def slow_collect(*args, stop_event=None, **kwargs):
        started.set()
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.005)
        stopped.set()
        return ArtifactGcResult(stopped=True)

    monkeypatch.setattr(maintenance_module, "collect_artifacts", slow_collect)
    database = tmp_path / "vectora.db"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    stop_event = threading.Event()
    task = asyncio.create_task(
        run_storage_maintenance(
            database, artifact_root=artifact_root, stop_event=stop_event
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stop_event.is_set()
    assert stopped.wait(2)
