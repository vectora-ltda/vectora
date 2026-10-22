"""Opt-in local benchmark for the native SQLite transcript path."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class StorageBenchmarkResult:
    """Measurements for one synthetic transcript size."""

    messages: int
    append_seconds: float
    open_seconds: float
    page_seconds: float
    database_bytes: int


async def benchmark_sqlite(
    path: str | Path,
    *,
    sizes: tuple[int, ...] = (10, 100, 1_000, 10_000),
    payload_bytes: int = 256,
) -> list[dict[str, float | int]]:
    """Measure append, reopen and cursor-page costs without external APIs.

    The benchmark is opt-in and writes only to ``path``. It uses a synthetic
    table so the result is useful for comparing storage settings without
    fabricating provider or model performance.
    """
    if any(size <= 0 for size in sizes):
        raise ValueError("sizes devem ser positivos")
    if payload_bytes < 1:
        raise ValueError("payload_bytes deve ser positivo")
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, float | int]] = []
    async with aiosqlite.connect(database) as connection:
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS benchmark_messages "
            "(id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        await connection.commit()

    for size in sizes:
        thread_id = f"benchmark-{size}"
        payload = "x" * payload_bytes
        append_started = time.perf_counter()
        async with aiosqlite.connect(database) as connection:
            await connection.executemany(
                "INSERT INTO benchmark_messages(thread_id, payload) VALUES (?, ?)",
                ((thread_id, payload) for _ in range(size)),
            )
            await connection.commit()
        append_seconds = time.perf_counter() - append_started

        open_started = time.perf_counter()
        async with aiosqlite.connect(database) as connection:
            await connection.execute("SELECT 1")
        open_seconds = time.perf_counter() - open_started

        page_started = time.perf_counter()
        async with aiosqlite.connect(database) as connection:
            cursor = await connection.execute(
                "SELECT id, payload FROM benchmark_messages "
                "WHERE thread_id = ? ORDER BY id LIMIT 100",
                (thread_id,),
            )
            await cursor.fetchall()
        page_seconds = time.perf_counter() - page_started
        results.append(
            asdict(
                StorageBenchmarkResult(
                    messages=size,
                    append_seconds=append_seconds,
                    open_seconds=open_seconds,
                    page_seconds=page_seconds,
                    database_bytes=database.stat().st_size,
                )
            )
        )
    return results


__all__ = ["StorageBenchmarkResult", "benchmark_sqlite"]
