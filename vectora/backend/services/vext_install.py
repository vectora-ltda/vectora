"""Atomic installation, activation and rollback for VEXT artifacts."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from backend.services.vext import ensure_supported_platform
from backend.services.vext_artifact import verify_vext
from backend.services.vext_registry import VextTrustStore


@dataclass(frozen=True, slots=True)
class InstalledExtension:
    """A version recorded by the local extension store."""

    extension_id: str
    version: str
    artifact: Path
    active: bool


class VextInstallStore:
    """Manage immutable versions with atomic activation and rollback."""

    def __init__(self, root: str | Path) -> None:
        self.root: Path = Path(root)
        self.versions: Path = self.root / "versions"
        self.state_path: Path = self.root / "active.json"
        self.lock_path: Path = self.root / ".install.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Serialize publication and activation across processes."""
        self.root.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        use_fcntl = False
        try:
            try:
                import fcntl

                flock = getattr(fcntl, "flock")  # noqa: B009
                lock_ex = getattr(fcntl, "LOCK_EX")  # noqa: B009
                lock_un = getattr(fcntl, "LOCK_UN")  # noqa: B009
                use_fcntl = True
                flock(handle.fileno(), lock_ex)
            except ImportError:
                import msvcrt

                locking = getattr(msvcrt, "locking")  # noqa: B009
                lock = getattr(msvcrt, "LK_LOCK")  # noqa: B009
                locking(handle.fileno(), lock, 1)
            yield
        finally:
            if use_fcntl:
                flock(handle.fileno(), lock_un)
            else:
                locking = getattr(msvcrt, "locking")  # noqa: B009
                unlock = getattr(msvcrt, "LK_UNLCK")  # noqa: B009
                locking(handle.fileno(), unlock, 1)
            handle.close()

    def _read_state(self) -> dict[str, str]:
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("estado de extensões inválido") from exc
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise ValueError("estado de extensões inválido")
        return value

    def _write_state(self, state: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.tmp"
        )
        payload = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        try:
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.state_path)
            try:
                directory_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Directory fsync is unavailable on some Windows filesystems.
                pass
        finally:
            temporary.unlink(missing_ok=True)

    def install(
        self,
        artifact: str | Path,
        *,
        trust_store: VextTrustStore | None = None,
        allow_unsigned: bool = False,
    ) -> InstalledExtension:
        """Verify and atomically install one immutable artifact version."""
        result = verify_vext(artifact)
        ensure_supported_platform(result.manifest)
        if trust_store is not None:
            trust_store.verify(artifact)
        elif not allow_unsigned:
            raise PermissionError("artefato VEXT exige publisher confiável")
        extension_id = result.manifest.id
        version = result.manifest.version
        with self._lock():
            destination = self.versions / extension_id / version
            destination.parent.mkdir(parents=True, exist_ok=True)
            existing = destination / "package.vext"
            if existing.is_file():
                existing_result = verify_vext(existing)
                if existing_result.content_digest != result.content_digest:
                    raise ValueError("versão já instalada com conteúdo diferente")
            else:
                temporary = Path(
                    tempfile.mkdtemp(prefix=f".{version}.", dir=destination.parent)
                )
                try:
                    shutil.copy2(result.path, temporary / "package.vext")
                    (temporary / "manifest.json").write_text(
                        result.manifest.model_dump_json(indent=2), encoding="utf-8"
                    )
                    temporary.replace(destination)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
            state = self._read_state()
            state[extension_id] = version
            self._write_state(state)
        return InstalledExtension(
            extension_id, version, destination / "package.vext", True
        )

    def active(self, extension_id: str) -> InstalledExtension | None:
        """Return the active installed version, if any."""
        version = self._read_state().get(extension_id)
        if version is None:
            return None
        artifact = self.versions / extension_id / version / "package.vext"
        if not artifact.is_file():
            raise ValueError("versão ativa não existe")
        return InstalledExtension(extension_id, version, artifact, True)

    def rollback(
        self,
        extension_id: str,
        version: str,
        *,
        trust_store: VextTrustStore | None = None,
        allow_unsigned: bool = False,
    ) -> InstalledExtension:
        """Activate a previously installed version under the same trust policy."""
        with self._lock():
            artifact = self.versions / extension_id / version / "package.vext"
            if not artifact.is_file():
                raise ValueError("versão para rollback não está instalada")
            result = verify_vext(artifact)
            ensure_supported_platform(result.manifest)
            if trust_store is not None:
                trust_store.verify(artifact)
            elif not allow_unsigned:
                raise PermissionError("artefato VEXT exige publisher confiável")
            if result.manifest.id != extension_id or result.manifest.version != version:
                raise ValueError("versão instalada não corresponde ao manifesto")
            state = self._read_state()
            state[extension_id] = version
            self._write_state(state)
            return InstalledExtension(extension_id, version, artifact, True)

    def list_installed(self) -> list[InstalledExtension]:
        """List all installed versions and their active state."""
        state = self._read_state()
        records: list[InstalledExtension] = []
        if not self.versions.exists():
            return records
        for artifact in sorted(self.versions.glob("*/*/package.vext")):
            result = verify_vext(artifact)
            records.append(
                InstalledExtension(
                    result.manifest.id,
                    result.manifest.version,
                    artifact,
                    state.get(result.manifest.id) == result.manifest.version,
                )
            )
        return records


__all__ = ["InstalledExtension", "VextInstallStore"]
