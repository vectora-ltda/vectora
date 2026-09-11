"""Contrato de segurança do formato de backup local manifestado."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
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


def test_restore_promove_banco_de_staging_ja_migrado(tmp_path: Path) -> None:
    """O arquivo publicado deve conter as colunas adicionadas pelo schema."""
    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    connection.execute(
        """
        CREATE TABLE vectora_background_tasks (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            workspace_id TEXT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            instruction TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            trigger_config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT,
            next_run_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.commit()
    connection.close()
    payload = source.read_bytes()
    archive = tmp_path / "migration.zip"
    manifest = {
        "format_version": 1,
        "storage_mode": "lite",
        "files": {
            "database": {
                "path": "vectora.db",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "count": 0,
            }
        },
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", json.dumps(manifest))
        handle.writestr("vectora.db", payload)

    target = tmp_path / "restored.db"
    restore_backup(archive, target)
    connection = sqlite3.connect(target)
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(vectora_background_tasks)"
        ).fetchall()
    }
    connection.close()
    assert "status" in columns
    assert "priority" in columns


def test_create_backup_copia_sessions_com_snapshot_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "vectora.db"
    database.write_bytes(b"sqlite-placeholder")
    sessions = tmp_path / "sessions.db"
    connection = sqlite3.connect(sessions)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, body TEXT)")
    connection.execute("INSERT INTO messages VALUES ('m1', 'confirmado')")
    connection.commit()
    archive = tmp_path / "backup.zip"
    # O arquivo principal não contém a linha enquanto o WAL estiver ativo;
    # create_backup deve usar a API de backup do SQLite.
    preview = create_backup(database, archive)
    assert preview.categories["database"] == 1
    connection.close()
    with zipfile.ZipFile(archive) as handle:
        assert "sessions.db" in handle.namelist()
        restored = tmp_path / "sessions-restored.db"
        restored.write_bytes(handle.read("sessions.db"))
    restored_connection = sqlite3.connect(restored)
    assert (
        restored_connection.execute("SELECT body FROM messages").fetchone()[0]
        == "confirmado"
    )
    restored_connection.close()
