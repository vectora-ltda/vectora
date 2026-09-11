"""Opaque multimodal asset metadata and ownership checks."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
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
        if mime_type not in ALLOWED_MIME or not path.is_file() or path.is_symlink():
            raise ValueError("asset inválido")
        size = path.stat().st_size
        if size > MAX_ASSET_BYTES:
            raise ValueError("asset excede o tamanho máximo")
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
            records = self._read()
            records[item.id] = asdict(item)
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
            # O fechamento do descritor libera o lock no Windows.
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
        if (
            not raw
            or raw.get("owner_id") != owner_id
            or raw.get("workspace_id") != workspace_id
            or (thread_id and raw.get("thread_id") != thread_id)
        ):
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
        if any(key not in raw for key in required):
            return None
        path = Path(str(raw["path"]))
        if path.is_symlink() or not path.is_file():
            return None
        size_value = raw["size_bytes"]
        if not isinstance(size_value, int):
            return None
        return Asset(
            id=str(raw["id"]),
            path=str(raw["path"]),
            owner_id=str(raw["owner_id"]),
            workspace_id=str(raw["workspace_id"]),
            thread_id=str(raw["thread_id"]),
            mime_type=str(raw["mime_type"]),
            size_bytes=size_value,
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
        return {
            str(key): record
            for key, record in value.items()
            if isinstance(record, dict)
        }


asset_store = AssetStore()
