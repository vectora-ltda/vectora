from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.services.artifact_retention import collect_artifacts


def _artifact(path: Path, *, retention_class: str = "run", age_days: int = 60) -> None:
    path.write_text("payload", encoding="utf-8")
    timestamp = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
    import os

    os.utime(path, (timestamp, timestamp))
    path.with_suffix(".artifact.json").write_text(
        f'{{"retention_class": "{retention_class}"}}',
        encoding="utf-8",
    )
    path.with_suffix(".artifact_type").write_text("plan", encoding="utf-8")


def test_gc_dry_run_reports_old_unreferenced_without_deleting(tmp_path: Path) -> None:
    artifact = tmp_path / "old.md"
    _artifact(artifact)

    result = collect_artifacts(tmp_path, now=datetime.now(UTC), dry_run=True)

    assert result.scanned == 1
    assert result.eligible == 1
    assert result.deleted == 0
    assert artifact.exists()
    assert artifact.with_suffix(".artifact.json").exists()


def test_gc_preserves_referenced_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "referenced.md"
    _artifact(artifact)

    result = collect_artifacts(tmp_path, referenced=[artifact], dry_run=False)

    assert result.skipped_referenced == 1
    assert result.deleted == 0
    assert artifact.exists()


def test_gc_deletes_payload_and_sidecars(tmp_path: Path) -> None:
    artifact = tmp_path / "old.md"
    _artifact(artifact)

    result = collect_artifacts(tmp_path, dry_run=False)

    assert result.deleted == 1
    assert result.bytes_freed == len("payload")
    assert not artifact.exists()
    assert not artifact.with_suffix(".artifact.json").exists()
    assert not artifact.with_suffix(".artifact_type").exists()


def test_gc_keeps_recent_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "recent.md"
    _artifact(artifact, age_days=1)

    result = collect_artifacts(
        tmp_path,
        older_than=datetime.now(UTC) - timedelta(days=7),
        dry_run=False,
    )

    assert result.skipped_recent == 1
    assert result.deleted == 0
    assert artifact.exists()


def test_gc_preserves_pinned_artifact_and_understands_file_uri(tmp_path: Path) -> None:
    artifact = tmp_path / "pinned.md"
    _artifact(artifact, retention_class="pinned")
    referenced = tmp_path / "referenced.md"
    _artifact(referenced)

    result = collect_artifacts(
        tmp_path,
        referenced=[referenced.as_uri()],
        dry_run=False,
    )

    assert result.skipped_referenced == 1
    assert result.skipped_policy == 1
    assert artifact.exists()
    assert referenced.exists()


def test_gc_skips_unknown_retention_class(tmp_path: Path) -> None:
    artifact = tmp_path / "unknown.md"
    _artifact(artifact, retention_class="future-policy")

    result = collect_artifacts(tmp_path, dry_run=False)

    assert result.skipped_policy == 1
    assert result.deleted == 0
    assert artifact.exists()
