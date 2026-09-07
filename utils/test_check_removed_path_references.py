"""Tests for the staged removed-path reference check."""

from __future__ import annotations

from check_removed_path_references import (
    RemovedPath,
    _find_references,
    _staged_files,
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


def test_ignores_staged_submodule_gitlink(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git_output(*args: str) -> bytes:
        calls.append(args)
        if args == ("ls-files", "-s", "-z"):
            return b"160000 abc123 0\tvendor/submodule\0100644 def456 0\tREADME.md\0"
        assert args == ("show", ":README.md")
        return b"README content\n"

    monkeypatch.setattr("check_removed_path_references._git_output", fake_git_output)

    assert _staged_files() == {"README.md": "README content\n"}
    assert ("show", ":vendor/submodule") not in calls
