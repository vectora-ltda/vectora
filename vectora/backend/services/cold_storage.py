"""Cold-storage export and rehydration for inactive conversation threads.

Archives are immutable JSONL gzip payloads.  The manifest is deliberately
small and carries the digest needed to reject a truncated or substituted
archive before it is reintroduced into the active transcript path.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

_MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024

type JsonValue = (
    bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None
)


class ColdArchiveRecord(BaseModel):
    kind: str
    thread_id: str
    value: dict[str, JsonValue]


class ColdArchiveManifest(BaseModel):
    thread_id: str
    sha256: str
    record_count: int = Field(ge=0)
    created_at: str
    retention_class: str = "cold"


@dataclass(frozen=True)
class ColdArchive:
    thread_id: str
    path: Path
    sha256: str
    record_count: int
    created_at: str


def _safe_thread_id(thread_id: str) -> str:
    value = thread_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("thread_id inválido para cold storage")
    return value


def _canonical_record(record: ColdArchiveRecord) -> bytes:
    return (
        json.dumps(
            record.model_dump(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def export_cold_archive(
    root: str | Path,
    thread_id: str,
    *,
    metadata: dict[str, JsonValue],
    transcript: Iterable[dict[str, JsonValue]],
    events: Iterable[dict[str, JsonValue]] = (),
) -> ColdArchive:
    """Write one immutable archive and its retention metadata atomically."""
    safe_id = _safe_thread_id(thread_id)
    destination_root = Path(root).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    final_path = destination_root / f"{safe_id}.jsonl.gz"
    temporary = final_path.with_suffix(final_path.suffix + ".tmp")
    records: list[ColdArchiveRecord] = [
        ColdArchiveRecord(kind="metadata", thread_id=safe_id, value=metadata)
    ]
    records.extend(
        ColdArchiveRecord(kind="transcript", thread_id=safe_id, value=item)
        for item in transcript
    )
    records.extend(
        ColdArchiveRecord(kind="event", thread_id=safe_id, value=item)
        for item in events
    )
    digest = hashlib.sha256()
    with gzip.open(temporary, "wb", compresslevel=6) as stream:
        for record in records:
            payload = _canonical_record(record)
            digest.update(payload)
            stream.write(payload)
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    temporary.replace(final_path)
    # Durabilidade do rename: o arquivo e a entrada do diretório precisam
    # sobreviver a uma queda de energia antes de publicar o manifesto.
    try:
        directory_fd = os.open(destination_root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        # Windows does not expose a fsync-able directory handle. The atomic
        # replace still provides the file-level guarantee on that platform.
        if os.name != "nt":
            final_path.unlink(missing_ok=True)
            raise
    created_at = datetime.now(UTC).isoformat()
    manifest = ColdArchiveManifest(
        thread_id=safe_id,
        sha256=digest.hexdigest(),
        record_count=len(records),
        created_at=created_at,
    )
    manifest_path = final_path.with_suffix(final_path.suffix + ".artifact.json")
    manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    manifest_tmp.write_text(
        json.dumps(manifest.model_dump(), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    with manifest_tmp.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    manifest_tmp.replace(manifest_path)
    return ColdArchive(
        thread_id=safe_id,
        path=final_path,
        sha256=digest.hexdigest(),
        record_count=len(records),
        created_at=created_at,
    )


def read_cold_archive(
    path: str | Path, *, expected_sha256: str, expected_thread_id: str | None = None
) -> list[dict[str, JsonValue]]:
    """Read and validate an archive before returning its logical records."""
    archive = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    records: list[dict[str, JsonValue]] = []
    total_bytes = 0
    try:
        with gzip.open(archive, "rb") as stream:
            for line in stream:
                total_bytes += len(line)
                if total_bytes > _MAX_DECOMPRESSED_BYTES:
                    raise ValueError("cold archive excede o limite descompactado")
                digest.update(line)
                value = json.loads(line.decode("utf-8"))
                parsed = ColdArchiveRecord.model_validate(value)
                if (
                    expected_thread_id is not None
                    and parsed.thread_id != expected_thread_id
                ):
                    raise ValueError("cold archive pertence a outra thread")
                records.append(parsed.model_dump())
    except (EOFError, gzip.BadGzipFile, OSError, zlib.error) as exc:
        raise ValueError("cold archive comprimido inválido ou truncado") from exc
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError("sha256 do cold archive não confere")
    return records


__all__ = [
    "ColdArchive",
    "ColdArchiveManifest",
    "ColdArchiveRecord",
    "export_cold_archive",
    "read_cold_archive",
]
