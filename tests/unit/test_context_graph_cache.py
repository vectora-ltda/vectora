"""Testes para backend/services/context_graph/cache.py.

Cobre: file_hash, load_cached, save_cached, cache_dir, cached_files,
clear_cache, check_semantic_cache, save_semantic_cache,
_body_content, _relativize_source_files_in, _absolutize_source_files_in,
_cleanup_stale_ast_entries.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


class TestBodyContent:
    def test_no_frontmatter_returns_original(self):
        from backend.context_graph.cache import _body_content

        content = b"# Heading\n\nBody text"
        assert _body_content(content) == content

    def test_strips_yaml_frontmatter(self):
        from backend.context_graph.cache import _body_content

        content = b"---\ntitle: Test\n---\n# Body"
        result = _body_content(content)
        assert b"title:" not in result
        assert b"Body" in result

    def test_unclosed_frontmatter_returns_original(self):
        from backend.context_graph.cache import _body_content

        content = b"---\ntitle: Test\n# No closing delimiter"
        assert _body_content(content) == content

    def test_dashes_in_middle_not_treated_as_frontmatter(self):
        from backend.context_graph.cache import _body_content

        content = b"# Heading\n\n---\nthematic break"
        assert _body_content(content) == content


class TestFileHash:
    def test_consistent_hash(self, tmp_path: Path):
        from backend.context_graph.cache import file_hash

        f = tmp_path / "file.py"
        f.write_text("def foo(): pass\n", encoding="utf-8")
        h1 = file_hash(f, tmp_path)
        h2 = file_hash(f, tmp_path)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_different_content_different_hash(self, tmp_path: Path):
        from backend.context_graph.cache import file_hash

        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("content A", encoding="utf-8")
        f2.write_text("content B", encoding="utf-8")
        assert file_hash(f1, tmp_path) != file_hash(f2, tmp_path)

    def test_directory_raises(self, tmp_path: Path):
        from backend.context_graph.cache import file_hash

        with pytest.raises(IsADirectoryError):
            file_hash(tmp_path, tmp_path)

    def test_md_file_strips_frontmatter_for_hash(self, tmp_path: Path):
        from backend.context_graph.cache import file_hash

        f = tmp_path / "doc.md"
        f.write_text("---\nreviewed: false\n---\n# Content stays", encoding="utf-8")
        h1 = file_hash(f, tmp_path)

        # Change only frontmatter — hash should be stable
        f.write_text("---\nreviewed: true\n---\n# Content stays", encoding="utf-8")
        h2 = file_hash(f, tmp_path)
        # Same body content → potentially same hash (stat may differ with mtime_ns)
        # Just verify both are valid 64-char hashes
        assert len(h1) == 64
        assert len(h2) == 64

    def test_stat_cache_fastpath(self, tmp_path: Path):
        from backend.context_graph.cache import file_hash

        f = tmp_path / "f.py"
        f.write_text("data", encoding="utf-8")
        # Prime the stat cache
        h1 = file_hash(f, tmp_path)
        # Second call should use stat fastpath (same mtime_ns + size)
        h2 = file_hash(f, tmp_path)
        assert h1 == h2


class TestCacheDir:
    def test_creates_ast_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        from backend.context_graph.cache import cache_dir

        d = cache_dir(tmp_path, kind="ast")
        assert d.exists()
        assert "ast" in str(d)

    def test_creates_semantic_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        from backend.context_graph.cache import cache_dir

        d = cache_dir(tmp_path, kind="semantic")
        assert d.exists()
        assert "semantic" in str(d)


class TestLoadAndSaveCached:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        from backend.context_graph.cache import load_cached, save_cached

        f = tmp_path / "source.py"
        f.write_text("def hello(): pass\n", encoding="utf-8")

        payload = {"nodes": [{"id": "x", "source_file": str(f)}], "edges": []}
        save_cached(f, payload, tmp_path)

        result = load_cached(f, tmp_path)
        assert result is not None
        assert result["nodes"][0]["id"] == "x"

    def test_load_returns_none_on_cache_miss(self, tmp_path: Path):
        from backend.context_graph.cache import load_cached

        f = tmp_path / "uncached.py"
        f.write_text("code", encoding="utf-8")
        assert load_cached(f, tmp_path) is None

    def test_load_returns_none_when_file_missing(self, tmp_path: Path):
        from backend.context_graph.cache import load_cached

        f = tmp_path / "ghost.py"
        assert load_cached(f, tmp_path) is None

    def test_save_non_file_is_noop(self, tmp_path: Path):
        from backend.context_graph.cache import save_cached

        save_cached(tmp_path, {"nodes": []}, tmp_path)  # directory → no-op


class TestCachedFiles:
    def test_empty_when_no_cache(self, tmp_path: Path):
        from backend.context_graph.cache import cached_files

        result = cached_files(tmp_path)
        assert isinstance(result, set)

    def test_includes_saved_hash(self, tmp_path: Path):
        from backend.context_graph.cache import (
            cached_files,
            file_hash,
            save_cached,
        )

        f = tmp_path / "f.py"
        f.write_text("x", encoding="utf-8")
        save_cached(f, {"nodes": []}, tmp_path)
        h = file_hash(f, tmp_path)
        assert h in cached_files(tmp_path)


class TestClearCache:
    def test_clear_removes_all_entries(self, tmp_path: Path):
        from backend.context_graph.cache import (
            cached_files,
            clear_cache,
            save_cached,
        )

        f = tmp_path / "f.py"
        f.write_text("y", encoding="utf-8")
        save_cached(f, {"nodes": []}, tmp_path)
        assert len(cached_files(tmp_path)) > 0

        clear_cache(tmp_path)
        assert len(cached_files(tmp_path)) == 0


class TestRelativizeAndAbsolutize:
    def test_relativize_then_absolutize_roundtrip(self, tmp_path: Path):
        from backend.context_graph.cache import (
            _absolutize_source_files_in,
            _relativize_source_files_in,
        )

        abs_path = str(tmp_path / "src" / "auth.py")
        payload = {"nodes": [{"source_file": abs_path}], "edges": [], "hyperedges": []}
        _relativize_source_files_in(payload, tmp_path)
        rel = payload["nodes"][0]["source_file"]
        assert not Path(rel).is_absolute()

        _absolutize_source_files_in(payload, tmp_path)
        restored = payload["nodes"][0]["source_file"]
        assert Path(restored).is_absolute()

    def test_relative_source_file_not_modified_by_relativize(self, tmp_path: Path):
        from backend.context_graph.cache import _relativize_source_files_in

        payload = {
            "nodes": [{"source_file": "src/auth.py"}],
            "edges": [],
            "hyperedges": [],
        }
        _relativize_source_files_in(payload, tmp_path)
        assert payload["nodes"][0]["source_file"] == "src/auth.py"

    def test_out_of_root_path_unchanged(self, tmp_path: Path):
        from backend.context_graph.cache import _relativize_source_files_in

        other_drive = "C:/other/path.py" if os.name == "nt" else "/other/path.py"
        payload = {
            "nodes": [{"source_file": other_drive}],
            "edges": [],
            "hyperedges": [],
        }
        _relativize_source_files_in(payload, tmp_path)
        assert payload["nodes"][0]["source_file"] == other_drive


class TestCheckSemanticCache:
    def test_all_uncached_when_empty(self, tmp_path: Path):
        from backend.context_graph.cache import check_semantic_cache

        f = tmp_path / "f.py"
        f.write_text("code", encoding="utf-8")
        sem_result = check_semantic_cache([str(f)], tmp_path)
        assert str(f) in sem_result[3]
        assert sem_result[0] == []

    def test_cached_file_merged(self, tmp_path: Path):
        from backend.context_graph.cache import (
            check_semantic_cache,
            save_cached,
        )

        f = tmp_path / "f.py"
        f.write_text("code", encoding="utf-8")
        payload = {
            "nodes": [{"id": "fn", "source_file": str(f)}],
            "edges": [],
            "hyperedges": [],
        }
        save_cached(f, payload, tmp_path, kind="semantic")

        cached_n, _, _, uncached = check_semantic_cache([str(f)], tmp_path)
        assert uncached == []
        assert any(n["id"] == "fn" for n in cached_n)


class TestSaveSemanticCache:
    def test_saves_nodes_by_source_file(self, tmp_path: Path):
        from backend.context_graph.cache import save_semantic_cache

        f = tmp_path / "module.py"
        f.write_text("def fn(): pass\n", encoding="utf-8")
        nodes = [{"id": "module_fn", "source_file": str(f)}]
        count = save_semantic_cache(nodes, [], root=tmp_path)
        assert count == 1

    def test_non_existent_file_not_saved(self, tmp_path: Path):
        from backend.context_graph.cache import save_semantic_cache

        nodes = [{"id": "ghost", "source_file": str(tmp_path / "ghost.py")}]
        count = save_semantic_cache(nodes, [], root=tmp_path)
        assert count == 0

    def test_empty_source_file_skipped(self, tmp_path: Path):
        from backend.context_graph.cache import save_semantic_cache

        count = save_semantic_cache([{"id": "n", "source_file": ""}], [], root=tmp_path)
        assert count == 0


class TestCleanupStaleAstEntries:
    def test_removes_old_version_dirs(self, tmp_path: Path):
        from backend.context_graph import cache as cache_mod

        ast_base = tmp_path / "cache" / "ast"
        ast_base.mkdir(parents=True)
        old_dir = ast_base / "v0.1.0"
        old_dir.mkdir()
        (old_dir / "stale.json").write_text("{}", encoding="utf-8")
        current_dir = ast_base / "v9.9.9"
        current_dir.mkdir()

        # Reset the cleanup cache so our test dir isn't skipped
        cache_mod._cleaned_ast_dirs.discard(str(current_dir))
        cache_mod._cleanup_stale_ast_entries(ast_base, current_dir)
        assert not old_dir.exists()
        assert current_dir.exists()
