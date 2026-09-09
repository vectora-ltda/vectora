"""Perfis locais e isolados do Browser Workbench."""

from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, cast

from backend.settings import settings


class _Fcntl(Protocol):
    LOCK_EX: int
    LOCK_UN: int

    def flock(self, file_descriptor: int, operation: int) -> None: ...


class _Msvcrt(Protocol):
    LK_LOCK: int
    LK_UNLCK: int

    def locking(self, file_descriptor: int, mode: int, nbytes: int) -> None: ...


try:
    _fcntl: _Fcntl | None = cast("_Fcntl", importlib.import_module("fcntl"))
except ImportError:  # pragma: no cover - usado no Windows
    _fcntl = None

try:
    _msvcrt: _Msvcrt | None = cast("_Msvcrt", importlib.import_module("msvcrt"))
except ImportError:  # pragma: no cover - usado em sistemas POSIX
    _msvcrt = None


@dataclass(slots=True)
class BrowserProfile:
    profile_id: str
    owner_id: str
    scope: BrowserScope
    name: str
    locale: str = ""
    scope_target: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    retention_days: int = 30
    expires_at: str | None = None


BrowserScope = Literal["global", "workspace", "session"]


class BrowserProfileStore:
    """Índice JSON atômico, com transações seguras entre processos.

    O índice guarda apenas metadados. Cookies, tokens e histórico continuam
    no armazenamento nativo do Chromium, que deve ser fechado antes da limpeza.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or settings.vectora_home / "browser-profiles"
        self._index = self._root / "profiles.json"
        self._lock_path = self._root / "profiles.lock"
        self._lock = asyncio.Lock()

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        self._root.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            else:  # pragma: no cover - exercitado somente no Windows
                if _msvcrt is None:
                    raise RuntimeError("backend de lock de processo indisponível")
                handle.seek(0)
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
                else:  # pragma: no cover - exercitado somente no Windows
                    if _msvcrt is None:
                        raise RuntimeError("backend de lock de processo indisponível")
                    handle.seek(0)
                    _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)

    def _read_sync(self) -> list[BrowserProfile]:
        if not self._index.exists():
            return []
        raw = json.loads(self._index.read_text(encoding="utf-8"))
        return [BrowserProfile(**item) for item in raw]

    def _write_sync(self, profiles: list[BrowserProfile]) -> None:
        payload = json.dumps(
            [asdict(item) for item in profiles], ensure_ascii=False, indent=2
        )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self._root, delete=False
        ) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.replace(self._index)

    @staticmethod
    def _expired(profile: BrowserProfile, now: datetime) -> bool:
        expiration = (
            datetime.fromisoformat(profile.expires_at)
            if profile.expires_at is not None
            else datetime.fromisoformat(profile.created_at)
            + timedelta(days=profile.retention_days)
        )
        return expiration <= now

    def _purge_expired_sync(
        self, profiles: list[BrowserProfile]
    ) -> list[BrowserProfile]:
        now = datetime.now(UTC)
        active = [profile for profile in profiles if not self._expired(profile, now)]
        if len(active) != len(profiles):
            self._write_sync(active)
        return active

    async def list_profiles(
        self,
        owner_id: str,
        scope_target: str | None = None,
        scope: BrowserScope | None = None,
    ) -> list[BrowserProfile]:
        async with self._lock:

            def read() -> list[BrowserProfile]:
                with self._process_lock():
                    profiles = self._purge_expired_sync(self._read_sync())
                    return [
                        profile
                        for profile in profiles
                        if profile.owner_id == owner_id
                        and (scope is None or profile.scope == scope)
                        and (
                            scope_target is None
                            or profile.scope == "global"
                            or profile.scope_target == scope_target
                        )
                    ]

            return await asyncio.to_thread(read)

    async def create(
        self,
        owner_id: str,
        name: str,
        scope: BrowserScope = "workspace",
        locale: str = "",
        scope_target: str | None = None,
        retention_days: int = 30,
    ) -> BrowserProfile:
        if scope not in {"global", "workspace", "session"}:
            raise ValueError("scope inválido")
        if scope != "global" and not scope_target:
            raise ValueError("scope_target é obrigatório para perfil não global")
        if retention_days < 1:
            raise ValueError("retention_days deve ser positivo")
        async with self._lock:

            def write() -> BrowserProfile:
                with self._process_lock():
                    profiles = self._purge_expired_sync(self._read_sync())
                    profile = BrowserProfile(
                        profile_id=str(uuid.uuid4()),
                        owner_id=owner_id,
                        name=name.strip() or "Vectora Browser",
                        scope=scope,
                        locale=locale,
                        scope_target=scope_target,
                        retention_days=retention_days,
                        expires_at=(
                            datetime.now(UTC) + timedelta(days=retention_days)
                        ).isoformat(),
                    )
                    profiles.append(profile)
                    self._write_sync(profiles)
                    return profile

            return await asyncio.to_thread(write)

    async def resolve(
        self,
        owner_id: str,
        profile_id: str,
        scope_target: str | None,
        scope: BrowserScope,
    ) -> BrowserProfile:
        """Resolve a profile only within its explicitly declared scope."""
        if scope not in {"global", "workspace", "session"}:
            raise ValueError("scope inválido")
        profiles = await self.list_profiles(owner_id, scope_target, scope)
        for profile in profiles:
            if profile.profile_id == profile_id:
                return profile
        raise KeyError("perfil não encontrado")

    async def delete(self, owner_id: str, profile_id: str) -> None:
        async with self._lock:

            def remove() -> None:
                with self._process_lock():
                    profiles = self._purge_expired_sync(self._read_sync())
                    filtered = [
                        profile
                        for profile in profiles
                        if not (
                            profile.owner_id == owner_id
                            and profile.profile_id == profile_id
                        )
                    ]
                    if len(filtered) == len(profiles):
                        raise KeyError("perfil não encontrado")
                    self._write_sync(filtered)

            await asyncio.to_thread(remove)


browser_profile_store = BrowserProfileStore()
