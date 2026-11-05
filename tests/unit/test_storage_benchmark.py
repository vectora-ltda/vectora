from pathlib import Path

import pytest

from backend.storage.benchmark import benchmark_sqlite


@pytest.mark.benchmark
async def test_storage_benchmark_reports_requested_sizes(tmp_path: Path) -> None:
    """Verifica o comportamento de storage benchmark reports requested sizes."""
    results = await benchmark_sqlite(
        tmp_path / "benchmark.db", sizes=(10, 100), payload_bytes=32
    )

    assert [item["messages"] for item in results] == [10, 100]
    assert all(item["append_seconds"] >= 0 for item in results)
    assert all(item["open_seconds"] >= 0 for item in results)
    assert all(item["page_seconds"] >= 0 for item in results)
    assert results[-1]["database_bytes"] >= results[0]["database_bytes"]
