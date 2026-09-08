"""Contrato da atividade remota de threads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from backend.persistence import thread_activity


async def _false() -> bool:
    return False


@pytest.fixture
async def activity_db(tmp_path, monkeypatch):
    from backend.settings import settings

    db_path = tmp_path / "activity.db"
    monkeypatch.setattr(settings, "db_dsn", str(db_path))
    monkeypatch.setattr(thread_activity, "_use_postgres", _false)
    return db_path


async def test_activity_is_idempotent_and_excludes_current_device(activity_db) -> None:
    await thread_activity.record_activity("alice", "vdev_a", "thread-1")
    await thread_activity.record_activity("alice", "vdev_b", "thread-1")

    remote = await thread_activity.get_remote_activity("alice", ["thread-1"], "vdev_a")

    assert set(remote) == {"thread-1"}
    assert remote["thread-1"].endswith("+00:00")

    async with aiosqlite.connect(activity_db) as db:
        rows = await db.execute_fetchall(
            "SELECT user_id, device_id, thread_id FROM vectora_thread_activity"
        )
    assert rows == [("alice", "vdev_a", "thread-1"), ("alice", "vdev_b", "thread-1")]


async def test_activity_isolated_by_user_and_cutoff(activity_db) -> None:
    await thread_activity.record_activity("alice", "vdev_b", "thread-1")
    await thread_activity.record_activity("bob", "vdev_b", "thread-1")

    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    async with aiosqlite.connect(activity_db) as db:
        await db.execute(
            "UPDATE vectora_thread_activity SET last_active_at = ? WHERE user_id = ?",
            (old, "alice"),
        )
        await db.commit()

    assert (
        await thread_activity.get_remote_activity("alice", ["thread-1"], "vdev_a") == {}
    )
    assert await thread_activity.get_remote_activity("bob", ["thread-1"], "vdev_a")
