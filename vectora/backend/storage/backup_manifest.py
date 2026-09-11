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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.storage.migrations.runner import _ALTER_ADD_COLUMN_RE, _split_statements

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
    results: dict[str, dict[str, object]] = field(default_factory=dict)


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


_SENSITIVE_TABLES = {
    "users",
    "refresh_tokens",
    "secrets",
    "vectora_secrets",
    "keyring",
    "sessions",
    "auth_sessions",
    "oauth_tokens",
    "cookies",
}


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
            # O snapshot nunca transporta credenciais, tokens ou cofres. A
            # cópia é sanitizada antes de ser materializada no arquivo ZIP.
            connection = sqlite3.connect(temporary)
            try:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                for (table,) in tables:
                    normalized = str(table).lower()
                    if normalized in _SENSITIVE_TABLES or any(
                        marker in normalized
                        for marker in ("token", "secret", "cookie", "keyring", "auth")
                    ):
                        connection.execute(
                            f'DROP TABLE IF EXISTS "{str(table).replace(chr(34), chr(34) * 2)}"'
                        )
                connection.commit()
            finally:
                connection.close()
            return temporary.read_bytes()
        finally:
            temporary.unlink(missing_ok=True)
    except (sqlite3.DatabaseError, OSError) as exc:
        # Fixtures legados e bancos externos podem ser blobs opacos; eles não
        # têm tabelas SQLite capazes de transportar credenciais.
        raw = database.read_bytes()
        if not raw.startswith(b"SQLite format 3\x00"):
            return raw
        raise ValueError("não foi possível criar snapshot SQLite sanitizado") from exc


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
            data = (
                _sqlite_snapshot(source)
                if category == "threads"
                else source.read_bytes()
            )
            sources[category] = [(archive_name, data)]
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


def _apply_sqlite_schema(connection: sqlite3.Connection) -> None:
    """Aplica o schema único ao banco em staging com a mesma semântica do runner.

    O restore é síncrono e ocorre antes de o arquivo voltar a ser visível para o
    processo. Reutilizamos a divisão e a regra de ``ALTER TABLE`` do runner para
    manter as migrações idempotentes, sem executar o script inteiro de uma vez.
    """
    schema_path = Path(__file__).parent / "migrations" / "sqlite" / "schema.sql"
    if not schema_path.is_file():
        return
    for statement in _split_statements(schema_path.read_text(encoding="utf-8")):
        alter_match = _ALTER_ADD_COLUMN_RE.match(statement)
        if alter_match:
            table, column = alter_match.group(1), alter_match.group(2)
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column in columns:
                continue
        connection.execute(statement)
    connection.commit()


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
    snapshots = snapshot_targets(destination, selected)
    try:
        with zipfile.ZipFile(archive_file) as archive:
            manifest = json.loads(archive.read(MANIFEST))
            declared = _manifest_files(manifest)
            staged = {
                targets[category]: archive.read(declared[category][0]["path"])
                for category in selected
            }
        # Validação e migração do SQLite ocorrem no staging, antes de tocar no
        # banco ativo. O arquivo promovido é o staging migrado, nunca o payload
        # original do ZIP.
        staged_temp: dict[Path, Path] = {}
        for path, data in staged.items():
            if path.suffix == ".db":
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
                    handle.write(data)
                    staged_temp[path] = Path(handle.name)
                check = sqlite3.connect(staged_temp[path])
                try:
                    if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("banco restaurado inválido")
                    _apply_sqlite_schema(check)
                    staged[path] = staged_temp[path].read_bytes()
                finally:
                    check.close()
                    staged_temp[path].unlink(missing_ok=True)
        skipped_counts: dict[str, int] = {}
        for path, data in staged.items():
            if path.name == "workspaces.json":
                parsed = json.loads(data)
                records = (
                    parsed
                    if isinstance(parsed, list)
                    else parsed.get("workspaces", [])
                    if isinstance(parsed, dict)
                    else []
                )
                if not isinstance(records, list):
                    raise ValueError("índice de workspaces inválido")
                skipped_counts["workspaces"] = 0
                safe_records = [
                    {
                        **item,
                        "trusted": False,
                        "trusted_at": None,
                        "trusted_by": None,
                    }
                    for item in records
                    if isinstance(item, dict)
                    and isinstance(item.get("path"), str)
                    and Path(item["path"]).expanduser().exists()
                ]
                skipped_counts["workspaces"] = len(records) - len(safe_records)
                staged_data = json.dumps(safe_records, ensure_ascii=False).encode()
            else:
                staged_data = data
            _atomic_write(path, staged_data)
    except Exception:
        for path, previous in snapshots.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
        raise
    results: dict[str, dict[str, object]] = {
        category: {
            "status": "imported",
            "count": preview.categories.get(category, 0)
            - skipped_counts.get(category, 0),
            "skipped": skipped_counts.get(category, 0),
        }
        for category in selected
    }
    for category in set(preview.categories) - selected:
        results[category] = {"status": "skipped", "count": preview.categories[category]}
    return BackupPreview(
        preview.version,
        preview.app_version,
        preview.size_bytes,
        preview.categories,
        preview.compatible,
        preview.storage_mode,
        results,
    )


def snapshot_targets(
    db_path: str | Path, categories: set[str]
) -> dict[Path, bytes | None]:
    """Captura os arquivos selecionados para rollback de uma promoção externa."""
    destination = Path(db_path)
    targets = {
        "database": destination,
        "workspaces": _workspace_path(destination),
        "threads": _sessions_path(destination),
        "memories": destination.parent.parent / "memories.json",
    }
    return {
        path: path.read_bytes() if path.is_file() else None
        for category, path in targets.items()
        if category in categories
    }


def restore_snapshots(snapshots: dict[Path, bytes | None]) -> None:
    """Reaplica snapshots capturados antes de uma promoção malsucedida."""
    for path, previous in snapshots.items():
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, previous)
