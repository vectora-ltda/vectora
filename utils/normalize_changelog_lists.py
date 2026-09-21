"""Normalize unordered list markers in the generated changelog."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_LIST_MARKER = re.compile(r"^(\s*)\*\s+")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def normalize_changelog(path: Path) -> bool:
    """Replace unordered ``*`` markers with ``-`` outside fenced code blocks.

    Returns whether the file was changed. Running this function repeatedly is
    safe because the replacement only targets a single-star list marker.
    """
    original = path.read_text(encoding="utf-8")
    normalized_lines: list[str] = []
    in_fence = False

    for line in original.splitlines(keepends=True):
        fence_match = _FENCE.match(line)
        if fence_match:
            in_fence = not in_fence
        if not in_fence and not fence_match:
            line = _LIST_MARKER.sub(r"\1- ", line)
        normalized_lines.append(line)

    normalized = "".join(normalized_lines)
    if normalized == original:
        return False

    with path.open("w", encoding="utf-8", newline="") as changelog:
        changelog.write(normalized)
    return True


def main() -> int:
    """Normalize the changelog at the supplied path or the repository default."""
    path = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else Path(__file__).parents[1] / "vectora" / "CHANGELOG.md"
    )
    changed = normalize_changelog(path)
    print(f"{'Normalized' if changed else 'Already normalized'} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
