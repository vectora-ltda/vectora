"""Opaque multimodal asset metadata and ownership checks."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol, cast
from uuid import uuid4

from backend.services.media_types import EXTENSION_BY_MIME, OCTET_STREAM, sniff_mime
from backend.settings import settings


class _Fcntl(Protocol):
    LOCK_EX: int

    def flock(self, file_descriptor: int, operation: int) -> None: ...


class _Msvcrt(Protocol):
    LK_LOCK: int

    def locking(
        self, file_descriptor: int, mode: int, number_of_bytes: int
    ) -> None: ...


def _read_relative_nofollow(root: Path, parts: list[str]) -> bytes:
    """Read a regular file through no-follow directory descriptors."""
    if os.name == "nt":
        return root.joinpath(*parts).read_bytes()
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(root, directory_flags | nofollow)
    try:
        for component in parts[:-1]:
            child = os.open(component, directory_flags | nofollow, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise OSError("not a regular file")
            with os.fdopen(file_fd, "rb") as stream:
                file_fd = -1
                return stream.read()
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    finally:
        os.close(fd)


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

ALLOWED_MIME = frozenset(mime for mime in EXTENSION_BY_MIME if mime != "image/jpg") | {
    "audio/mp3"
}
MAX_ASSET_BYTES = 100 * 1024 * 1024

#: MIMEs que compartilham contêiner e só se distinguem pela extensão.
_EQUIVALENT_MIMES = (
    frozenset({"audio/webm", "video/webm"}),
    frozenset({"audio/mp4", "video/mp4"}),
    frozenset({"audio/mpeg", "audio/mp3"}),
)
_EXTRA_EXTENSIONS: dict[str, set[str]] = {
    "image/jpeg": {".jpeg"},
    "audio/mp3": {".mp3"},
    "audio/webm": {".weba"},
}


def _normalize_mime(mime_type: str) -> str:
    return "image/jpeg" if mime_type == "image/jpg" else mime_type


def _same_media(declared: str, sniffed: str | None) -> bool:
    if sniffed is None:
        return False
    if declared == sniffed:
        return True
    return any({declared, sniffed} <= group for group in _EQUIVALENT_MIMES)


def _has_valid_signature(data: bytes, mime_type: str, filename: str = "") -> bool:
    """Bytes conferem com o MIME; arquivo genérico aceita qualquer conteúdo."""
    if mime_type == OCTET_STREAM:
        return True
    return _same_media(mime_type, sniff_mime(data, filename))


def validate_asset_bytes(
    data: bytes, mime_type: str, filename: str | None = None
) -> bool:
    normalized = _normalize_mime(mime_type)
    if normalized not in ALLOWED_MIME or not _has_valid_signature(
        data, normalized, filename or ""
    ):
        return False
    if filename and normalized != OCTET_STREAM:
        expected = {EXTENSION_BY_MIME.get(normalized, "")} | _EXTRA_EXTENSIONS.get(
            normalized, set()
        )
        for group in _EQUIVALENT_MIMES:
            if normalized in group:
                for alias in group:
                    expected.add(EXTENSION_BY_MIME.get(alias, ""))
                    expected |= _EXTRA_EXTENSIONS.get(alias, set())
        suffix = Path(filename).suffix.lower()
        if suffix and suffix not in expected:
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
    sha256: str = ""


class AssetStore:
    """Small local metadata store; the storage key never leaves this module."""

    def __init__(self, root: Path | None = None) -> None:
        self._uses_default_root = root is None
        self.root = root or settings.vectora_home / "assets"
        self.index = self.root / "index.json"
        self._lock = self.root / "index.lock"

    def _storage_root(self) -> Path:
        """Return the media root corresponding to this metadata store."""
        if self._uses_default_root:
            return settings.vectora_home.resolve()
        return self.root.parent.resolve()

    def _process_lock(self) -> Lock:
        with _PROCESS_LOCKS_GUARD:
            return _PROCESS_LOCKS.setdefault(self.index.resolve(), Lock())

    @contextmanager
    def _locked_index(self) -> Generator[None]:
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
        mime_type = _normalize_mime(mime_type)
        original_path = path
        storage_root = self._storage_root()
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
        payload = path.read_bytes()
        if not validate_asset_bytes(payload, mime_type, path.name):
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
            hashlib.sha256(payload).hexdigest(),
        )
        with self._locked_index():
            records = self._read(strict=True)
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
        result = self.read(
            asset_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )
        return result[0] if result is not None else None

    def read(
        self,
        asset_id: str,
        *,
        owner_id: str,
        workspace_id: str,
        thread_id: str = "",
    ) -> tuple[Asset, bytes] | None:
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
        storage_root = self._storage_root()
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
                relative = path.relative_to(storage_root)
                payload = _read_relative_nofollow(storage_root, list(relative.parts))
                valid = len(payload) != size_bytes or len(payload) > MAX_ASSET_BYTES
            if not valid:
                valid = not validate_asset_bytes(
                    payload, str(raw["mime_type"]), path.name
                )
            expected_digest = str(raw.get("sha256") or "")
            if not valid and expected_digest:
                valid = hashlib.sha256(payload).hexdigest() != expected_digest
        except OSError:
            valid = True
        if valid:
            return None
        asset = Asset(
            id=str(raw["id"]),
            path=str(raw["path"]),
            owner_id=str(raw["owner_id"]),
            workspace_id=str(raw["workspace_id"]),
            thread_id=str(raw["thread_id"]),
            mime_type=str(raw["mime_type"]),
            size_bytes=size_bytes,
            source=str(raw["source"]),
            created_at=str(raw["created_at"]),
            sha256=str(raw.get("sha256") or ""),
        )
        return asset, payload

    def _read(self, *, strict: bool = False) -> dict[str, dict[str, object]]:
        try:
            value = json.loads(self.index.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            if strict:
                raise RuntimeError("índice de assets corrompido") from exc
            return {}
        if not isinstance(value, dict):
            if strict:
                raise RuntimeError("índice de assets corrompido")
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
                (asset_id, record)
                for asset_id, record in records.items()
                if str(record.get("thread_id", "")) == thread_id
            ]
            if not removed:
                return
            kept = {
                asset_id: record
                for asset_id, record in records.items()
                if str(record.get("thread_id", "")) != thread_id
            }
            kept_paths = {str(record.get("path")) for record in kept.values()}
            root_path = self._storage_root()
            for asset_id, record in removed:
                path = str(record.get("path", ""))
                candidate = Path(path)
                removed_ok = False
                if path and path not in kept_paths:
                    try:
                        if candidate.resolve().is_relative_to(root_path):
                            candidate.unlink(missing_ok=True)
                            removed_ok = True
                    except OSError:
                        removed_ok = False
                if not removed_ok:
                    kept[asset_id] = record
            self._write_records(kept)


asset_store = AssetStore()
