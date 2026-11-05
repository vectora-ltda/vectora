"""Testes do serviço de ingest direto no RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.embedding import rag_ingest


class _FakeQueue:
    """Captura chamadas de enqueue para inspeção."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue(
        self,
        text: str,
        collection: str = "articles",
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "text": text,
                "collection": collection,
                "metadata": metadata,
                "job_id": job_id,
            }
        )
        return f"q-{len(self.calls)}"


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> _FakeQueue:
    queue = _FakeQueue()

    async def _get_queue(_dsn: str | None) -> _FakeQueue:
        return queue

    monkeypatch.setattr(rag_ingest, "get_embedding_queue", _get_queue)
    monkeypatch.setattr(rag_ingest, "is_safe_file_path", lambda _p: True)
    return queue


def _make_tree(root: Path) -> None:
    (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "b.md").write_text("# Título\n\nconteúdo de teste\n", encoding="utf-8")
    (root / "c.txt").write_text("texto puro\n", encoding="utf-8")


class TestMatchesFileTypeCustomExtensions:
    def test_custom_extension_list_filters_to_only_that_extension(self) -> None:
        assert rag_ingest._matches_file_type(Path("docs/class.xml"), ["xml"])
        assert not rag_ingest._matches_file_type(Path("readme.md"), ["xml"])

    def test_custom_extension_ignores_leading_dot(self) -> None:
        assert rag_ingest._matches_file_type(Path("class.xml"), [".xml"])

    def test_custom_extension_list_multiple_extensions(self) -> None:
        assert rag_ingest._matches_file_type(Path("scene.tscn"), ["xml", "tscn"])
        assert rag_ingest._matches_file_type(Path("docs.xml"), ["xml", "tscn"])
        assert not rag_ingest._matches_file_type(Path("readme.md"), ["xml", "tscn"])

    def test_empty_list_matches_everything_like_all_preset(self) -> None:
        assert rag_ingest._matches_file_type(Path("readme.md"), [])
        assert rag_ingest._matches_file_type(Path("class.xml"), [])

    def test_presets_still_work_as_plain_strings(self) -> None:
        assert rag_ingest._matches_file_type(Path("a.py"), "code")
        assert not rag_ingest._matches_file_type(Path("a.md"), "code")
        assert rag_ingest._matches_file_type(Path("a.md"), "markdown")
        assert rag_ingest._matches_file_type(Path("qualquer.ext"), "all")


class TestMatchesFileTypeIncludeExclude:
    def test_include_csv_restricts_formats(self) -> None:
        assert rag_ingest._matches_file_type(Path("a.xml"), include_exts="xml, tscn")
        assert rag_ingest._matches_file_type(
            Path("scene.tscn"), include_exts="xml, tscn"
        )
        assert not rag_ingest._matches_file_type(
            Path("readme.md"), include_exts="xml, tscn"
        )

    def test_include_list_of_extensions(self) -> None:
        assert rag_ingest._matches_file_type(Path("a.xml"), include_exts=["xml"])
        assert not rag_ingest._matches_file_type(Path("a.md"), include_exts=["xml"])

    def test_include_ignores_leading_dot(self) -> None:
        assert rag_ingest._matches_file_type(Path("a.xml"), include_exts=".xml")

    def test_exclude_removes_even_with_all_preset(self) -> None:
        assert not rag_ingest._matches_file_type(
            Path("pkg.lock"), exclude_exts="md, lock"
        )
        assert rag_ingest._matches_file_type(Path("readme.md"), exclude_exts="lock")

    def test_include_and_exclude_combined(self) -> None:
        # include restringe; exclude remove mesmo dentro do include.
        assert rag_ingest._matches_file_type(
            Path("a.xml"), include_exts="xml, md", exclude_exts="lock"
        )
        assert not rag_ingest._matches_file_type(
            Path("a.lock"), include_exts="xml, lock", exclude_exts="lock"
        )

    def test_exclude_folder_name_excludes_children(self) -> None:
        # Nome de pasta exclui arquivos dentro dela (VS Code files.exclude).
        assert not rag_ingest._matches_file_type(
            Path("node_modules/foo/index.js"), exclude_exts="node_modules"
        )
        assert rag_ingest._matches_file_type(
            Path("src/app.js"), exclude_exts="node_modules"
        )

    def test_exclude_glob_pattern_path(self) -> None:
        # Glob de caminho remove tudo que casa (ex. **/*.min.js).
        assert not rag_ingest._matches_file_type(
            Path("src/app.min.js"), exclude_exts="**/*.min.js"
        )
        assert rag_ingest._matches_file_type(
            Path("src/app.js"), exclude_exts="**/*.min.js"
        )

    def test_exclude_nested_folder(self) -> None:
        assert not rag_ingest._matches_file_type(
            Path("lib/vendor/utils/helper.ts"), exclude_exts="vendor"
        )
        assert rag_ingest._matches_file_type(
            Path("lib/utils/helper.ts"), exclude_exts="vendor"
        )

    def test_include_folder_glob_only_that_folder(self) -> None:
        assert rag_ingest._matches_file_type(
            Path("src/components/Button.tsx"), include_exts="src/components"
        )
        assert not rag_ingest._matches_file_type(
            Path("src/hooks/useThing.ts"), include_exts="src/components"
        )


