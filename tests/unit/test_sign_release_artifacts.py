"""Testa checksums dos artefatos de release sem credenciais GPG."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from utils.sign_release_artifacts import sign_release_artifacts


def test_writes_sorted_checksums_without_gpg_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "release-assets"
    artifacts.mkdir()
    (artifacts / "Vectora.zip").write_bytes(b"zip payload")
    (artifacts / "Vectora.deb").write_bytes(b"deb payload")
    (artifacts / "SHA256SUMS.old").write_text("stale", encoding="utf-8")
    (artifacts / "existing.asc").write_text("stale", encoding="utf-8")
    monkeypatch.delenv("GPG_PRIVATE_KEY", raising=False)

    sign_release_artifacts(artifacts)

    checksums = (artifacts / "SHA256SUMS").read_text(encoding="utf-8")
    assert checksums.splitlines() == [
        f"{hashlib.sha256(b'deb payload').hexdigest()}  Vectora.deb",
        f"{hashlib.sha256(b'zip payload').hexdigest()}  Vectora.zip",
    ]


def test_rejects_missing_artifact_directory(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Artifact directory does not exist"):
        sign_release_artifacts(tmp_path / "missing")


def test_rejects_empty_artifact_directory(tmp_path: Path) -> None:
    artifacts = tmp_path / "release-assets"
    artifacts.mkdir()

    with pytest.raises(SystemExit, match="No release artifacts found"):
        sign_release_artifacts(artifacts)
