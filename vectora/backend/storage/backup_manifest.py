"""Formato local versionado para backup e reinstalação do Vectora.

O arquivo é uma lista explícita de artefatos permitidos. O leitor valida todas
as entradas antes de tocar no estado ativo, e o restore publica cada arquivo
atomically após preparar snapshots de rollback.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 256 * 1024 * 1024

_CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    "database": ("vectora.db",),
    "workspaces": ("workspaces.json",),
    "threads": ("sessions.db",),
    "memories": ("memories.json",),
}
_CATEGORIES = frozenset(_CATEGORY_PATHS)


@dataclass(frozen=True)
class BackupPreview:
    version: int
    app_version: str
    size_bytes: int
    categories: dict[str, int]
    compatible: bool
    storage_mode: str = "lite"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _workspace_path(database: Path) -> Path:
    """Resolve o arquivo global sem assumir que o banco se chama vectora.db."""
    if database.parent.name == "data":
        return database.parent.parent / "workspaces.json"
    return database.parent / "workspaces.json"


def _sessions_path(database: Path) -> Path:
    if database.parent.name == "data":
        return database.parent.parent / "sessions.db"
    return database.parent / "sessions.db"


def _sqlite_snapshot(database: Path) -> bytes:
    """Cria snapshot consistente, incluindo WAL, quando o arquivo é SQLite."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            return temporary.read_bytes()
        finally:
            temporary.unlink(missing_ok=True)
    except (sqlite3.DatabaseError, OSError):
        return database.read_bytes()


def _count_category(category: str, data: bytes) -> int:  # noqa: PLR0911
    if category == "workspaces":
        try:
            parsed = json.loads(data)
            if isinstance(parsed, list):
                return len(parsed)
            if isinstance(parsed, dict):
                value = parsed.get("workspaces", parsed)
                return len(value) if isinstance(value, (list, dict)) else 0
        except (TypeError, ValueError):
            return 0
    if category in {"database", "threads"}:
        return _count_sqlite_rows(
            data, "vectora_sessions" if category == "database" else "messages"
        )
    if category == "memories":
        try:
            parsed = json.loads(data)
            return len(parsed) if isinstance(parsed, (list, dict)) else 0
        except (TypeError, ValueError):
            return 0
    return 0


def _count_sqlite_rows(data: bytes, table: str) -> int:
    """Conta linhas usando apenas nomes de tabela internos permitidos."""
    if table not in {"vectora_sessions", "messages"}:
        return 0
    path: Path | None = None
    connection: sqlite3.Connection | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            path = Path(handle.name)
            handle.write(data)
        connection = sqlite3.connect(path)
        with connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row and row[0]:
                count_queries = {
                    "vectora_sessions": 'SELECT COUNT(*) FROM "vectora_sessions"',
                    "messages": 'SELECT COUNT(*) FROM "messages"',
                }
                return int(connection.execute(count_queries[table]).fetchone()[0])
            return 1
    except sqlite3.DatabaseError:
        return 1
    finally:
        if connection is not None:
            connection.close()
        if path is not None:
            path.unlink(missing_ok=True)


def _validate_relative(path: str) -> None:
    candidate = Path(path)
    if (
        not path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in path
        or candidate.name == ""
    ):
        raise ValueError("caminho de backup inválido")


def _manifest_files(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict):
        raise ValueError("manifesto sem arquivos")
    result: dict[str, list[dict[str, Any]]] = {}
    for category, raw in raw_files.items():
        if category not in _CATEGORIES:
            raise ValueError("categoria de backup inválida")
        items = raw if isinstance(raw, list) else [raw]
        if not items or any(not isinstance(item, dict) for item in items):
            raise ValueError("entrada de manifesto inválida")
        result[category] = items
    return result


def create_backup(
    db_path: str | Path,
    output: str | Path,
    *,
    app_version: str = "",
    storage_mode: str = "lite",
) -> BackupPreview:
    """Cria backup Lite sem segredos, caches ou diretórios de projeto."""
    database = Path(db_path)
    if not database.is_file():
        raise FileNotFoundError(database)
    if storage_mode != "lite":
        raise ValueError("backups do modo Complete devem ser feitos pelo provedor")
    sources: dict[str, list[tuple[str, bytes]]] = {
        "database": [("vectora.db", _sqlite_snapshot(database))]
    }
    optional = {
        "workspaces": (_workspace_path(database), "workspaces.json"),
        "threads": (_sessions_path(database), "sessions.db"),
        "memories": (database.parent.parent / "memories.json", "memories.json"),
    }
    for category, (source, archive_name) in optional.items():
        if source.is_file():
            sources[category] = [(archive_name, source.read_bytes())]
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "storage_mode": storage_mode,
        "files": {},
    }
    for category, entries in sources.items():
        manifest["files"][category] = [
            {
                "path": archive_name,
                "sha256": _digest(data),
                "size": len(data),
                "count": _count_category(category, data),
            }
            for archive_name, data in entries
        ]
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        for entries in sources.values():
            for archive_name, data in entries:
                archive.writestr(archive_name, data)
    return inspect_backup(destination)


