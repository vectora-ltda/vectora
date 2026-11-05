import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from backend.services.storage_maintenance import run_storage_maintenance


def _old_artifact(path: Path) -> None:
    """Executa a operação  old artifact."""
    path.write_text("old", encoding="utf-8")
    timestamp = (datetime.now(UTC) - timedelta(days=60)).timestamp()
    import os

    os.utime(path, (timestamp, timestamp))
    path.with_suffix(".artifact.json").write_text(
        '{"retention_class":"run"}', encoding="utf-8"
    )


async def test_maintenance_runs_sqlite_pragmas_and_dry_run_gc(tmp_path: Path) -> None:
    """Verifica o comportamento de maintenance runs sqlite pragmas and dry run gc."""
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
    """Verifica o comportamento de maintenance runs incremental vacuum when enabled."""
    database = tmp_path / "vectora.db"
    async with aiosqlite.connect(database) as connection:
        await connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        await connection.commit()

    result = await run_storage_maintenance(database)

    assert result.incremental_vacuum is True
    assert result.context_records == {"projections": 0, "checkpoints": 0}
    assert result.memory_expired == 0
    assert result.memory_purged == 0


async def test_maintenance_rejeita_retencao_invalida(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="context_retention_days"):
        await run_storage_maintenance(tmp_path / "vectora.db", context_retention_days=0)


async def test_maintenance_cancellation_stops_artifact_worker(
    tmp_path: Path, monkeypatch
) -> None:
    """Verifica o comportamento de maintenance cancellation stops artifact worker."""
    import backend.services.storage_maintenance as maintenance_module
    from backend.services.artifact_retention import ArtifactGcResult

    started = threading.Event()
    stopped = threading.Event()

    def slow_collect(*args, stop_event=None, **kwargs):
        """Executa a operação slow collect."""
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


async def test_maintenance_so_cria_schema_de_sessoes_no_banco_de_sessoes(
    tmp_path: Path,
) -> None:
    """Outro banco (``vectora.db``) não recebe as tabelas de sessão; o banco
    ``sessions.db`` recebe o schema e tem os registros antigos de contexto purgados."""

    async def tables(path: Path) -> set[str]:
        async with aiosqlite.connect(path) as connection:
            cursor = await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            return {str(row[0]) for row in await cursor.fetchall()}

    other = tmp_path / "vectora.db"
    async with aiosqlite.connect(other) as connection:
        await connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        await connection.commit()
    await run_storage_maintenance(other)
    assert await tables(other) == {"sample"}

    sessions = tmp_path / "sessions.db"
    async with aiosqlite.connect(sessions) as connection:
        await connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        await connection.commit()
    result = await run_storage_maintenance(sessions)
    assert {"sessions", "messages"} <= await tables(sessions)
    assert result.context_records == {"projections": 0, "checkpoints": 0}
