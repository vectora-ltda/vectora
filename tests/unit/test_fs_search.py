"""Testes para busca de texto em arquivos do workspace (A.5).

Cobre ``_python_text_search`` (fallback Python puro):
- Retorna hits para texto correspondente
- Retorna vazio quando sem correspondência
- Busca é case-insensitive
- Exclui diretórios reservados (.git, node_modules…)
- Trunca em max_hits e marca truncated=True
- Pula arquivos maiores que 1 MiB
- Registra line_number correto
"""

from __future__ import annotations

from pathlib import Path

from backend.api.handlers.workspaces import _python_text_search


def test_search_finds_matches(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("def hello():\n    print('world')\n")
    hits, truncated = _python_text_search(tmp_path, "hello")
    assert len(hits) >= 1
    assert any(h.path == "hello.py" for h in hits)
    assert not truncated


def test_search_no_match(tmp_path: Path) -> None:
    (tmp_path / "backend.py").write_text("x = 1\n")
    hits, truncated = _python_text_search(tmp_path, "zzz_not_present")
    assert hits == []
    assert not truncated


def test_search_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("# Project README\n")
    hits, _ = _python_text_search(tmp_path, "readme")
    assert len(hits) >= 1


def test_search_excludes_git_dir(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "main.py").write_text("# main\n")
    hits, _ = _python_text_search(tmp_path, "main")
    paths = [h.path for h in hits]
    assert not any(".git" in p for p in paths)
    assert "main.py" in paths


def test_search_excludes_node_modules(tmp_path: Path) -> None:
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("export const find = () => {}")
    (tmp_path / "app.ts").write_text("const find = true")
    hits, _ = _python_text_search(tmp_path, "find")
    paths = [h.path for h in hits]
    assert not any("node_modules" in p for p in paths)
    assert "app.ts" in paths


def test_search_truncates_at_max_hits(tmp_path: Path) -> None:
    content = "\n".join(f"match {i}" for i in range(250))
    (tmp_path / "big.txt").write_text(content)
    hits, truncated = _python_text_search(tmp_path, "match", max_hits=10)
    assert len(hits) == 10
    assert truncated


def test_search_skips_large_files(tmp_path: Path) -> None:
    large = tmp_path / "large.bin"
    large.write_bytes(b"needle " * (200 * 1024))  # ~1.3 MiB
    hits, _ = _python_text_search(tmp_path, "needle")
    assert all(h.path != "large.bin" for h in hits)


def test_search_correct_line_number(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("line one\nfoo bar\nline three\n")
    hits, _ = _python_text_search(tmp_path, "foo")
    assert len(hits) == 1
    assert hits[0].line_number == 2
    assert "foo" in hits[0].line_text


def test_search_truncates_long_lines(tmp_path: Path) -> None:
    long_line = "x" * 300 + " needle " + "y" * 300
    (tmp_path / "long.txt").write_text(long_line)
    hits, _ = _python_text_search(tmp_path, "needle", max_columns=50)
    assert len(hits) == 1
    assert len(hits[0].line_text) <= 50
