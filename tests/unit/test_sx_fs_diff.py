"""Parser de ``git status --porcelain=v1`` e ``DiffFile`` (handler de diff)."""

from __future__ import annotations


def test_parse_porcelain_modified_unstaged() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1(" M src/foo.py\n")
    assert out == [(" ", "M", "src/foo.py")]


def test_parse_porcelain_modified_staged() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1("M  src/foo.py\n")
    assert out == [("M", " ", "src/foo.py")]


def test_parse_porcelain_modified_both() -> None:
    """``XY=MM`` — staged E unstaged simultâneos sobre o mesmo path."""
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1("MM src/foo.py\n")
    assert out == [("M", "M", "src/foo.py")]


def test_parse_porcelain_untracked() -> None:
    """``??`` indica path untracked."""
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1("?? new_file.md\n")
    assert out == [("?", "?", "new_file.md")]


def test_parse_porcelain_added_staged() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1("A  src/new.py\n")
    assert out == [("A", " ", "src/new.py")]


def test_parse_porcelain_deleted_unstaged() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1(" D src/gone.py\n")
    assert out == [(" ", "D", "src/gone.py")]


def test_parse_porcelain_rename_uses_destination() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1("R  old.py -> new.py\n")
    assert out == [("R", " ", "new.py")]


def test_parse_porcelain_normalizes_windows_paths() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1("M  src\\foo\\bar.py\n")
    assert out == [("M", " ", "src/foo/bar.py")]


def test_parse_porcelain_multiple_entries() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    raw = "M  src/a.py\n M src/b.py\n?? src/c.py\nMM src/d.py\n"
    out = _parse_porcelain_v1(raw)
    assert len(out) == 4
    paths = [p for _, _, p in out]
    assert paths == ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]


def test_parse_porcelain_ignores_short_lines() -> None:
    from backend.api.handlers.workspaces import _parse_porcelain_v1

    out = _parse_porcelain_v1("AB\nM\n")
    assert out == []


def test_diff_file_accepts_decomposed_flags() -> None:
    """``DiffFile`` aceita ``staged_change``/``unstaged_change``/``untracked``."""
    from backend.api.handlers.workspaces import DiffFile

    f = DiffFile(
        path="src/foo.py",
        status="M",
        staged_change="M",
        unstaged_change="M",
        untracked=False,
    )
    assert f.staged_change == "M"
    assert f.unstaged_change == "M"
    assert f.untracked is False


def test_diff_file_status_only_defaults_flags_to_none() -> None:
    """Construir ``DiffFile`` só com ``status`` resulta em flags ``None``."""
    from backend.api.handlers.workspaces import DiffFile

    f = DiffFile(path="x.py", status="M", additions=3, deletions=1)
    assert f.status == "M"
    assert f.staged_change is None
    assert f.unstaged_change is None
    assert f.untracked is False


# ---------------------------------------------------------------------------
# Arquivo untracked exibido como diff puro de adição
# ---------------------------------------------------------------------------


def test_untracked_as_diff_single_line() -> None:
    """Arquivo untracked com 1 linha gera 1 hunk com header @@ -0,0 +1,1 @@."""
    from backend.api.handlers.workspaces import _untracked_as_diff

    hunks = _untracked_as_diff("hello\n")
    assert len(hunks) == 1
    assert hunks[0].header == "@@ -0,0 +1,1 @@"
    assert [line.text for line in hunks[0].lines] == ["+hello"]
    assert hunks[0].lines[0].new_line_number == 1


def test_untracked_as_diff_multiline() -> None:
    """Arquivo untracked multiline gera linhas prefixadas com +."""
    from backend.api.handlers.workspaces import _untracked_as_diff

    content = "linha1\nlinha2\nlinha3\n"
    hunks = _untracked_as_diff(content)
    assert hunks[0].header == "@@ -0,0 +1,3 @@"
    assert [line.text for line in hunks[0].lines] == [
        "+linha1",
        "+linha2",
        "+linha3",
    ]
    assert [line.new_line_number for line in hunks[0].lines] == [1, 2, 3]


def test_untracked_as_diff_empty_content() -> None:
    """Arquivo vazio retorna lista vazia de hunks."""
    from backend.api.handlers.workspaces import _untracked_as_diff

    hunks = _untracked_as_diff("")
    assert hunks == []


def test_untracked_as_diff_no_trailing_newline() -> None:
    """Conteúdo sem newline final deve incluir todas as linhas."""
    from backend.api.handlers.workspaces import _untracked_as_diff

    hunks = _untracked_as_diff("abc")
    assert [line.text for line in hunks[0].lines] == ["+abc"]
    assert hunks[0].header == "@@ -0,0 +1,1 @@"


def test_parse_unified_diff_staged_only() -> None:
    """Diff de arquivo staged (git diff --cached HEAD) produz hunks corretos."""
    from backend.api.handlers.workspaces import _parse_unified_diff

    diff = "@@ -0,0 +1,2 @@\n+nova linha 1\n+nova linha 2\n"
    hunks = _parse_unified_diff(diff)
    assert len(hunks) == 1
    assert any(line.text == "+nova linha 1" for line in hunks[0].lines)
    assert any(line.text == "+nova linha 2" for line in hunks[0].lines)
