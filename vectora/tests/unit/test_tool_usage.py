"""Unit tests for privacy-safe tool usage aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from backend.services import tool_usage


@pytest.fixture
async def usage_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from backend.settings import settings

    db_path = tmp_path / "tool-usage.db"
    monkeypatch.setattr(settings, "db_dsn", str(db_path))
    monkeypatch.setattr(
        "backend.services.license.get_effective_storage_mode", lambda: "lite"
    )
    return db_path


@pytest.mark.asyncio
async def test_aggregate_uses_window_and_isolates_users(usage_db: Path) -> None:
    now = datetime.now(UTC)
    await tool_usage.record_tool_usage("alice", "file_read", "ok")
    await tool_usage.record_tool_usage("bob", "file_read", "ok")

    async with aiosqlite.connect(usage_db) as db:
        await db.execute(
            "INSERT INTO tool_usage_events VALUES (?, ?, ?, ?, ?)",
            (
                "expired",
                "alice",
                "file_read",
                "ok",
                (now - timedelta(days=8)).isoformat(),
            ),
        )
        await db.commit()

    assert await tool_usage.aggregate_last_7d("alice", now=now) == {"file_read": 1}


@pytest.mark.asyncio
async def test_usage_record_does_not_store_arguments_or_results(usage_db: Path) -> None:
    await tool_usage.record_tool_usage("alice", "terminal", "error")

    async with aiosqlite.connect(usage_db) as db:
        columns = list(
            await db.execute_fetchall("PRAGMA table_info(tool_usage_events)")
        )
        rows = list(await db.execute_fetchall("SELECT * FROM tool_usage_events"))

    assert {str(column[1]) for column in columns} == {
        "id",
        "user_id",
        "tool_name",
        "status",
        "created_at",
    }
    assert len(rows) == 1
    row = rows[0]
    assert row[1:] == ("alice", "terminal", "error", row[4])
