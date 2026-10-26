from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.services.cold_storage import export_cold_archive, read_cold_archive


def test_cold_archive_round_trip_is_hashed_and_rehydratable(tmp_path: Path) -> None:
    """Verifica o comportamento de cold archive round trip is hashed and rehydratable."""
    archive = export_cold_archive(
        tmp_path / "cold",
        "thread-1",
        metadata={"title": "demo", "head_sequence": 3},
        transcript=[{"sequence": 1, "role": "user", "content": "Olá"}],
        events=[{"sequence": 2, "event_type": "message_created"}],
    )

    assert archive.path.exists()
    assert archive.record_count == 3
    records = read_cold_archive(
        archive.path, expected_sha256=archive.sha256, expected_thread_id="thread-1"
    )
    assert records[0]["kind"] == "metadata"
    value = records[1]["value"]
    assert isinstance(value, dict)
    assert value["sequence"] == 1


def test_cold_archive_rejects_tampering_and_wrong_thread(tmp_path: Path) -> None:
    """Verifica o comportamento de cold archive rejects tampering and wrong thread."""
    archive = export_cold_archive(
        tmp_path,
        "thread-2",
        metadata={},
        transcript=[{"sequence": 1}],
    )
    with pytest.raises(ValueError, match="sha256"):
        read_cold_archive(archive.path, expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="outra thread"):
        read_cold_archive(
            archive.path,
            expected_sha256=archive.sha256,
            expected_thread_id="thread-elsewhere",
        )


def test_cold_archive_rejects_path_traversal(tmp_path: Path) -> None:
    """Verifica o comportamento de cold archive rejects path traversal."""
    with pytest.raises(ValueError, match="thread_id"):
        export_cold_archive(tmp_path, "../escape", metadata={}, transcript=[])


def test_cold_archive_concurrent_exports_do_not_share_staging_files(
    tmp_path: Path,
) -> None:
    """Exports concorrentes publicam arquivos íntegros, sem staging compartilhado."""

    def export(index: int):
        return export_cold_archive(
            tmp_path,
            "same-thread",
            metadata={"writer": index},
            transcript=[{"sequence": index, "content": f"value-{index}"}],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        archives = list(executor.map(export, (1, 2)))

    for archive in archives:
        records = read_cold_archive(
            archive.path,
            expected_sha256=archive.sha256,
            expected_thread_id="same-thread",
        )
        assert len(records) == archive.record_count
