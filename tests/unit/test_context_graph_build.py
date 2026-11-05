"""Testes para backend/services/context_graph/build.py.

Cobre: build_from_json, build, edge_data, edge_datas, dedupe_nodes, dedupe_edges,
deduplicate_by_label, _norm_source_file, build_merge, prefix_graph_for_global,
prune_repo_from_graph.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _extraction(
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    hyperedges: list[dict] | None = None,
) -> dict:
    return {
        "nodes": nodes or [],
        "edges": edges or [],
        "hyperedges": hyperedges or [],
    }


def _node(nid: str, label: str = "", source_file: str = "a.py", **kw: object) -> dict:
    return {
        "id": nid,
        "label": label or nid,
        "file_type": "code",
        "source_file": source_file,
        **kw,
    }


def _edge(
    src: str,
    tgt: str,
    rel: str = "calls",
    conf: str = "EXTRACTED",
    source_file: str = "a.py",
) -> dict:
    return {
        "source": src,
        "target": tgt,
        "relation": rel,
        "confidence": conf,
        "source_file": source_file,
    }


class TestBuildFromJson:
    def test_empty_extraction_returns_empty_graph(self):
        from backend.context_graph.build import build_from_json

        G = build_from_json(_extraction())
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0

    def test_nodes_added(self):
        from backend.context_graph.build import build_from_json

        G = build_from_json(_extraction(nodes=[_node("a"), _node("b")]))
        assert "a" in G.nodes
        assert "b" in G.nodes

    def test_edge_added(self):
        from backend.context_graph.build import build_from_json

        G = build_from_json(
            _extraction(
                nodes=[_node("a"), _node("b")],
                edges=[_edge("a", "b")],
            )
        )
        assert G.has_edge("a", "b")

    def test_directed_graph(self):
        import networkx as nx

        from backend.context_graph.build import build_from_json

        G = build_from_json(_extraction(nodes=[_node("a"), _node("b")]), directed=True)
        assert isinstance(G, nx.DiGraph)

    def test_links_key_remapped(self):
        from backend.context_graph.build import build_from_json

        data = {
            "nodes": [_node("a"), _node("b")],
            "links": [_edge("a", "b")],
        }
        G = build_from_json(data)
        assert G.has_edge("a", "b")

    def test_unknown_edges_skipped(self):
        from backend.context_graph.build import build_from_json

        G = build_from_json(
            _extraction(
                nodes=[_node("a")],
                edges=[_edge("a", "GHOST_NOT_IN_NODES")],
            )
        )
        assert G.number_of_edges() == 0

    def test_hyperedges_stored_in_graph(self):
        from backend.context_graph.build import build_from_json

        G = build_from_json(
            _extraction(
                nodes=[_node("a"), _node("b")],
                hyperedges=[{"id": "h1", "nodes": ["a", "b"], "label": "HE"}],
            )
        )
        assert "hyperedges" in G.graph

    def test_source_field_remapped_to_source_file(self, capsys: pytest.CaptureFixture):
        from backend.context_graph.build import build_from_json

        data = {
            "nodes": [{"id": "x", "label": "X", "file_type": "code", "source": "x.py"}],
            "edges": [],
        }
        build_from_json(data)
        stderr = capsys.readouterr().err
        assert "source_file" in stderr

    def test_invalid_file_type_remapped(self):
        from backend.context_graph.build import build_from_json

        data = {
            "nodes": [
                {
                    "id": "x",
                    "label": "X",
                    "file_type": "markdown",
                    "source_file": "x.md",
                }
            ],
            "edges": [],
        }
        G = build_from_json(data)
        assert G.nodes["x"]["file_type"] == "document"

    def test_none_file_type_defaults_to_concept(self):
        from backend.context_graph.build import build_from_json

        data = {
            "nodes": [
                {"id": "x", "label": "X", "file_type": None, "source_file": "x.md"}
            ],
            "edges": [],
        }
        G = build_from_json(data)
        assert G.nodes["x"]["file_type"] == "concept"

    def test_cross_language_inferred_calls_dropped(self):
        from backend.context_graph.build import build_from_json

        G = build_from_json(
            _extraction(
                nodes=[
                    _node("py_fn", source_file="app.py"),
                    _node("ts_fn", source_file="app.ts"),
                ],
                edges=[
                    _edge(
                        "py_fn",
                        "ts_fn",
                        rel="calls",
                        conf="INFERRED",
                        source_file="app.py",
                    )
                ],
            )
        )
        assert not G.has_edge("py_fn", "ts_fn")

    def test_preserves_direction_in_attrs(self):
        from backend.context_graph.build import build_from_json

        G = build_from_json(
            _extraction(
                nodes=[_node("a"), _node("b")],
                edges=[_edge("a", "b")],
            )
        )
        data = G["a"]["b"]
        assert data["_src"] == "a"
        assert data["_tgt"] == "b"


class TestEdgeData:
    def test_returns_dict(self):
        from backend.context_graph.build import build_from_json, edge_data

        G = build_from_json(
            _extraction(
                nodes=[_node("a"), _node("b")],
                edges=[_edge("a", "b", rel="calls")],
            )
        )
        d = edge_data(G, "a", "b")
        assert d["relation"] == "calls"


class TestEdgeDatas:
    def test_returns_list(self):
        from backend.context_graph.build import build_from_json, edge_datas

        G = build_from_json(
            _extraction(
                nodes=[_node("a"), _node("b")],
                edges=[_edge("a", "b")],
            )
        )
        result = edge_datas(G, "a", "b")
        assert isinstance(result, list)
        assert len(result) == 1


class TestDedupeNodes:
    def test_deduplicates_by_id(self):
        from backend.context_graph.build import dedupe_nodes

        nodes = [
            {"id": "x", "label": "First"},
            {"id": "x", "label": "Second"},
            {"id": "y", "label": "Other"},
        ]
        result = dedupe_nodes(nodes)
        ids = [n["id"] for n in result]
        assert ids.count("x") == 1
        assert ids.count("y") == 1

    def test_last_writer_wins(self):
        from backend.context_graph.build import dedupe_nodes

        result = dedupe_nodes(
            [
                {"id": "x", "label": "First"},
                {"id": "x", "label": "Second"},
            ]
        )
        assert result[0]["label"] == "Second"

    def test_nodes_without_id_dropped(self):
        from backend.context_graph.build import dedupe_nodes

        result = dedupe_nodes([{"label": "NoId"}, {"id": "ok", "label": "OK"}])
        assert len(result) == 1


class TestDedupeEdges:
    def test_deduplicates_exact_parallel(self):
        from backend.context_graph.build import dedupe_edges

        edges = [
            {"source": "a", "target": "b", "relation": "calls"},
            {"source": "a", "target": "b", "relation": "calls"},
            {"source": "a", "target": "b", "relation": "imports"},
        ]
        result = dedupe_edges(edges)
        assert len(result) == 2

    def test_empty_list(self):
        from backend.context_graph.build import dedupe_edges

        assert dedupe_edges([]) == []


class TestDeduplicateByLabel:
    def test_merges_nodes_with_same_label(self):
        from backend.context_graph.build import deduplicate_by_label

        nodes = [
            {
                "id": "auth_service",
                "label": "AuthService",
                "file_type": "code",
                "source_file": "a.py",
            },
            {
                "id": "auth_service_c1",
                "label": "AuthService",
                "file_type": "code",
                "source_file": "b.py",
            },
        ]
        edges = [
            {"source": "auth_service", "target": "auth_service_c1", "relation": "calls"}
        ]
        new_nodes, _new_edges = deduplicate_by_label(nodes, edges)
        ids = {n["id"] for n in new_nodes}
        assert "auth_service_c1" not in ids

    def test_no_duplicates_passthrough(self):
        from backend.context_graph.build import deduplicate_by_label

        nodes = [
            {"id": "a", "label": "Alpha", "file_type": "code", "source_file": "a.py"},
            {"id": "b", "label": "Beta", "file_type": "code", "source_file": "b.py"},
        ]
        edges = [{"source": "a", "target": "b", "relation": "calls"}]
        new_nodes, new_edges_list = deduplicate_by_label(nodes, edges)
        assert len(new_nodes) == 2
        assert len(new_edges_list) == 1


class TestBuild:
    def test_merges_multiple_extractions(self):
        from backend.context_graph.build import build

        ext1 = _extraction(nodes=[_node("a")], edges=[])
        ext2 = _extraction(nodes=[_node("b")], edges=[_edge("a", "b")])
        G = build([ext1, ext2])
        assert "a" in G.nodes
        assert "b" in G.nodes

    def test_empty_extractions(self):
        from backend.context_graph.build import build

        G = build([])
        assert G.number_of_nodes() == 0


class TestBuildMerge:
    def test_creates_new_graph_when_no_existing(self, tmp_path: Path):
        from backend.context_graph.build import build_merge

        graph_path = tmp_path / "graph.json"
        new_chunk = _extraction(
            nodes=[_node("a"), _node("b")],
            edges=[_edge("a", "b")],
        )
        G = build_merge([new_chunk], graph_path=graph_path)
        assert G.number_of_nodes() == 2

    def test_merges_with_existing(self, tmp_path: Path):
        from backend.context_graph.build import build_merge

        graph_path = tmp_path / "graph.json"
        # Use different source_files so build_merge doesn't drop the existing node
        existing = {
            "nodes": [_node("existing_node", source_file="existing.py")],
            "edges": [],
        }
        graph_path.write_text(json.dumps(existing), encoding="utf-8")

        new_chunk = _extraction(
            nodes=[_node("new_node", source_file="new.py")], edges=[]
        )
        G = build_merge([new_chunk], graph_path=graph_path)
        assert "existing_node" in G.nodes
        assert "new_node" in G.nodes

    def test_prune_deleted_sources(self, tmp_path: Path):
        from backend.context_graph.build import build_merge

        graph_path = tmp_path / "graph.json"
        # Build initial graph
        G = build_merge(
            [_extraction(nodes=[_node("to_delete", source_file="old.py")])],
            graph_path=graph_path,
        )
        from backend.context_graph.export import to_json

        to_json(G, {}, str(graph_path))

        G2 = build_merge(
            [_extraction(nodes=[_node("new_node")], edges=[])],
            graph_path=graph_path,
            prune_sources=["old.py"],
        )
        assert "to_delete" not in G2.nodes


class TestPrefixGraphForGlobal:
    def test_prefixes_node_ids(self):
        from backend.context_graph.build import (
            build_from_json,
            prefix_graph_for_global,
        )

        G = build_from_json(_extraction(nodes=[_node("fn1"), _node("fn2")]))
        H = prefix_graph_for_global(G, "repo1")
        assert "repo1::fn1" in H.nodes
        assert "repo1::fn2" in H.nodes

    def test_adds_repo_attr(self):
        from backend.context_graph.build import (
            build_from_json,
            prefix_graph_for_global,
        )

        G = build_from_json(_extraction(nodes=[_node("fn1")]))
        H = prefix_graph_for_global(G, "myrepo")
        assert H.nodes["myrepo::fn1"]["repo"] == "myrepo"


class TestPruneRepoFromGraph:
    def test_removes_nodes_with_repo_tag(self):
        from backend.context_graph.build import (
            build_from_json,
            prune_repo_from_graph,
        )

        G = build_from_json(_extraction(nodes=[_node("fn1"), _node("fn2")]))
        G.nodes["fn1"]["repo"] = "repoA"
        G.nodes["fn2"]["repo"] = "repoB"
        count = prune_repo_from_graph(G, "repoA")
        assert count == 1
        assert "fn1" not in G.nodes
        assert "fn2" in G.nodes
