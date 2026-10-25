"""Retention and garbage collection for file-backed artifacts.

The transcript and checkpoint stores keep references to artifacts, so a
cleanup pass must be conservative: only payloads older than the policy are
eligible, and a referenced payload is never removed.  The default is a dry
run to make maintenance observable before it becomes destructive.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from backend.vtypes.documents import ArtifactIntegrity


@dataclass(frozen=True)
class ArtifactGcResult:
    """Stable accounting for one retention pass."""

    scanned: int = 0
    eligible: int = 0
    deleted: int = 0
    skipped_referenced: int = 0
    skipped_recent: int = 0
    skipped_policy: int = 0
    skipped_symlink: int = 0
    failed: int = 0
    bytes_freed: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    stopped: bool = False


def _normalise_reference(value: str | Path) -> str:
    """Executa a operação  normalise reference."""
    text = str(value)
    parsed = urlparse(text)
    if parsed.scheme == "file":
        text = unquote(parsed.path)
        if len(text) >= 3 and text[0] == "/" and text[2] == ":":
            text = text[1:]
        if parsed.netloc:
            text = f"//{parsed.netloc}{text}"
    return str(Path(text).resolve(strict=False)).casefold()


def _retention_class(sidecar: Path) -> str:
    """Executa a operação  retention class."""
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "run"
    try:
        return ArtifactIntegrity.model_validate(payload).retention_class
    except (TypeError, ValueError, ValidationError):
        return "run"


def _iter_payloads(
    root: Path, stop_event: threading.Event | None = None
) -> Iterable[Path]:
    # Markdown artifacts are the canonical payload.  Sidecars are metadata,
    # never independent payloads, and therefore are handled with their file.
    """Executa a operação  iter payloads."""
    for path in root.rglob("*.md"):
        if stop_event is not None and stop_event.is_set():
            return
        if path.is_file():
            yield path


def collect_artifacts(
    root: Path,
    *,
    referenced: Iterable[str | Path] = (),
    older_than: datetime | None = None,
    retention_classes: Mapping[str, timedelta] | None = None,
    now: datetime | None = None,
    dry_run: bool = True,
    stop_event: threading.Event | None = None,
    max_scan_seconds: float | None = 30.0,
) -> ArtifactGcResult:
    """Collect unreferenced artifacts according to retention policy.

    ``referenced`` accepts absolute paths or artifact paths returned by the
    store.  A payload and its ``.artifact.json``/``.artifact_type`` sidecars
    are removed as one unit.  Any filesystem failure is reported in the
    result and does not abort the rest of the scan.
    """
    root = root.resolve(strict=True)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    default_cutoff = (
        older_than.astimezone(UTC) if older_than else current - timedelta(days=30)
    )
    policies = retention_classes or {
        "run": timedelta(days=30),
        "session": timedelta(days=90),
    }
    protected = {_normalise_reference(value) for value in referenced}
    scanned = eligible = deleted = skipped_referenced = skipped_recent = 0
    skipped_policy = skipped_symlink = failed = bytes_freed = 0
    errors: list[str] = []
    stopped = False
    deadline = (
        time.monotonic() + max_scan_seconds if max_scan_seconds is not None else None
    )

    for path in _iter_payloads(root, stop_event):
        if stop_event is not None and stop_event.is_set():
            stopped = True
            break
        if deadline is not None and time.monotonic() >= deadline:
            stopped = True
            break
        scanned += 1
        if path.is_symlink():
            skipped_symlink += 1
            continue
        normalised = _normalise_reference(path)
        if normalised in protected:
            skipped_referenced += 1
            continue
        sidecar = path.with_suffix(".artifact.json")
        retention = _retention_class(sidecar)
        if retention in {"pinned", "permanent", "audit"}:
            skipped_policy += 1
            continue
        cutoff = default_cutoff
        if older_than is None and retention not in policies:
            skipped_policy += 1
            continue
        if older_than is None and retention in policies:
            cutoff = current - policies[retention]
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            size = path.stat().st_size
        except OSError as exc:
            failed += 1
            errors.append(f"{path}: {exc}")
            continue
        if modified >= cutoff:
            skipped_recent += 1
            continue
        eligible += 1
        if dry_run:
            continue
        try:
            path.unlink()
            for metadata in (sidecar, path.with_suffix(".artifact_type")):
                metadata.unlink(missing_ok=True)
            deleted += 1
            bytes_freed += size
        except OSError as exc:
            failed += 1
            errors.append(f"{path}: {exc}")

    return ArtifactGcResult(
        scanned=scanned,
        eligible=eligible,
        deleted=deleted,
        skipped_referenced=skipped_referenced,
        skipped_recent=skipped_recent,
        skipped_policy=skipped_policy,
        skipped_symlink=skipped_symlink,
        failed=failed,
        bytes_freed=bytes_freed,
        errors=tuple(errors),
        stopped=stopped,
    )
