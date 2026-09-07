"""Tests for the staged removed-path reference check."""

from __future__ import annotations

from check_removed_path_references import (
    RemovedPath,
    _find_references,
    _staged_removed_paths,
)


def test_parses_deletion_and_rename_records() -> None:
    raw = b"D\0documents/old.md\0R100\0docs/old.md\0docs/new.md\0"
    assert _staged_removed_paths(raw) == [
        RemovedPath("documents/old.md"),
        RemovedPath("docs/old.md", "docs/new.md"),
    ]


def test_reports_reference_to_deleted_path() -> None:
    files = {
        "README.md": "See documents/old.md for details.\n",
        "documents/old.md": "Historical content.\n",
    }
    assert _find_references(files, [RemovedPath("documents/old.md")]) == [
        ("README.md", 1, "documents/old.md", "See documents/old.md for details.")
    ]


def test_ignores_deleted_file_and_clean_snapshot() -> None:
    files = {"documents/old.md": "documents/old.md\n", "README.md": "Nothing here.\n"}
    assert _find_references(files, [RemovedPath("documents/old.md")]) == []


def test_reports_backslash_reference_to_renamed_path() -> None:
    files = {"config.json": '{"source": "docs\\old.md"}\n'}
    assert _find_references(files, [RemovedPath("docs/old.md", "docs/new.md")]) == [
        ("config.json", 1, "docs/old.md", '{"source": "docs\\old.md"}')
    ]
