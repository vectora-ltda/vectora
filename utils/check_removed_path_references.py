#!/usr/bin/env python3
"""Detect staged references to files removed or renamed by the commit.

The check reads the Git index so it validates the exact snapshot that will be
committed, including staged edits and renames.  It intentionally reports path
mentions rather than trying to infer every language's import semantics.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class RemovedPath:
    """A path that will no longer exist after the staged commit."""

    path: str
    replacement: str | None = None


def _git_output(*args: str) -> bytes:
    result = subprocess.run(  # nosec B603, B607
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _staged_removed_paths(raw: bytes) -> list[RemovedPath]:
    """Parse ``git diff --name-status -z`` deletion and rename records."""
    fields = raw.split(b"\0")
    removed: list[RemovedPath] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii")
        index += 1
        if status.startswith("R"):
            if index + 1 >= len(fields):
                break
            old = fields[index].decode("utf-8", errors="surrogateescape")
            new = fields[index + 1].decode("utf-8", errors="surrogateescape")
            removed.append(RemovedPath(old, new))
            index += 2
        elif status == "D":
            if index >= len(fields):
                break
            removed.append(
                RemovedPath(fields[index].decode("utf-8", errors="surrogateescape"))
            )
            index += 1
        else:
            index += 1
    return removed


def _normalise_path(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/")))


def _find_references(
    files: dict[str, str], removed: list[RemovedPath]
) -> list[tuple[str, int, str, str]]:
    """Return ``(file, line, removed_path, line_text)`` for path mentions."""
    references: list[tuple[str, int, str, str]] = []
    removed_paths = {_normalise_path(item.path): item for item in removed}
    for filename, text in files.items():
        if filename in removed_paths:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            normalised_line = line.replace("\\", "/")
            for path in removed_paths:
                if path in normalised_line:
                    references.append((filename, line_number, path, line.strip()))
    return references


def _staged_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in _git_output("ls-files", "-s", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        if metadata.split(b" ", 1)[0] == b"160000":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        blob = _git_output("show", f":{path}")
        if b"\0" in blob:
            continue
        try:
            files[path] = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


def main() -> int:
    try:
        removed = _staged_removed_paths(
            _git_output(
                "diff",
                "--cached",
                "--name-status",
                "-z",
                "-M",
                "--diff-filter=DR",
            )
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"removed-path-references: unable to inspect Git index: {exc}")
        return 1

    if not removed:
        return 0

    references = _find_references(_staged_files(), removed)
    if not references:
        return 0

    print("References to staged removed or renamed paths were found:")
    for filename, line_number, removed_path, line in references:
        print(f"  {filename}:{line_number}: {removed_path} <- {line}")
    print(
        "Update or remove these references before committing. "
        "Renamed paths may use the replacement path shown in the diff."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
