"""Persisted safe-root registry with atomic, cross-process transactions."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import time
from _thread import RLock as RLockType
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import ClassVar

from backend.settings import settings
from backend.vtypes import SafeRoot

logger = logging.getLogger(__name__)

_WINDOWS_LOCK_ATTEMPTS = 20
_WINDOWS_LOCK_RETRY_DELAY_SECONDS = 0.5


class SafeRootPersistenceError(RuntimeError):
    """Raised when the safe-root registry cannot be durably persisted."""


def _safe_roots_file() -> Path:
    """Return the configured safe-root JSON path."""
    return settings.vectora_home / "safe_roots.json"


def _default_builtin_root() -> Path:
    """Return the default workspace root."""
    return settings.vectora_home.parent / "Documents" / "vectora"


class SafeRootRegistry:
    """Registry whose every operation observes a serialized disk snapshot."""

    _instance: ClassVar[SafeRootRegistry | None] = None

    def __init__(self) -> None:
        self._roots: dict[str, SafeRoot] = {}
        self._loaded = False
        self._lock: RLockType = RLock()

    @classmethod
    def instance(cls) -> SafeRootRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def derive_id(path: str) -> str:
        """Return a deterministic ID for an absolute, resolved path."""
        normalized = str(Path(path).expanduser().resolve())
        return hashlib.sha256(normalized.encode()).hexdigest()[:8]

    def _refresh_from_disk(self) -> None:
        """Replace the in-memory snapshot with the latest persisted data."""
        safe_roots_file = _safe_roots_file()
        if not safe_roots_file.exists():
            self._roots = {}
            return
        try:
            data = json.loads(safe_roots_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("roots"), list):
                raise ValueError("formato de safe_roots.json inválido")
            candidate: dict[str, SafeRoot] = {}
            for item in data.get("roots", []):
                try:
                    root = SafeRoot(**item)
                except Exception as exc:
                    raise ValueError(f"SafeRoot inválido: {item!r}") from exc
                candidate[root.id] = root
        except Exception as exc:
            logger.warning("Falha ao carregar safe_roots.json", exc_info=True)
            raise SafeRootPersistenceError(
                "Não foi possível carregar as pastas seguras persistidas."
            ) from exc
        self._roots = candidate

    def _load(self) -> None:
        """Load once for compatibility with existing callers."""
        if self._loaded:
            return
        self._refresh_from_disk()
        self._ensure_builtin()
        self._loaded = True

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Serialize transactions across registry instances and processes."""
        target = _safe_roots_file()
        lock_file = target.with_name(f"{target.name}.lock")
        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
        except SafeRootPersistenceError:
            raise
        except Exception as exc:
            raise SafeRootPersistenceError(
                "Não foi possível bloquear o registro de pastas seguras."
            ) from exc
        if os.name == "nt":
            import msvcrt

            # ``locking`` needs one existing byte and provides an advisory
            # inter-process lock on the Windows host.
            stream = None
            try:
                with lock_file.open("ab") as initializer:
                    if initializer.tell() == 0:
                        initializer.write(b"0")
                stream = lock_file.open("r+b")
                for attempt in range(_WINDOWS_LOCK_ATTEMPTS):
                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError:
                        if attempt == _WINDOWS_LOCK_ATTEMPTS - 1:
                            raise
                        time.sleep(_WINDOWS_LOCK_RETRY_DELAY_SECONDS)
                    else:
                        break
            except Exception as exc:
                if stream is not None:
                    stream.close()
                raise SafeRootPersistenceError(
                    "Não foi possível bloquear o registro de pastas seguras."
                ) from exc
            body_failed = False
            try:
                try:
                    yield
                except BaseException:
                    body_failed = True
                    raise
            finally:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception as exc:
                    if not body_failed:
                        raise SafeRootPersistenceError(
                            "Não foi possível desbloquear o registro de pastas seguras."
                        ) from exc
                    logger.warning(
                        "Falha ao liberar o lock de safe_roots", exc_info=True
                    )
                finally:
                    stream.close()
            return

        try:
            stream = lock_file.open("a+b")
        except Exception as exc:
            raise SafeRootPersistenceError(
                "Não foi possível bloquear o registro de pastas seguras."
            ) from exc
        import fcntl

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except Exception as exc:
            stream.close()
            raise SafeRootPersistenceError(
                "Não foi possível bloquear o registro de pastas seguras."
            ) from exc
        body_failed = False
        try:
            try:
                yield
            except BaseException:
                body_failed = True
                raise
        finally:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except Exception as exc:
                if not body_failed:
                    raise SafeRootPersistenceError(
                        "Não foi possível desbloquear o registro de pastas seguras."
                    ) from exc
                logger.warning("Falha ao liberar o lock de safe_roots", exc_info=True)
            finally:
                stream.close()

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Run a transaction against a freshly loaded snapshot."""
        with self._lock:
            with self._file_lock():
                self._refresh_from_disk()
                self._ensure_builtin()
                self._loaded = True
                yield

    def _ensure_builtin(self) -> None:
        """Ensure the default workspace root exists and is protected."""
        builtin_path = _default_builtin_root()
        builtin_id = self.derive_id(str(builtin_path))
        existing = self._roots.get(builtin_id)
        if existing is not None:
            if not existing.builtin:
                candidate = dict(self._roots)
                candidate[builtin_id] = existing.model_copy(update={"builtin": True})
                self._save_roots(candidate)
                self._roots = candidate
            return
        builtin = SafeRoot(
            id=builtin_id,
            path=str(builtin_path.resolve()),
            label="Workspaces Vectora",
            created_at=datetime.now(UTC).isoformat(),
            created_by="system",
            builtin=True,
        )
        candidate = dict(self._roots)
        candidate[builtin_id] = builtin
        self._save_roots(candidate)
        self._roots = candidate

    def _save_roots(self, roots: dict[str, SafeRoot]) -> None:
        """Atomically persist a candidate and sync file and directory data."""
        safe_roots_file = _safe_roots_file()
        data = {"roots": [root.model_dump() for root in roots.values()]}
        temporary_file: Path | None = None
        fd = -1
        try:
            safe_roots_file.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{safe_roots_file.name}.",
                suffix=".tmp",
                dir=safe_roots_file.parent,
            )
            temporary_file = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                fd = -1
                json.dump(data, output, indent=2, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            temporary_file.replace(safe_roots_file)
            if os.name != "nt":
                directory_fd = os.open(safe_roots_file.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception as exc:
            if fd >= 0:
                os.close(fd)
            if temporary_file is not None:
                temporary_file.unlink(missing_ok=True)
            logger.warning("Falha ao salvar safe_roots.json", exc_info=True)
            raise SafeRootPersistenceError(
                "Não foi possível persistir as pastas seguras."
            ) from exc

    def _save(self) -> None:
        """Persist the current registry atomically."""
        self._save_roots(self._roots)

    def all_roots(self, *, include_archived: bool = False) -> list[SafeRoot]:
        """Return active roots, or archived roots when requested."""
        with self._locked():
            return sorted(
                (
                    root
                    for root in self._roots.values()
                    if include_archived or root.archived_at is None
                ),
                key=lambda root: (not root.builtin, root.label.lower()),
            )

    def get(self, root_id: str) -> SafeRoot | None:
        """Return one root by ID."""
        with self._locked():
            return self._roots.get(root_id)

    def add(self, path: str, label: str, user_id: str) -> SafeRoot:
        """Add a root, restoring an archived entry with the same path."""
        with self._locked():
            resolved = str(Path(path).expanduser().resolve())
            root_id = self.derive_id(resolved)
            existing = self._roots.get(root_id)
            if existing is not None:
                if existing.archived_at is not None:
                    restored = existing.model_copy(update={"archived_at": None})
                    candidate = dict(self._roots)
                    candidate[root_id] = restored
                    self._save_roots(candidate)
                    self._roots = candidate
                    return restored
                return existing
            root = SafeRoot(
                id=root_id,
                path=resolved,
                label=label.strip() or Path(resolved).name or resolved,
                created_at=datetime.now(UTC).isoformat(),
                created_by=user_id,
                builtin=False,
            )
            candidate = dict(self._roots)
            candidate[root_id] = root
            self._save_roots(candidate)
            self._roots = candidate
            return root

    def update_label(self, root_id: str, label: str) -> SafeRoot | None:
        """Rename an entry; builtin roots may be renamed."""
        with self._locked():
            root = self._roots.get(root_id)
            if root is None:
                return None
            updated = root.model_copy(update={"label": label.strip() or root.label})
            candidate = dict(self._roots)
            candidate[root_id] = updated
            self._save_roots(candidate)
            self._roots = candidate
            return updated

    def remove(self, root_id: str) -> bool:
        """Remove a non-builtin root."""
        with self._locked():
            root = self._roots.get(root_id)
            if root is None or root.builtin:
                return False
            candidate = dict(self._roots)
            del candidate[root_id]
            self._save_roots(candidate)
            self._roots = candidate
            return True

    def archive(self, root_id: str) -> SafeRoot | None:
        """Archive a root without deleting its history."""
        with self._locked():
            root = self._roots.get(root_id)
            if root is None or root.builtin:
                return None
            archived = root.model_copy(
                update={"archived_at": datetime.now(UTC).isoformat()}
            )
            candidate = dict(self._roots)
            candidate[root_id] = archived
            self._save_roots(candidate)
            self._roots = candidate
            return archived

    def restore(self, root_id: str) -> SafeRoot | None:
        """Restore an archived root."""
        with self._locked():
            root = self._roots.get(root_id)
            if root is None:
                return None
            restored = root.model_copy(update={"archived_at": None})
            candidate = dict(self._roots)
            candidate[root_id] = restored
            self._save_roots(candidate)
            self._roots = candidate
            return restored

    def is_under_safe_root(self, path: str) -> SafeRoot | None:
        """Return the active root containing ``path``, if any."""
        with self._locked():
            target = Path(path).expanduser().resolve()
            for root in self._roots.values():
                if root.archived_at is not None:
                    continue
                try:
                    target.relative_to(Path(root.path))
                    return root
                except ValueError:
                    continue
            return None

    def closest_safe_root_for(self, path: str) -> SafeRoot | None:
        """Return the closest containing root, or the first active root."""
        with self._locked():
            target = Path(path).expanduser().resolve()
            roots = [root for root in self._roots.values() if root.archived_at is None]
            for root in roots:
                try:
                    target.relative_to(Path(root.path))
                    return root
                except ValueError:
                    continue
            roots.sort(key=lambda root: (not root.builtin, root.label.lower()))
            return roots[0] if roots else None


def get_safe_root_registry() -> SafeRootRegistry:
    """Return the process singleton."""
    return SafeRootRegistry.instance()
