"""Opaque multimodal asset metadata and ownership checks."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol, cast
from uuid import uuid4

from backend.settings import settings


class _Fcntl(Protocol):
    LOCK_EX: int

    def flock(self, file_descriptor: int, operation: int) -> None: ...


class _Msvcrt(Protocol):
    LK_LOCK: int

    def locking(
        self, file_descriptor: int, mode: int, number_of_bytes: int
    ) -> None: ...


try:
    _fcntl: _Fcntl | None = cast("_Fcntl", importlib.import_module("fcntl"))
except ImportError:  # pragma: no cover - Windows
    _fcntl = None
try:
    _msvcrt: _Msvcrt | None = cast("_Msvcrt", importlib.import_module("msvcrt"))
except ImportError:  # pragma: no cover - Unix
    _msvcrt = None

_PROCESS_LOCKS: dict[Path, Lock] = {}
_PROCESS_LOCKS_GUARD = Lock()

ALLOWED_MIME = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "audio/mpeg",
    "video/mp4",
}
MAX_ASSET_BYTES = 100 * 1024 * 1024


def _has_valid_signature(data: bytes, mime_type: str) -> bool:
    signatures: dict[str, tuple[bytes, ...]] = {
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "audio/mpeg": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    }
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime_type == "video/mp4":
        return len(data) >= 8 and data[4:8] == b"ftyp"
    return any(
        data.startswith(signature) for signature in signatures.get(mime_type, ())
    )


def validate_asset_bytes(
    data: bytes, mime_type: str, filename: str | None = None
) -> bool:
    normalized = "image/jpeg" if mime_type == "image/jpg" else mime_type
    if normalized not in ALLOWED_MIME or not _has_valid_signature(data, normalized):
        return False
    if filename:
        extensions = {
            "image/png": {".png"},
            "image/jpeg": {".jpg", ".jpeg"},
            "image/gif": {".gif"},
            "image/webp": {".webp"},
            "audio/mpeg": {".mp3"},
            "video/mp4": {".mp4"},
        }
        suffix = Path(filename).suffix.lower()
        if suffix and suffix not in extensions.get(normalized, set()):
            return False
    return True


@dataclass(frozen=True)
class Asset:
    id: str
    path: str
    owner_id: str
    workspace_id: str
    thread_id: str
    mime_type: str
    size_bytes: int
    source: str
    created_at: str


class AssetStore:
    """Small local metadata store; the storage key never leaves this module."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.vectora_home / "assets"
        self.index = self.root / "index.json"
        self._lock = self.root / "index.lock"

    def _process_lock(self) -> Lock:
        with _PROCESS_LOCKS_GUARD:
            return _PROCESS_LOCKS.setdefault(self.index.resolve(), Lock())

    @contextmanager
    def _locked_index(self) -> Iterator[None]:
        """Serialize index reads and writes across threads and processes."""
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            lock_file = self._lock.open("r+b")
        except FileNotFoundError:
            lock_file = self._lock.open("w+b")
        with self._process_lock(), lock_file as lock:
            if _fcntl is not None:
                _fcntl.flock(lock.fileno(), _fcntl.LOCK_EX)
            elif _msvcrt is not None:
                lock.seek(0)
                lock.write(b"0")
                lock.flush()
                lock.seek(0)
                _msvcrt.locking(lock.fileno(), _msvcrt.LK_LOCK, 1)
            else:
                raise RuntimeError("lock interprocesso de assets indisponível")
            yield

    def _write_records(self, records: dict[str, dict[str, object]]) -> None:
        """Atomically replace the metadata index with validated records."""
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.root, delete=False
        ) as temporary:
            temporary.write(json.dumps(records, ensure_ascii=False))
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(self.index)
        finally:
            temporary_path.unlink(missing_ok=True)

    def create(
        self,
        *,
        path: Path,
        owner_id: str,
        workspace_id: str,
        thread_id: str,
        mime_type: str,
        source: str,
    ) -> Asset:
        mime_type = "image/jpeg" if mime_type == "image/jpg" else mime_type
        original_path = path
        storage_root = self.root.parent.resolve()
        path = path.resolve()
        if (
            mime_type not in ALLOWED_MIME
            or not path.is_relative_to(storage_root)
            or not path.is_file()
            or original_path.is_symlink()
        ):
            raise ValueError("asset inválido")
        size = path.stat().st_size
        if size > MAX_ASSET_BYTES:
            raise ValueError("asset excede o tamanho máximo")
        if not validate_asset_bytes(path.read_bytes(), mime_type, path.name):
            raise ValueError("assinatura do asset inválida")
        item = Asset(
            uuid4().hex,
            str(path),
            owner_id,
            workspace_id,
            thread_id,
            mime_type,
            size,
            source,
            datetime.now(UTC).isoformat(),
        )
        with self._locked_index():
            records = self._read()
            records[item.id] = asdict(item)
            self._write_records(records)
        return item

    def get(
        self,
        asset_id: str,
        *,
        owner_id: str,
        workspace_id: str,
        thread_id: str = "",
    ) -> Asset | None:
        raw = self._read().get(asset_id)
        if not isinstance(raw, dict):
            return None
        required = (
            "id",
            "path",
            "owner_id",
            "workspace_id",
            "thread_id",
            "mime_type",
            "size_bytes",
            "source",
            "created_at",
        )
        if (
            not raw
            or raw.get("owner_id") != owner_id
            or raw.get("workspace_id") != workspace_id
            or (thread_id and raw.get("thread_id") != thread_id)
            or any(key not in raw for key in required)
        ):
            return None
        storage_root = self.root.parent.resolve()
        stored_path = Path(str(raw["path"]))
        path = stored_path.resolve()
        valid = (
            not path.is_relative_to(storage_root)
            or stored_path.is_symlink()
            or not path.is_file()
        )
        size_value = raw["size_bytes"]
        size_bytes = size_value if isinstance(size_value, int) else -1
        valid = valid or size_bytes < 0
        try:
            if not valid:
                valid = (
                    path.stat().st_size != size_bytes or size_bytes > MAX_ASSET_BYTES
                )
            if not valid:
                valid = not validate_asset_bytes(
                    path.read_bytes(), str(raw["mime_type"]), path.name
                )
        except OSError:
            valid = True
        if valid:
            return None
        return Asset(
            id=str(raw["id"]),
            path=str(raw["path"]),
            owner_id=str(raw["owner_id"]),
            workspace_id=str(raw["workspace_id"]),
            thread_id=str(raw["thread_id"]),
            mime_type=str(raw["mime_type"]),
            size_bytes=size_bytes,
            source=str(raw["source"]),
            created_at=str(raw["created_at"]),
        )

    def _read(self) -> dict[str, dict[str, object]]:
        try:
            value = json.loads(self.index.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        required = {
            "id",
            "path",
            "owner_id",
            "workspace_id",
            "thread_id",
            "mime_type",
            "size_bytes",
            "source",
            "created_at",
        }
        return {
            str(key): record
            for key, record in value.items()
            if isinstance(record, dict) and required.issubset(record)
        }

    def delete_thread_assets(self, thread_id: str) -> None:
        """Remove assets da thread sem apagar arquivos ainda referenciados."""
        if not thread_id.strip():
            return
        with self._locked_index():
            records = self._read()
            removed = [
                record
                for record in records.values()
                if str(record.get("thread_id", "")) == thread_id
            ]
            if not removed:
                return
            kept = {
                asset_id: record
                for asset_id, record in records.items()
                if str(record.get("thread_id", "")) != thread_id
            }
            self._write_records(kept)
            kept_paths = {str(record.get("path")) for record in kept.values()}
            root_path = self.root.parent.resolve()
            for record in removed:
                path = str(record.get("path", ""))
                candidate = Path(path)
                if (
                    path
                    and path not in kept_paths
                    and candidate.resolve().is_relative_to(root_path)
                ):
                    candidate.unlink(missing_ok=True)


asset_store = AssetStore()
