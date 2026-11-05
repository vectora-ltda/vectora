"""Testes estendidos para backend/services/context_graph/pipeline.py.

Cobre: _graph_out_dir, GraphResult, build_workspace_graph (workspace not found),
_run_ast_extraction (com cache e sem extractor).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_registry(cwd: Path) -> MagicMock:
    ws = MagicMock()
    ws.cwd = str(cwd)
    registry = MagicMock()
    registry.get = MagicMock(return_value=ws)
    return registry


class TestGraphOutDir:
    def test_returns_expected_path(self, tmp_path: Path):
        from backend.context_graph.pipeline import _graph_out_dir

        result = _graph_out_dir(tmp_path)
        assert result == tmp_path / ".vectora" / "context-graph"


class TestGraphResult:
    def test_default_values(self, tmp_path: Path):
        from backend.context_graph.pipeline import GraphResult

        result = GraphResult(
            workspace_id="ws1",
            workspace_path=tmp_path,
            graph_path=tmp_path / "graph.json",
            report_path=tmp_path / "report.md",
            html_path=tmp_path / "graph.html",
        )
        assert result.node_count == 0
        assert result.edge_count == 0
        assert result.error is None
        assert result.god_nodes == []

    def test_with_error(self, tmp_path: Path):
        from backend.context_graph.pipeline import GraphResult

        result = GraphResult(
            workspace_id="ws1",
            workspace_path=tmp_path,
            graph_path=tmp_path / "graph.json",
            report_path=tmp_path / "report.md",
            html_path=tmp_path / "graph.html",
            error="something failed",
        )
        assert result.error == "something failed"


class TestBuildWorkspaceGraphWorkspaceNotFound:
    @pytest.mark.asyncio
    async def test_missing_workspace_returns_error(self):
        from backend.context_graph.pipeline import build_workspace_graph

        registry = MagicMock()
        registry.get = MagicMock(return_value=None)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            result = await build_workspace_graph("ws_missing")
        assert result.error is not None
        assert "não encontrado" in result.error


class TestBuildWorkspaceGraphNoFiles:
    @pytest.mark.asyncio
    async def test_no_files_returns_empty_result(self, tmp_path: Path):
        from backend.context_graph.pipeline import build_workspace_graph

        registry = _make_registry(tmp_path)

        def _fake_detect(workspace_path):
            return {"files": {}}

        with (
            patch("backend.workspace.workspace.workspace_registry", registry),
            patch("backend.context_graph.detect.detect", _fake_detect),
        ):
            result = await build_workspace_graph("ws_test", mode="ast")

        assert result.error is None
        assert result.node_count == 0


class TestRunAstExtraction:
    def test_uses_cache_when_available(self, tmp_path: Path):
        from backend.context_graph.pipeline import _run_ast_extraction

        f = tmp_path / "cached.py"
        f.write_text("x = 1\n", encoding="utf-8")

        cached_data = {"nodes": [{"id": "from_cache"}], "edges": [], "hyperedges": []}
        with patch("backend.context_graph.cache.load_cached", return_value=cached_data):
            result = _run_ast_extraction([str(f)], tmp_path)

        assert any(n["id"] == "from_cache" for n in result["nodes"])

    def test_no_extractor_skips_file(self, tmp_path: Path):
        from backend.context_graph.pipeline import _run_ast_extraction

        f = tmp_path / "binary.abc123def"
        f.write_bytes(b"\x00\x01\x02")

        result = _run_ast_extraction([str(f)], tmp_path)
        assert result["nodes"] == []

    def test_empty_file_list(self, tmp_path: Path):
        from backend.context_graph.pipeline import _run_ast_extraction

        result = _run_ast_extraction([], tmp_path)
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_real_python_file_extracted(self, tmp_path: Path):
        from backend.context_graph.pipeline import _run_ast_extraction

        f = tmp_path / "hello.py"
        f.write_text("def hello(): pass\n", encoding="utf-8")

        result = _run_ast_extraction([str(f)], tmp_path)
        assert isinstance(result["nodes"], list)
        assert isinstance(result["edges"], list)
