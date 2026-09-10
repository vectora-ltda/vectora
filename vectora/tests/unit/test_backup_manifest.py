"""Contrato de segurança do formato de backup local manifestado."""

from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

import pytest

from backend.storage.backup_manifest import (
    create_backup,
    inspect_backup,
    restore_backup,
)


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


def test_restore_respeita_destino_customizado_e_selecao_vazia(tmp_path: Path) -> None:
    database = tmp_path / "vectora.db"
    database.write_bytes(b"original")
    archive = tmp_path / "backup.vbackup.zip"
    create_backup(database, archive)

    target = tmp_path / "restaurado-com-outro-nome.sqlite"
    target.write_bytes(b"atual")
    restore_backup(archive, target, categories=set())
    assert target.read_bytes() == b"atual"
    restore_backup(archive, target, categories={"database"})
    assert target.read_bytes() == b"original"


def test_rejeita_categoria_desconhecida_e_entrada_extra(tmp_path: Path) -> None:
    database = tmp_path / "vectora.db"
    database.write_bytes(b"data")
    archive = tmp_path / "backup.vbackup.zip"
    create_backup(database, archive)
    with pytest.raises(ValueError, match="desconhecida"):
        restore_backup(archive, tmp_path / "restored.db", categories={"other"})
    with zipfile.ZipFile(archive, "a") as handle:
        handle.writestr("extra.txt", b"nao declarado")
    with pytest.raises(ValueError, match="não declaradas"):
        inspect_backup(archive)


def test_restore_mantem_compatibilidade_com_backup_db_gz(tmp_path: Path) -> None:
    archive = tmp_path / "legacy.db.gz"
    with gzip.open(archive, "wb") as handle:
        handle.write(b"legacy")
    target = tmp_path / "custom.db"
    restore_backup(archive, target)
    assert target.read_bytes() == b"legacy"
