"""Versioned, local-only backup format used by the reinstall wizard.

Archives contain a manifest and a small allow-list of local data files.  The
reader treats every archive entry as untrusted and validates hashes and paths
before any destination is touched.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 256 * 1024 * 1024
_ALLOWED = {"database": "vectora.db", "workspaces": "workspaces.json"}


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


def create_backup(
    db_path: str | Path,
    output: str | Path,
    *,
    app_version: str = "",
    storage_mode: str = "lite",
) -> BackupPreview:
    """Create a manifest archive without including credentials or caches."""
    database = Path(db_path)
    if not database.is_file():
        raise FileNotFoundError(database)
    files: dict[str, bytes] = {"database": database.read_bytes()}
    workspace_file = database.parent.parent / "workspaces.json"
    if workspace_file.is_file():
        files["workspaces"] = workspace_file.read_bytes()
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "storage_mode": storage_mode,
        "files": {
            category: {
                "path": _ALLOWED[category],
                "sha256": _digest(data),
                "size": len(data),
            }
            for category, data in files.items()
        },
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        for category, data in files.items():
            archive.writestr(_ALLOWED[category], data)
    return inspect_backup(destination)


def inspect_backup(archive_path: str | Path) -> BackupPreview:
    """Validate manifest, entry names, hashes and duplicate paths."""
    archive_file = Path(archive_path)
    if archive_file.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("backup excede o limite de tamanho")
    with zipfile.ZipFile(archive_file) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or MANIFEST not in names:
            raise ValueError("backup manifest ausente ou entradas duplicadas")
        manifest = json.loads(archive.read(MANIFEST))
        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError("versão de backup incompatível")
        mode = str(manifest.get("storage_mode", "lite"))
        if mode != "lite":
            raise ValueError(
                "backups do modo Complete devem ser restaurados pelo provedor"
            )
        categories: dict[str, int] = {}
        for category, item in manifest.get("files", {}).items():
            path = str(item.get("path", ""))
            if (
                category not in _ALLOWED
                or path != _ALLOWED[category]
                or Path(path).is_absolute()
                or ".." in Path(path).parts
            ):
                raise ValueError("caminho de backup inválido")
            info = archive.getinfo(path)
            if info.file_size > MAX_ENTRY_BYTES or info.is_dir():
                raise ValueError("entrada de backup inválida")
            # Unix symlinks are represented in the external attributes.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("symlink não permitido no backup")
            data = archive.read(info)
            if _digest(data) != item.get("sha256") or len(data) != item.get("size"):
                raise ValueError(f"integridade inválida para {category}")
            categories[category] = 1
    return BackupPreview(
        FORMAT_VERSION,
        str(manifest.get("app_version", "")),
        archive_file.stat().st_size,
        categories,
        True,
        mode,
    )


def restore_backup(
    archive_path: str | Path, db_path: str | Path, categories: set[str] | None = None
) -> BackupPreview:
    """Restore selected categories with a rollback copy of the active database."""
    preview = inspect_backup(archive_path)
    selected = categories or set(preview.categories)
    destination = Path(db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rollback = destination.with_suffix(destination.suffix + ".rollback")
    if destination.exists():
        shutil.copy2(destination, rollback)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if "database" in selected:
                archive.extract("vectora.db", destination.parent)
            if "workspaces" in selected:
                workspace_file = destination.parent.parent / "workspaces.json"
                workspace_file.write_bytes(archive.read("workspaces.json"))
    except Exception:
        if rollback.exists():
            shutil.copy2(rollback, destination)
        raise
    return preview
