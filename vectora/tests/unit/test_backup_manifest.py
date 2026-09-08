"""Contrato de segurança do formato de backup local manifestado."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from backend.storage.backup_manifest import create_backup, inspect_backup


def test_create_and_inspect_preserva_manifesto_lite(tmp_path: Path) -> None:
    database = tmp_path / "vectora.db"
    database.write_bytes(b"sqlite-placeholder")
    archive = tmp_path / "backup.vbackup.zip"

    preview = create_backup(database, archive, app_version="1.0.0")

    assert preview.compatible is True
    assert preview.storage_mode == "lite"
    assert preview.categories == {"database": 1}
    assert inspect_backup(archive).size_bytes == archive.stat().st_size


def test_rejeita_backup_complete_antes_de_tocar_no_estado(tmp_path: Path) -> None:
    archive = tmp_path / "complete.zip"
    manifest = {
        "format_version": 1,
        "storage_mode": "complete",
        "files": {},
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="Complete"):
        inspect_backup(archive)


def test_rejeita_symlink_em_entrada_do_backup(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    payload = b"data"
    manifest = {
        "format_version": 1,
        "storage_mode": "lite",
        "files": {
            "database": {
                "path": "vectora.db",
                "sha256": "x",
                "size": len(payload),
            }
        },
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", json.dumps(manifest))
        info = zipfile.ZipInfo("vectora.db")
        info.external_attr = 0o120777 << 16
        handle.writestr(info, payload)

    with pytest.raises(ValueError, match="symlink"):
        inspect_backup(archive)
