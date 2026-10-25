"""Contrato de segurança do formato de backup local manifestado."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from backend.storage import backup_manifest as backup_module
from backend.storage.backup_manifest import (
    create_backup,
    inspect_backup,
    restore_backup,
)


def test_restore_rejeita_limite_agregado_antes_de_publicar(
    tmp_path: Path, monkeypatch
) -> None:
    """Verifica o comportamento de restore rejeita limite agregado antes de publicar."""
    payload_a = b"1234"
    payload_b = b"5678"
    archive = tmp_path / "aggregate-limit.zip"
    manifest = {
        "format_version": 1,
        "storage_mode": "lite",
        "files": {
            "artifacts": [
                {
                    "path": "artifacts/a.bin",
                    "sha256": hashlib.sha256(payload_a).hexdigest(),
                    "size": len(payload_a),
                    "count": 1,
                },
                {
                    "path": "artifacts/b.bin",
                    "sha256": hashlib.sha256(payload_b).hexdigest(),
                    "size": len(payload_b),
                    "count": 1,
                },
            ]
        },
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", json.dumps(manifest))
        handle.writestr("artifacts/a.bin", payload_a)
        handle.writestr("artifacts/b.bin", payload_b)

    database = tmp_path / "data" / "vectora.db"
    database.parent.mkdir()
    database.write_bytes(b"unchanged")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    existing = artifacts / "existing.md"
    existing.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(backup_module, "MAX_RESTORE_BYTES", 7)

    with pytest.raises(ValueError, match="limite agregado"):
        restore_backup(archive, database, categories={"artifacts"})
    assert database.read_bytes() == b"unchanged"
    assert existing.read_text(encoding="utf-8") == "keep"
    assert not (artifacts / "a.bin").exists()
    assert not (artifacts / "b.bin").exists()


def test_create_and_inspect_preserva_manifesto_lite(tmp_path: Path) -> None:
    """Verifica o comportamento de create and inspect preserva manifesto lite."""
    database = tmp_path / "vectora.db"
    database.write_bytes(b"sqlite-placeholder")
    archive = tmp_path / "backup.vbackup.zip"

    preview = create_backup(database, archive, app_version="1.0.0")

    assert preview.compatible is True
    assert preview.storage_mode == "lite"
    assert preview.categories == {"database": 1}
    assert inspect_backup(archive).size_bytes == archive.stat().st_size


def test_rejeita_backup_complete_antes_de_tocar_no_estado(tmp_path: Path) -> None:
    """Verifica o comportamento de rejeita backup complete antes de tocar no estado."""
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
    """Verifica o comportamento de rejeita symlink em entrada do backup."""
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


def test_artifacts_root_symlink_is_rejected_before_reading(tmp_path: Path) -> None:
    """Não percorre um diretório de artifacts apontado para fora do storage."""
    database = tmp_path / "data" / "vectora.db"
    database.parent.mkdir()
    database.write_bytes(b"sqlite-placeholder")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (database.parent.parent / "artifacts").symlink_to(
            outside, target_is_directory=True
        )
    except (OSError, NotImplementedError):
        pytest.skip("symlink não disponível neste ambiente")

    with pytest.raises(ValueError, match="symlink"):
        backup_module._artifact_entries(database)


def test_restore_respeita_destino_customizado_e_selecao_vazia(tmp_path: Path) -> None:
    """Verifica o comportamento de restore respeita destino customizado e selecao vazia."""
    database = tmp_path / "vectora.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('original')")
    connection.commit()
    connection.close()
    archive = tmp_path / "backup.vbackup.zip"
    create_backup(database, archive)

    target = tmp_path / "restaurado-com-outro-nome.sqlite"
    target.write_bytes(b"atual")
    restore_backup(archive, target, categories=set())
    assert target.read_bytes() == b"atual"
    restore_backup(archive, target, categories={"database"})
    restored = sqlite3.connect(target)
    assert restored.execute("SELECT value FROM marker").fetchone() == ("original",)
    restored.close()


def test_rejeita_categoria_desconhecida_e_entrada_extra(tmp_path: Path) -> None:
    """Verifica o comportamento de rejeita categoria desconhecida e entrada extra."""
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
    """Verifica o comportamento de restore mantem compatibilidade com backup db gz."""
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
    """Verifica o comportamento de create backup copia sessions com snapshot sqlite."""
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


def test_backup_restore_preserva_artifacts_e_sidecars(tmp_path: Path) -> None:
    """Verifica o comportamento de backup restore preserva artifacts e sidecars."""
    source_root = tmp_path / "source"
    source_db = source_root / "data" / "vectora.db"
    source_db.parent.mkdir(parents=True)
    source_db.write_bytes(b"sqlite-placeholder")
    artifact = source_root / "artifacts" / "thread" / "plan.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("conteudo", encoding="utf-8")
    artifact.with_suffix(".artifact.json").write_text(
        '{"sha256":"abc","retention_class":"run"}', encoding="utf-8"
    )

    archive = tmp_path / "artifacts.zip"
    preview = create_backup(source_db, archive)
    assert preview.categories["artifacts"] == 2

    target_root = tmp_path / "target"
    target_db = target_root / "data" / "vectora.db"
    target_db.parent.mkdir(parents=True)
    target_db.write_bytes(b"old")
    restore_backup(archive, target_db, categories={"artifacts"})

    restored = target_root / "artifacts" / "thread" / "plan.md"
    assert restored.read_text(encoding="utf-8") == "conteudo"
    assert restored.with_suffix(".artifact.json").exists()
