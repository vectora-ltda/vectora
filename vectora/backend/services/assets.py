"""Opaque multimodal asset metadata and ownership checks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.settings import settings

ALLOWED_MIME = {"image/png", "image/jpeg", "audio/mpeg", "video/mp4"}
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
        records = self._read()
        records[item.id] = asdict(item)
        self.index.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return item

    def get(self, asset_id: str, *, owner_id: str, thread_id: str = "") -> Asset | None:
        raw = self._read().get(asset_id)
        if (
            not raw
            or raw.get("owner_id") != owner_id
            or (thread_id and raw.get("thread_id") != thread_id)
        ):
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
            return json.loads(self.index.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


asset_store = AssetStore()