def inspect_backup(archive_path: str | Path) -> BackupPreview:
    """Valida manifesto, caminhos, tipos, limites, hashes e entradas extras."""
    archive_file = Path(archive_path)
    if archive_file.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("backup excede o limite de tamanho")
    with zipfile.ZipFile(archive_file) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or MANIFEST not in names:
            raise ValueError("backup manifest ausente ou entradas duplicadas")
        for name in names:
            _validate_relative(name)
            info = archive.getinfo(name)
            if info.file_size > MAX_ENTRY_BYTES:
                raise ValueError("entrada de backup excede o limite")
            if info.is_dir() or ((info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("symlink ou diretório não permitido no backup")
        try:
            manifest = json.loads(archive.read(MANIFEST))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("manifesto inválido") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("format_version") != FORMAT_VERSION
        ):
            raise ValueError("versão de backup incompatível")
        mode = manifest.get("storage_mode", "lite")
        if mode != "lite":
            raise ValueError(
                "backups do modo Complete devem ser restaurados pelo provedor"
            )
        declared = _manifest_files(manifest)
        declared_paths = {MANIFEST}
        categories: dict[str, int] = {}
        for category, items in declared.items():
            allowed = set(_CATEGORY_PATHS[category])
            count = 0
            for item in items:
                path = item.get("path")
                if not isinstance(path, str) or path not in allowed:
                    raise ValueError("caminho de backup inválido")
                _validate_relative(path)
                if path in declared_paths or path not in names:
                    raise ValueError("entrada de backup duplicada ou ausente")
                declared_paths.add(path)
                info = archive.getinfo(path)
                data = archive.read(info)
                if (
                    not isinstance(item.get("sha256"), str)
                    or _digest(data) != item["sha256"]
                ):
                    raise ValueError(f"integridade inválida para {category}")
                if item.get("size") != len(data):
                    raise ValueError(f"tamanho inválido para {category}")
                if not isinstance(item.get("count", 0), int) or item["count"] < 0:
                    raise ValueError("contagem de backup inválida")
                count += item["count"]
            categories[category] = count
        if set(names) != declared_paths:
            raise ValueError("backup contém entradas não declaradas")
    return BackupPreview(
        FORMAT_VERSION,
        str(manifest.get("app_version", "")),
        archive_file.stat().st_size,
        categories,
        True,
        str(mode),
    )


def _atomic_write(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(destination)


def restore_backup(
    archive_path: str | Path, db_path: str | Path, categories: set[str] | None = None
) -> BackupPreview:
    """Restaura categorias selecionadas com staging, snapshots e rollback total."""
    archive_file = Path(archive_path)
    if archive_file.suffix == ".gz" and not zipfile.is_zipfile(archive_file):
        destination = Path(db_path)
        with gzip.open(archive_file, "rb") as source:
            _atomic_write(destination, source.read())
        return BackupPreview(0, "", archive_file.stat().st_size, {"database": 0}, True)
    preview = inspect_backup(archive_file)
    selected = set(preview.categories) if categories is None else set(categories)
    unknown = selected - _CATEGORIES
    if unknown:
        raise ValueError("categoria de backup desconhecida")
    unavailable = selected - set(preview.categories)
    if unavailable:
        raise ValueError("categoria selecionada não está presente no backup")
    destination = Path(db_path)
    targets = {
        "database": destination,
        "workspaces": _workspace_path(destination),
        "threads": _sessions_path(destination),
        "memories": destination.parent.parent / "memories.json",
    }
    snapshots: dict[Path, bytes | None] = {
        path: path.read_bytes() if path.is_file() else None
        for category, path in targets.items()
        if category in selected
    }
    try:
        with zipfile.ZipFile(archive_file) as archive:
            manifest = json.loads(archive.read(MANIFEST))
            declared = _manifest_files(manifest)
            staged = {
                targets[category]: archive.read(declared[category][0]["path"])
                for category in selected
            }
        for path, data in staged.items():
            _atomic_write(path, data)
    except Exception:
        for path, previous in snapshots.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
        raise
    return preview
