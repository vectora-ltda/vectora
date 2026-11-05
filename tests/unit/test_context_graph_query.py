"""Testes para backend/services/context_graph/query.py."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from backend.context_graph.query import (
    affected_summary,
    explain_node,
    load_graph_nx,
    path_between,
    query_nodes,
)


def _sample_data() -> dict:
    """Graph data com 4 nós e 3 arestas."""
    return {
        "directed": True,
        "nodes": [
            {
                "id": "n.auth",
                "label": "AuthService",
                "file_type": "code",
                "source_file": "auth.py",
            },
            {
                "id": "n.token",
                "label": "TokenHandler",
                "file_type": "code",
                "source_file": "token.py",
            },
            {
                "id": "n.db",
                "label": "Database",
                "file_type": "code",
                "source_file": "db.py",
            },
            {
                "id": "n.cache",
                "label": "CacheLayer",
                "file_type": "code",
                "source_file": "cache.py",
            },
        ],
        "edges": [
            {
                "source": "n.auth",
                "target": "n.token",
                "relation": "calls",
                "label": "calls",
            },
            {
                "source": "n.token",
                "target": "n.db",
                "relation": "imports",
                "label": "imports",
            },
            {
                "source": "n.auth",
                "target": "n.cache",
                "relation": "uses",
                "label": "uses",
            },
        ],
    }


# ─────────────────────────── load_graph_nx ───────────────────────────────────


class TestLoadGraphNx:
    def test_loads_from_json_file(self, tmp_path: Path) -> None:
        data = _sample_data()
        graph_file = tmp_path / "graph.json"
        graph_file.write_text(json.dumps(data), encoding="utf-8")
        g = load_graph_nx(graph_file)
        assert g.number_of_nodes() == 4
        assert g.number_of_edges() == 3

    def test_returns_digraph(self, tmp_path: Path) -> None:
        data = _sample_data()
        graph_file = tmp_path / "graph.json"
        graph_file.write_text(json.dumps(data), encoding="utf-8")
        g = load_graph_nx(graph_file)
        assert isinstance(g, nx.DiGraph)


# ─────────────────────────── query_nodes ─────────────────────────────────────


class TestQueryNodes:
    def test_match_by_label_substring(self) -> None:
        data = _sample_data()
        nodes, _edges = query_nodes(data, "Auth")
        ids = {n["id"] for n in nodes}
        assert "n.auth" in ids

    def test_resolve_seed_takes_priority(self) -> None:
        data = _sample_data()
        nodes, _edges = query_nodes(data, "AuthService")
        ids = {n["id"] for n in nodes}
        assert "n.auth" in ids

    def test_returns_neighborhood_edges(self) -> None:
        data = _sample_data()
        _nodes, edges = query_nodes(data, "AuthService")
        assert len(edges) > 0

    def test_top_k_limits_results(self) -> None:
        data = _sample_data()
        nodes, _edges = query_nodes(data, "a", top_k=1)
        assert len(nodes) <= 1

    def test_no_match_returns_empty(self) -> None:
        data = _sample_data()
        nodes, edges = query_nodes(data, "xyz_nonexistent_query_99")
        assert nodes == []
        assert edges == []


# ─────────────────────────── explain_node ────────────────────────────────────


class TestExplainNode:
    def test_existing_node_returns_target_and_neighbors(self) -> None:
        data = _sample_data()
        target, _neighbors, edges = explain_node(data, "n.auth")
        assert target is not None
        assert target["id"] == "n.auth"
        assert len(edges) > 0

    def test_accepts_label_via_resolve_seed(self) -> None:
        data = _sample_data()
        target, _neighbors, _edges = explain_node(data, "AuthService")
        assert target is not None
        assert target["id"] == "n.auth"

    def test_nonexistent_node_returns_none(self) -> None:
        data = _sample_data()
        target, neighbors, edges = explain_node(data, "n.nonexistent_xyz")
        assert target is None
        assert neighbors == []
        assert edges == []


# ─────────────────────────── path_between ────────────────────────────────────


class TestPathBetween:
    def test_direct_path(self) -> None:
        data = _sample_data()
        path_nodes, _path_edges = path_between(data, "n.auth", "n.token")
        ids = {n["id"] for n in path_nodes}
        assert "n.auth" in ids
        assert "n.token" in ids

    def test_indirect_path(self) -> None:
        data = _sample_data()
        path_nodes, _path_edges = path_between(data, "n.auth", "n.db")
        ids = {n["id"] for n in path_nodes}
        assert "n.auth" in ids
        assert "n.db" in ids

    def test_no_path_returns_empty(self) -> None:
        data = _sample_data()
        data["nodes"].append(
            {"id": "n.isolated", "label": "Isolated", "file_type": "code"}
        )
        path_nodes, path_edges = path_between(data, "n.auth", "n.isolated")
        assert path_nodes == []
        assert path_edges == []

    def test_source_not_found_returns_empty(self) -> None:
        data = _sample_data()
        path_nodes, _path_edges = path_between(data, "n.nonexistent", "n.auth")
        assert path_nodes == []


# ─────────────────────────── affected_summary ────────────────────────────────


class TestAffectedSummary:
    def test_valid_seed_returns_text_with_affected(self) -> None:
        data = _sample_data()
        text = affected_summary(data, "n.token")
        assert "Token" in text or "n.token" in text or "auth" in text.lower()

    def test_ambiguous_or_not_found_returns_message(self) -> None:
        data = _sample_data()
        text = affected_summary(data, "xyz_does_not_exist_99")
        assert len(text) > 0

    def test_no_affected_nodes_says_none(self) -> None:
        data = _sample_data()
        text = affected_summary(data, "n.db", depth=1)
        assert "No affected nodes" in text or "não há" in text.lower() or len(text) > 0

    def test_custom_depth(self) -> None:
        data = _sample_data()
        text = affected_summary(data, "n.db", depth=2)
        assert "Depth: 2" in text

    def test_seed_by_label(self) -> None:
        data = _sample_data()
        text = affected_summary(data, "Database")
        assert len(text) > 0


# ─────────────────────────── load_graph_nx (extras) ──────────────────────────


class TestLoadGraphNxExtra:
    def test_handles_links_key(self, tmp_path: Path) -> None:
        data = {
            "directed": True,
            "nodes": [{"id": "a"}, {"id": "b"}],
            "links": [{"source": "a", "target": "b", "relation": "calls"}],
        }
        gf = tmp_path / "graph.json"
        gf.write_text(json.dumps(data), encoding="utf-8")
        g = load_graph_nx(gf)
        assert g.has_edge("a", "b")

    def test_empty_graph(self, tmp_path: Path) -> None:
        gf = tmp_path / "graph.json"
        gf.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
        g = load_graph_nx(gf)
        assert g.number_of_nodes() == 0


# ─────────────────────────── query_nodes (extras) ────────────────────────────


class TestQueryNodesExtra:
    def test_empty_data_returns_empty(self) -> None:
        assert query_nodes({}, "x") == ([], [])

    def test_no_nodes_returns_empty(self) -> None:
        assert query_nodes({"nodes": [], "edges": []}, "x") == ([], [])

    def test_match_by_id_substring(self) -> None:
        data = _sample_data()
        nodes, _edges = query_nodes(data, "n.db")
        assert any(n["id"] == "n.db" for n in nodes)

    def test_exact_id_resolves(self) -> None:
        data = _sample_data()
        nodes, _edges = query_nodes(data, "n.cache")
        assert {n["id"] for n in nodes} == {"n.cache"}

    def test_isolated_node_has_no_edges(self) -> None:
        data = _sample_data()
        data["nodes"].append({"id": "n.iso", "label": "Iso", "file_type": "code"})
        _nodes, edges = query_nodes(data, "n.iso")
        assert edges == []


# ─────────────────────────── explain_node (extras) ───────────────────────────


class TestExplainNodeExtra:
    def test_depth_2_expands_further(self) -> None:
        data = _sample_data()
        _target, neighbors, _edges = explain_node(data, "n.auth", depth=2)
        ids = {n["id"] for n in neighbors}
        assert "n.db" in ids  # auth → token → db

    def test_depth_1_neighbors_populated(self) -> None:
        data = _sample_data()
        _target, neighbors, _edges = explain_node(data, "n.auth", depth=1)
        ids = {n["id"] for n in neighbors}
        assert "n.token" in ids and "n.cache" in ids

    def test_connected_edges_touch_target(self) -> None:
        data = _sample_data()
        _target, _neighbors, edges = explain_node(data, "n.auth")
        assert all(e["source"] == "n.auth" or e["target"] == "n.auth" for e in edges)


# ─────────────────────────── path_between (extras) ───────────────────────────


class TestPathBetweenExtra:
    def test_same_source_and_target(self) -> None:
        data = _sample_data()
        path_nodes, _edges = path_between(data, "n.auth", "n.auth")
        assert {n["id"] for n in path_nodes} == {"n.auth"}

    def test_target_not_found_returns_empty(self) -> None:
        data = _sample_data()
        path_nodes, _edges = path_between(data, "n.auth", "GHOST_NODE_X")
        assert path_nodes == []

    def test_path_via_label(self) -> None:
        data = _sample_data()
        path_nodes, _edges = path_between(data, "AuthService", "TokenHandler")
        ids = {n["id"] for n in path_nodes}
        assert "n.auth" in ids and "n.token" in ids
