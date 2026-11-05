"""Testes do pipeline do Context Graph.

Cobre: GraphResult dataclass, _run_ast_extraction (caminho feliz + erro),
e build_workspace_graph com workspace inexistente.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.context_graph.pipeline import (
    GraphResult,
    _graph_out_dir,
    _load_ast_checkpoint,
    _run_ast_extraction,
    _write_ast_checkpoint,
)

# ---------------------------------------------------------------------------
# GraphResult
# ---------------------------------------------------------------------------


def test_graph_result_defaults():
    result = GraphResult(
        workspace_id="abc",
        workspace_path=Path(),
        graph_path=Path(),
        report_path=Path(),
        html_path=Path(),
    )
    assert result.node_count == 0
    assert result.edge_count == 0
    assert result.error is None
    assert result.god_nodes == []
    assert result.suggested_questions == []


def test_graph_result_with_error():
    result = GraphResult(
        workspace_id="abc",
        workspace_path=Path(),
        graph_path=Path(),
        report_path=Path(),
        html_path=Path(),
        error="falha",
    )
    assert result.error == "falha"
    assert result.node_count == 0


def test_graph_result_metrics():
    result = GraphResult(
        workspace_id="abc",
        workspace_path=Path("/tmp"),
        graph_path=Path("/tmp/graph.json"),
        report_path=Path("/tmp/GRAPH_REPORT.md"),
        html_path=Path("/tmp/graph.html"),
        node_count=42,
        edge_count=100,
        god_nodes=["AuthService"],
        suggested_questions=["Como autenticar?"],
    )
    assert result.node_count == 42
    assert result.edge_count == 100
    assert "AuthService" in result.god_nodes
    assert len(result.suggested_questions) == 1


# ---------------------------------------------------------------------------
# _graph_out_dir
# ---------------------------------------------------------------------------


def test_graph_out_dir():
    d = _graph_out_dir(Path("/tmp/ws"))
    assert d == Path("/tmp/ws/.vectora/context-graph")


# ---------------------------------------------------------------------------
# _run_ast_extraction
# ---------------------------------------------------------------------------


def test_run_ast_extraction_empty_list():
    result = _run_ast_extraction([], Path())
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["hyperedges"] == []


def test_run_ast_extraction_unsupported_ext(tmp_path):
    f = tmp_path / "file.unknownext123"
    f.write_text("hello", encoding="utf-8")
    result = _run_ast_extraction([str(f)], tmp_path)
    assert isinstance(result["nodes"], list)
    assert isinstance(result["edges"], list)


def test_run_ast_extraction_python_file(tmp_path):
    py_file = tmp_path / "sample.py"
    py_file.write_text("def foo():\n    pass\n", encoding="utf-8")
    result = _run_ast_extraction([str(py_file)], tmp_path)
    assert isinstance(result["nodes"], list)
    assert isinstance(result["edges"], list)
    assert isinstance(result["hyperedges"], list)


def test_run_ast_extraction_nonexistent_file(tmp_path):
    result = _run_ast_extraction([str(tmp_path / "does_not_exist.py")], tmp_path)
    assert result["nodes"] == []


def test_run_ast_extraction_multiple_files(tmp_path):
    py_file1 = tmp_path / "a.py"
    py_file2 = tmp_path / "b.py"
    py_file1.write_text("class A:\n    pass\n", encoding="utf-8")
    py_file2.write_text("class B:\n    pass\n", encoding="utf-8")
    result = _run_ast_extraction([str(py_file1), str(py_file2)], tmp_path)
    assert isinstance(result["nodes"], list)
    assert isinstance(result["edges"], list)


# ---------------------------------------------------------------------------
# build_workspace_graph — workspace inexistente
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_workspace_graph_no_workspace():
    from backend.context_graph.pipeline import build_workspace_graph

    registry = MagicMock()
    registry.get = MagicMock(return_value=None)
    with patch("backend.workspace.workspace.workspace_registry", registry):
        result = await build_workspace_graph("nonexistent-id")

    assert result.error is not None
    assert result.node_count == 0


# ---------------------------------------------------------------------------
# build_workspace_graph — nenhum arquivo detectado (modo ast)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_workspace_graph_no_files(tmp_path):
    from backend.context_graph.pipeline import build_workspace_graph

    ws_mock = MagicMock()
    ws_mock.cwd = str(tmp_path)
    registry = MagicMock()
    registry.get = MagicMock(return_value=ws_mock)

    fake_detect = {"files": {}}

    import backend.context_graph.analyze as analyze_mod
    import backend.context_graph.build as build_mod
    import backend.context_graph.cluster as cluster_mod
    import backend.context_graph.detect as detect_mod
    import backend.context_graph.export as export_mod
    import backend.context_graph.report as report_mod

    with (
        patch("backend.workspace.workspace.workspace_registry", registry),
        patch.object(detect_mod, "detect", return_value=fake_detect),
    ):
        result = await build_workspace_graph("test-id", mode="ast")

    assert result.error is None
    assert result.node_count == 0


# ---------------------------------------------------------------------------
# Checkpoint do AST — resume de build pausado por quota (Parte C)
# ---------------------------------------------------------------------------


class TestAstCheckpoint:
    def test_write_then_load_roundtrip(self, tmp_path: Path):
        ast = {"nodes": [{"id": "a"}], "edges": [], "hyperedges": []}
        _write_ast_checkpoint(tmp_path, ast)
        assert _load_ast_checkpoint(tmp_path) == ast

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert _load_ast_checkpoint(tmp_path) is None

    def test_load_invalid_json_returns_none(self, tmp_path: Path):
        (tmp_path / "checkpoint_ast.json").write_text("{nope", encoding="utf-8")
        assert _load_ast_checkpoint(tmp_path) is None

    def test_load_non_dict_returns_none(self, tmp_path: Path):
        (tmp_path / "checkpoint_ast.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert _load_ast_checkpoint(tmp_path) is None

    def test_write_failure_does_not_raise(self, tmp_path: Path):
        # Diretório inexistente → escrita falha, mas é defensiva (não levanta).
        _write_ast_checkpoint(tmp_path / "missing-subdir", {"nodes": []})
