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
from typing import Any


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


def _canonical_record(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def export_cold_archive(
    root: str | Path,
    thread_id: str,
    *,
    metadata: dict[str, Any],
    transcript: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]] = (),
) -> ColdArchive:
    """Write one immutable archive and its retention metadata atomically."""
    safe_id = _safe_thread_id(thread_id)
    destination_root = Path(root).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    final_path = destination_root / f"{safe_id}.jsonl.gz"
    temporary = final_path.with_suffix(final_path.suffix + ".tmp")
    records: list[dict[str, Any]] = [
        {"kind": "metadata", "thread_id": safe_id, "value": metadata}
    ]
    records.extend(
        {"kind": "transcript", "thread_id": safe_id, "value": item}
        for item in transcript
    )
    records.extend(
        {"kind": "event", "thread_id": safe_id, "value": item} for item in events
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
    manifest = {
        "thread_id": safe_id,
        "sha256": digest.hexdigest(),
        "record_count": len(records),
        "created_at": created_at,
        "retention_class": "cold",
    }
    manifest_path = final_path.with_suffix(final_path.suffix + ".artifact.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return ColdArchive(
        thread_id=safe_id,
        path=final_path,
        sha256=digest.hexdigest(),
        record_count=len(records),
        created_at=created_at,
    )


def read_cold_archive(
    path: str | Path, *, expected_sha256: str, expected_thread_id: str | None = None
) -> list[dict[str, Any]]:
    """Read and validate an archive before returning its logical records."""
    archive = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    records: list[dict[str, Any]] = []
    try:
        with gzip.open(archive, "rb") as stream:
            for line in stream:
                digest.update(line)
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("cold archive contém registro inválido")
                if (
                    expected_thread_id is not None
                    and value.get("thread_id") != expected_thread_id
                ):
                    raise ValueError("cold archive pertence a outra thread")
                records.append(value)
    except (EOFError, gzip.BadGzipFile, OSError, zlib.error) as exc:
        raise ValueError("cold archive comprimido inválido ou truncado") from exc
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError("sha256 do cold archive não confere")
    return records


__all__ = ["ColdArchive", "export_cold_archive", "read_cold_archive"]
