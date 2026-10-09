"""Validate and deterministically sort Vectora translation message keys."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

METADATA_KEYS: Final = ("$schema", "$comment")
DEFAULT_FILES: Final = tuple(
    sorted(
        file
        for directory in ("vectora/frontend/messages", "company/messages")
        for file in Path(directory).glob("*.json")
    )
)


def sorted_messages(data: dict[str, object]) -> dict[str, object]:
    """Keep metadata first and sort every translation key lexicographically."""
    metadata = {key: data[key] for key in METADATA_KEYS if key in data}
    messages = {key: data[key] for key in data if key not in metadata}
    return {**metadata, **dict(sorted(messages.items(), key=lambda item: item[0]))}


def check_file(path: Path, *, write: bool) -> bool:
    """Return whether a file is ordered; optionally rewrite it."""
    original = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(original, dict):
        raise ValueError(f"arquivo de traduções inválido: {path}")
    ordered = sorted_messages(original)
    content = json.dumps(ordered, ensure_ascii=False, indent=2) + "\n"
    current = path.read_text(encoding="utf-8")
    if current == content:
        return True
    if write:
        path.write_text(content, encoding="utf-8")
        return True
    print(f"chaves de tradução fora de ordem: {path}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    paths = tuple(args.paths) or DEFAULT_FILES
    return 0 if all(check_file(path, write=args.write) for path in paths) else 1


if __name__ == "__main__":
    raise SystemExit(main())
