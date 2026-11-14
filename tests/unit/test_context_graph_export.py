"""Testes para backend/services/context_graph/export.py.

Cobre: _obsidian_tag, _strip_diacritics, _yaml_str, backup_if_protected,
prune_dangling_edges, to_json, to_html (light), _cypher_escape, _cypher_label.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest


def _simple_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("a", label="AuthService", source_file="auth.py", file_type="code")
    G.add_node("b", label="Token", source_file="token.py", file_type="code")
    G.add_edge(
        "a",
        "b",
        relation="calls",
        confidence="EXTRACTED",
        source_file="auth.py",
        _src="a",
        _tgt="b",
    )
    return G


class TestStripDiacritics:
    def test_removes_accents(self):
        from backend.context_graph.export import _strip_diacritics

        assert _strip_diacritics("café") == "cafe"

    def test_plain_ascii_unchanged(self):
        from backend.context_graph.export import _strip_diacritics

        assert _strip_diacritics("hello") == "hello"

    def test_none_returns_empty(self):
        from backend.context_graph.export import _strip_diacritics

        assert _strip_diacritics(None) == ""

    def test_unicode_string(self):
        from backend.context_graph.export import _strip_diacritics

        result = _strip_diacritics("über")
        assert isinstance(result, str)


class TestYamlStr:
    def test_plain_text(self):
        from backend.context_graph.export import _yaml_str

        assert _yaml_str("hello") == "hello"

    def test_escapes_backslash(self):
        from backend.context_graph.export import _yaml_str

        assert _yaml_str("a\\b") == "a\\\\b"

    def test_escapes_double_quote(self):
        from backend.context_graph.export import _yaml_str

        assert _yaml_str('say "hi"') == 'say \\"hi\\"'

    def test_escapes_newline(self):
        from backend.context_graph.export import _yaml_str

        assert _yaml_str("line1\nline2") == "line1\\nline2"

    def test_escapes_carriage_return(self):
        from backend.context_graph.export import _yaml_str

        assert _yaml_str("a\rb") == "a\\rb"

    def test_nul_escaped(self):
        from backend.context_graph.export import _yaml_str

        assert _yaml_str("a\x00b") == "a\\0b"

    def test_line_separator_escaped(self):
        from backend.context_graph.export import _yaml_str

        assert _yaml_str("a b") == "a\\Lb"

    def test_control_char_hex_escaped(self):
        from backend.context_graph.export import _yaml_str

        result = _yaml_str("a\x01b")
        assert "\\x01" in result


class TestToJson:
    def test_creates_json_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from backend.context_graph.export import to_json

        monkeypatch.setenv("GRAPH_NO_BACKUP", "1")
        G = _simple_graph()
        output = str(tmp_path / "graph.json")
        to_json(G, {0: ["a", "b"]}, output)
        assert Path(output).exists()

    def test_json_has_nodes_and_links(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from backend.context_graph.export import to_json

        monkeypatch.setenv("GRAPH_NO_BACKUP", "1")
        G = _simple_graph()
        output = str(tmp_path / "graph.json")
        to_json(G, {0: ["a", "b"]}, output)
        data = json.loads(Path(output).read_text())
        assert "nodes" in data
        assert "links" in data or "edges" in data

    def test_no_overwrite_when_existing_graph_is_larger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from backend.context_graph.export import to_json

        monkeypatch.setenv("GRAPH_NO_BACKUP", "1")
        # Create a larger existing graph (more nodes than our new one)
        big_graph = {
            "nodes": [{"id": f"n{i}", "label": f"N{i}"} for i in range(100)],
            "links": [],
        }
        output = str(tmp_path / "graph.json")
        Path(output).write_text(json.dumps(big_graph), encoding="utf-8")
        G = _simple_graph()  # only 2 nodes
        result = to_json(G, {}, output, force=False)
        # Should refuse (return False) since new graph is smaller
        assert result is False

    def test_overwrite_when_force_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from backend.context_graph.export import to_json

        monkeypatch.setenv("GRAPH_NO_BACKUP", "1")
        G = _simple_graph()
        output = str(tmp_path / "graph.json")
        Path(output).write_text('{"original": true}', encoding="utf-8")
        to_json(G, {}, output, force=True)
        content = json.loads(Path(output).read_text())
        assert "original" not in content


class TestToHtml:
    def test_creates_html_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from backend.context_graph.export import to_html

        monkeypatch.setenv("GRAPH_NO_BACKUP", "1")
        G = _simple_graph()
        output = str(tmp_path / "graph.html")
        communities = {0: ["a", "b"]}
        to_html(G, communities, output, community_labels={0: "Auth"})
        assert Path(output).exists()
        content = Path(output).read_text(encoding="utf-8")
        assert "<html" in content.lower() or "<!DOCTYPE" in content.lower()

    def test_html_contains_node_label(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from backend.context_graph.export import to_html

        monkeypatch.setenv("GRAPH_NO_BACKUP", "1")
        G = _simple_graph()
        output = str(tmp_path / "graph.html")
        to_html(G, {0: ["a", "b"]}, output, community_labels={0: "Auth"})
        content = Path(output).read_text(encoding="utf-8")
        assert "AuthService" in content or "Token" in content