class TestListMatchingFiles:
    def test_simple_extension_matches_relative_name(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        (tmp_path / "docs.xml").write_text("<doc>xml</doc>\n", encoding="utf-8")
        files, total = rag_ingest.list_matching_files(str(tmp_path), file_types=["xml"])
        assert total == 1
        assert files == ["docs.xml"]

    def test_path_glob_excludes_only_matching_files(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.js").write_text("1", encoding="utf-8")
        (tmp_path / "src" / "app.min.js").write_text("1", encoding="utf-8")
        files, total = rag_ingest.list_matching_files(
            str(tmp_path), exclude_exts="**/*.min.js"
        )
        assert total == 1
        assert files == ["src/app.js"]

    def test_exclude_folder_name_excludes_whole_subtree(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / "foo").mkdir(parents=True)
        (tmp_path / "node_modules" / "foo" / "index.js").write_text(
            "1", encoding="utf-8"
        )
        (tmp_path / "app.js").write_text("1", encoding="utf-8")
        files, total = rag_ingest.list_matching_files(
            str(tmp_path), exclude_exts="node_modules"
        )
        assert total == 1
        assert files == ["app.js"]

    def test_exclude_takes_precedence_over_include(self, tmp_path: Path) -> None:
        (tmp_path / "a.xml").write_text("1", encoding="utf-8")
        (tmp_path / "b.xml").write_text("1", encoding="utf-8")
        files, total = rag_ingest.list_matching_files(
            str(tmp_path), include_exts="xml", exclude_exts="a.xml"
        )
        assert total == 1
        assert files == ["b.xml"]

    def test_limit_truncates_list_but_total_reflects_full_count(
        self, tmp_path: Path
    ) -> None:
        for i in range(250):
            (tmp_path / f"f{i}.txt").write_text("1", encoding="utf-8")
        files, total = rag_ingest.list_matching_files(str(tmp_path), limit=200)
        assert len(files) == 200
        assert total == 250

    def test_rejects_unsafe_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rag_ingest, "is_safe_file_path", lambda _p: False)
        with pytest.raises(ValueError, match="fora do escopo"):
            rag_ingest.list_matching_files(str(tmp_path))

    def test_rejects_non_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rag_ingest, "is_safe_file_path", lambda _p: True)
        f = tmp_path / "file.py"
        f.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Não é um diretório"):
            rag_ingest.list_matching_files(str(f))


@pytest.mark.asyncio
async def test_ingest_markdown_only(tmp_path: Path, _patched: _FakeQueue) -> None:
    _make_tree(tmp_path)
    result = await rag_ingest.ingest_directory(
        str(tmp_path), file_types="markdown", workspace_id="ws-1"
    )
    assert result["status"] == "enqueued"
    assert result["total_files"] == 1  # só o .md
    assert result["total_chunks"] >= 1
    # Todos os chunks compartilham o mesmo job_id e carregam o workspace_id.
    job_ids = {c["job_id"] for c in _patched.calls}
    assert job_ids == {result["job_id"]}
    assert all(c["metadata"]["workspace_id"] == "ws-1" for c in _patched.calls)


@pytest.mark.asyncio
async def test_ingest_code_only(tmp_path: Path, _patched: _FakeQueue) -> None:
    _make_tree(tmp_path)
    result = await rag_ingest.ingest_directory(str(tmp_path), file_types="code")
    assert result["total_files"] == 1  # só o .py (txt/md não são "code")
    assert result["total_chunks"] >= 1


@pytest.mark.asyncio
async def test_ingest_all(tmp_path: Path, _patched: _FakeQueue) -> None:
    _make_tree(tmp_path)
    result = await rag_ingest.ingest_directory(str(tmp_path), file_types="all")
    assert result["total_files"] == 3


@pytest.mark.asyncio
async def test_ingest_custom_extension_only(
    tmp_path: Path, _patched: _FakeQueue
) -> None:
    _make_tree(tmp_path)
    (tmp_path / "docs.xml").write_text("<doc>xml</doc>\n", encoding="utf-8")
    result = await rag_ingest.ingest_directory(str(tmp_path), file_types=["xml"])
    assert result["total_files"] == 1  # só o .xml


@pytest.mark.asyncio
async def test_ingest_rejects_unsafe_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rag_ingest, "is_safe_file_path", lambda _p: False)
    with pytest.raises(ValueError, match="fora do escopo"):
        await rag_ingest.ingest_directory(str(tmp_path))


@pytest.mark.asyncio
async def test_ingest_not_a_directory(tmp_path: Path, _patched: _FakeQueue) -> None:
    f = tmp_path / "file.py"
    f.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Não é um diretório"):
        await rag_ingest.ingest_directory(str(f))
