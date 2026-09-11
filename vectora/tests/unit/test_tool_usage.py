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


@pytest.mark.asyncio
async def test_record_prunes_events_older_than_seven_days(usage_db: Path) -> None:
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    async with aiosqlite.connect(usage_db) as db:
        await db.execute(
            "CREATE TABLE tool_usage_events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, tool_name TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO tool_usage_events VALUES (?, ?, ?, ?, ?)",
            ("expired", "alice", "file_read", "ok", old),
        )
        await db.commit()

    await tool_usage.record_tool_usage("alice", "file_write", "ok")

    async with aiosqlite.connect(usage_db) as db:
        rows = list(await db.execute_fetchall("SELECT id FROM tool_usage_events"))
    assert len(rows) == 1
    assert rows[0][0] != "expired"


@pytest.mark.asyncio
async def test_record_postgres_usa_datetime_para_timestamptz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.rows: list[dict[str, object]] = [
                {
                    "tool_name": "expired",
                    "user_id": "alice",
                    "status": "ok",
                    "created_at": datetime.now(UTC) - timedelta(days=8),
                }
            ]

        async def execute(self, query: str, *args: object) -> None:
            self.calls.append((query, args))
            if query.startswith("DELETE"):
                cutoff = args[0]
                assert isinstance(cutoff, datetime)
                self.rows = [
                    row
                    for row in self.rows
                    if isinstance(row["created_at"], datetime)
                    and row["created_at"] >= cutoff
                ]
            elif query.startswith("INSERT"):
                self.rows.append(
                    {
                        "tool_name": args[2],
                        "user_id": args[1],
                        "status": args[3],
                        "created_at": args[4],
                    }
                )

        async def fetch(
            self, _query: str, user_id: str, cutoff: datetime
        ) -> list[dict[str, object]]:
            active = [
                row
                for row in self.rows
                if row["user_id"] == user_id
                and isinstance(row["created_at"], datetime)
                and row["created_at"] >= cutoff
            ]
            return [
                {
                    "tool_name": "file_read",
                    "count": sum(row["tool_name"] == "file_read" for row in active),
                }
            ]

    class Acquire:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self.connection

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakePool:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        def acquire(self) -> Acquire:
            return Acquire(self.connection)

    from backend.settings import settings

    connection = FakeConnection()
    monkeypatch.setattr(
        "backend.services.license.get_effective_storage_mode", lambda: "complete"
    )
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql://test")

    async def get_pool() -> FakePool:
        return FakePool(connection)

    monkeypatch.setattr("backend.storage.factory.get_pg_pool", get_pool)

    await tool_usage.record_tool_usage("alice", "file_read", "ok")

    assert len(connection.calls) == 2
    assert isinstance(connection.calls[0][1][0], datetime)
    assert isinstance(connection.calls[1][1][4], datetime)
    assert await tool_usage.aggregate_last_7d("alice") == {"file_read": 1}
    assert all(row["tool_name"] != "expired" for row in connection.rows)
