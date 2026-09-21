"""Normalize unordered list markers in the generated changelog."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_LIST_MARKER = re.compile(r"^(\s*)\*\s+")
_FENCE = re.compile(r"^[ \t]{0,3}([`~]{3,})(.*?)(?:\r?\n)?$")
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*(?:\r?\n)?$")


def _heading_intro(heading: str, level: int) -> str:
    """Return stable prose for a generated heading without an introduction."""
    normalized = heading.casefold()
    if level == 2:
        return "Esta seção reúne as alterações publicadas nesta versão."
    if "feature" in normalized:
        return "Os recursos incluídos nesta versão são listados abaixo."
    if "bug" in normalized or "fix" in normalized:
        return "As correções incluídas nesta versão são listadas abaixo."
    return "Os itens desta seção são listados abaixo."


def _ensure_latest_release_intros(lines: list[str]) -> list[str]:
    """Insert prose before generated lists in the newest release section.

    Older releases are left untouched so a normalization commit cannot rewrite
    the entire historical changelog. The newest section is the only one that
    Release Please updates on each run.
    """
    release_start = next(
        (index for index, line in enumerate(lines) if line.startswith("## ")), None
    )
    if release_start is None:
        return lines
    release_end = next(
        (
            index
            for index in range(release_start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    result = lines[:release_start]
    index = release_start
    while index < release_end:
        line = lines[index]
        result.append(line)
        heading_match = _HEADING.match(line.rstrip("\r\n"))
        if heading_match:
            lookahead = index + 1
            while lookahead < release_end and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < release_end:
                first = lines[lookahead].lstrip()
                if first.startswith(("#", "- ", "* ")):
                    ending = "\r\n" if line.endswith("\r\n") else "\n"
                    result.extend(
                        [
                            ending,
                            _heading_intro(
                                heading_match.group(2), len(heading_match.group(1))
                            )
                            + ending,
                            ending,
                        ]
                    )
                    index = lookahead
                    continue
        index += 1
    result.extend(lines[release_end:])
    return result


def normalize_changelog(path: Path) -> bool:
    """Replace unordered ``*`` markers with ``-`` outside fenced code blocks.

    Returns whether the file was changed. Running this function repeatedly is
    safe because the replacement only targets a single-star list marker.
    """
    original = path.read_text(encoding="utf-8")
    normalized_lines: list[str] = []
    fence_char: str | None = None
    fence_length = 0

    for line in original.splitlines(keepends=True):
        fence_match = _FENCE.match(line)
        if fence_char is None and fence_match:
            delimiter = fence_match.group(1)
            fence_char = delimiter[0]
            fence_length = len(delimiter)
        elif fence_char is not None and fence_match:
            delimiter = fence_match.group(1)
            trailing = fence_match.group(2).strip()
            if (
                delimiter[0] == fence_char
                and len(delimiter) >= fence_length
                and not trailing
            ):
                fence_char = None
                fence_length = 0
        normalized_line = line
        if fence_char is None and not fence_match:
            normalized_line = _LIST_MARKER.sub(r"\1- ", line)
        normalized_lines.append(normalized_line)

    normalized = "".join(_ensure_latest_release_intros(normalized_lines))
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
