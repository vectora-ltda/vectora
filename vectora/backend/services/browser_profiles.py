"""Perfis locais e isolados do Browser Workbench."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backend.settings import settings


@dataclass(slots=True)
class BrowserProfile:
    profile_id: str
    owner_id: str
    scope: str
    name: str
    locale: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    retention_days: int = 30


class BrowserProfileStore:
    """Índice JSON atômico; dados sensíveis permanecem no Chromium local."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or settings.vectora_home / "browser-profiles"
        self._index = self._root / "profiles.json"
        self._lock = asyncio.Lock()

    async def _read(self) -> list[BrowserProfile]:
        def read() -> list[BrowserProfile]:
            if not self._index.exists():
                return []
            raw = json.loads(self._index.read_text(encoding="utf-8"))
            return [BrowserProfile(**item) for item in raw]

        return await asyncio.to_thread(read)

    async def _write(self, profiles: list[BrowserProfile]) -> None:
        def write() -> None:
            self._root.mkdir(parents=True, exist_ok=True)
            temporary = self._index.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    [asdict(item) for item in profiles], ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            temporary.replace(self._index)

        await asyncio.to_thread(write)

    async def list_profiles(self, owner_id: str) -> list[BrowserProfile]:
        async with self._lock:
            return [
                profile
                for profile in await self._read()
                if profile.owner_id == owner_id
            ]

    async def create(
        self, owner_id: str, name: str, scope: str = "workspace", locale: str = ""
    ) -> BrowserProfile:
        if scope not in {"global", "workspace", "session"}:
            raise ValueError("scope inválido")
        async with self._lock:
            profiles = await self._read()
            profile = BrowserProfile(
                profile_id=str(uuid.uuid4()),
                owner_id=owner_id,
                name=name.strip() or "Vectora Browser",
                scope=scope,
                locale=locale,
            )
            profiles.append(profile)
            await self._write(profiles)
            return profile

    async def delete(self, owner_id: str, profile_id: str) -> None:
        async with self._lock:
            profiles = await self._read()
            filtered = [
                p
                for p in profiles
                if not (p.owner_id == owner_id and p.profile_id == profile_id)
            ]
            if len(filtered) == len(profiles):
                raise KeyError("perfil não encontrado")
            await self._write(filtered)


browser_profile_store = BrowserProfileStore()
