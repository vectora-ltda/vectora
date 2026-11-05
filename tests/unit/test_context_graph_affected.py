"""Testes para backend/services/context_graph/affected.py.

Cobre: resolve_seed, affected_nodes, format_affected.
"""

from __future__ import annotations

import networkx as nx

from backend.context_graph.affected import (
    DEFAULT_AFFECTED_RELATIONS,
    AffectedHit,
    _bare_name,
    _format_location,
    _node_label,
    _normalize_label,
    affected_nodes,
    format_affected,
    load_graph,
    resolve_seed,
)


def _make_graph() -> nx.DiGraph:
    """Grafo dirigido simples para testes:

    A -[calls]-> B -[calls]-> C
    D -[imports]-> B
    E -[unknown_rel]-> B
    """
    g = nx.DiGraph()
    g.add_node("node.A", label="Alpha", source_file="src/a.py")
    g.add_node("node.B", label="Beta()", source_file="src/b.py")
    g.add_node("node.C", label="Gamma", source_file="src/c.py")
    g.add_node("node.D", label="Delta", source_file="src/d.py")
    g.add_node("node.E", label="Epsilon", source_file="src/e.py")

    g.add_edge("node.A", "node.B", relation="calls")
    g.add_edge("node.B", "node.C", relation="calls")
    g.add_edge("node.D", "node.B", relation="imports")
    g.add_edge("node.E", "node.B", relation="unknown_rel")

    return g


# ─────────────────────────── resolve_seed ────────────────────────────────────


class TestResolveSeed:
    def test_exact_id(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "node.A") == "node.A"

    def test_exact_label(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "Alpha") == "node.A"

    def test_bare_name_strips_parens(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "Beta") == "node.B"

    def test_exact_source_file(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "src/c.py") == "node.C"

    def test_substring_unique(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "Gamm") == "node.C"

    def test_ambiguous_returns_none(self) -> None:
        g = _make_graph()
        g.add_node("node.X", label="Alpha", source_file="src/x.py")
        assert resolve_seed(g, "Alpha") is None

    def test_not_found_returns_none(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "nonexistent_xyz") is None

    def test_case_insensitive_label(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "alpha") == "node.A"

    def test_bare_name_query_with_parens(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "Beta()") == "node.B"

    def test_substring_ambiguous_returns_none(self) -> None:
        g = _make_graph()
        g.add_node("node.A2", label="Alphabet", source_file="src/a2.py")
        # "alph" é substring de "Alpha" e "Alphabet" → ambíguo
        assert resolve_seed(g, "alph") is None

    def test_empty_query_returns_none(self) -> None:
        g = _make_graph()
        assert resolve_seed(g, "") is None

    def test_accented_label_nfc_normalized(self) -> None:
        g = nx.DiGraph()
        g.add_node("node.cafe", label="Café", source_file="cafe.py")
        assert resolve_seed(g, "café") == "node.cafe"

    def test_source_file_ambiguous_returns_none(self) -> None:
        g = _make_graph()
        g.add_node("node.dup", label="Dup", source_file="src/a.py")
        assert resolve_seed(g, "src/a.py") is None


# ─────────────────────────── helpers puros ───────────────────────────────────


class TestPureHelpers:
    def test_bare_name_strips_parens_and_casefolds(self) -> None:
        assert _bare_name("Foo()") == "foo"

    def test_bare_name_no_parens(self) -> None:
        assert _bare_name("Bar") == "bar"

    def test_normalize_label_casefold(self) -> None:
        assert _normalize_label("ALPHA") == "alpha"

    def test_normalize_label_nfc(self) -> None:
        # "e" + combining acute == precomposed "é" após NFC
        assert _normalize_label("café") == _normalize_label("café")

    def test_node_label_returns_label(self) -> None:
        g = _make_graph()
        assert _node_label(g, "node.A") == "Alpha"

    def test_node_label_falls_back_to_id(self) -> None:
        g = nx.DiGraph()
        g.add_node("node.noLabel")
        assert _node_label(g, "node.noLabel") == "node.noLabel"

    def test_format_location_with_source_location(self) -> None:
        assert (
            _format_location({"source_file": "a.py", "source_location": "L10"})
            == "a.py:L10"
        )

    def test_format_location_without_location(self) -> None:
        assert _format_location({"source_file": "a.py"}) == "a.py"

    def test_format_location_missing_source(self) -> None:
        assert _format_location({}) == "-"


# ─────────────────────────── affected_nodes ──────────────────────────────────


class TestAffectedNodes:
    def test_depth_1(self) -> None:
        g = _make_graph()
        hits = affected_nodes(g, "node.B", depth=1)
        hit_ids = {h.node_id for h in hits}
        assert "node.A" in hit_ids
        assert "node.D" in hit_ids
        assert "node.E" not in hit_ids

    def test_depth_2_propagates_transitively(self) -> None:
        g = _make_graph()
        g.add_edge("node.X", "node.A", relation="calls")
        g.add_node("node.X", label="X", source_file="src/x.py")
        hits = affected_nodes(g, "node.B", depth=2)
        hit_ids = {h.node_id for h in hits}
        assert "node.A" in hit_ids
        assert "node.X" in hit_ids

    def test_unlisted_relation_not_propagated(self) -> None:
        g = _make_graph()
        hits = affected_nodes(g, "node.B", depth=1)
        hit_ids = {h.node_id for h in hits}
        assert "node.E" not in hit_ids

    def test_empty_graph_returns_empty(self) -> None:
        g = nx.DiGraph()
        g.add_node("node.A", label="A")
        hits = affected_nodes(g, "node.A", depth=2)
        assert hits == []

    def test_returns_affected_hit_dataclass(self) -> None:
        g = _make_graph()
        hits = affected_nodes(g, "node.B", depth=1)
        assert all(isinstance(h, AffectedHit) for h in hits)
        alpha_hit = next(h for h in hits if h.node_id == "node.A")
        assert alpha_hit.depth == 1
        assert alpha_hit.via_relation == "calls"

    def test_seed_not_in_hits(self) -> None:
        g = _make_graph()
        hits = affected_nodes(g, "node.B", depth=2)
        assert all(h.node_id != "node.B" for h in hits)

    def test_default_relations_tuple(self) -> None:
        assert "calls" in DEFAULT_AFFECTED_RELATIONS
        assert "imports" in DEFAULT_AFFECTED_RELATIONS
        assert "inherits" in DEFAULT_AFFECTED_RELATIONS


# ─────────────────────────── format_affected ─────────────────────────────────


class TestFormatAffected:
    def test_valid_seed_returns_formatted_text(self) -> None:
        g = _make_graph()
        text = format_affected(g, "Beta")
        assert "Beta" in text
        assert "Alpha" in text or "node.A" in text
        assert "calls" in text

    def test_invalid_seed_returns_no_match_message(self) -> None:
        g = _make_graph()
        text = format_affected(g, "this_does_not_exist_xyz")
        assert (
            "No unique node match" in text
            or "não encontrado" in text.lower()
            or "nonexistent" in text.lower()
            or "no unique" in text.lower()
        )

    def test_no_affected_nodes_says_none_found(self) -> None:
        g = nx.DiGraph()
        g.add_node("node.Lone", label="Lone", source_file="lone.py")
        text = format_affected(g, "node.Lone")
        assert "No affected nodes" in text

    def test_custom_relations_only_imports(self) -> None:
        g = _make_graph()
        text = format_affected(g, "node.B", relations=("imports",))
        assert "Delta" in text or "node.D" in text
        assert "Alpha" not in text  # calls não está nas relações

    def test_custom_depth_appears_in_header(self) -> None:
        g = _make_graph()
        text = format_affected(g, "node.B", depth=3)
        assert "Depth: 3" in text

    def test_lists_one_line_per_hit(self) -> None:
        g = _make_graph()
        text = format_affected(g, "node.B", depth=1)
        bullet_lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
        assert len(bullet_lines) == 2  # node.A (calls) e node.D (imports)


# ─────────────────────────── affected_nodes (extras) ─────────────────────────


class TestAffectedNodesExtra:
    def test_depth_0_returns_empty(self) -> None:
        g = _make_graph()
        assert affected_nodes(g, "node.B", depth=0) == []

    def test_undirected_graph_uses_target_match(self) -> None:
        # nx.Graph.edges() rende a aresta a partir do nó inserido primeiro (B),
        # como (B, X); com seed=X, target==X casa e a origem B é afetada.
        g = nx.Graph()
        g.add_node("B", label="B")
        g.add_node("X", label="X")
        g.add_edge("B", "X", relation="calls")
        hits = affected_nodes(g, "X", depth=1)
        assert any(h.node_id == "B" for h in hits)

    def test_undirected_graph_returns_list(self) -> None:
        g = nx.Graph()
        g.add_node("A", label="A")
        g.add_node("B", label="B")
        g.add_edge("A", "B", relation="calls")
        assert isinstance(affected_nodes(g, "A", depth=2), list)

    def test_custom_relations_filters(self) -> None:
        g = _make_graph()
        hits = affected_nodes(g, "node.B", relations=("imports",), depth=1)
        ids = {h.node_id for h in hits}
        assert ids == {"node.D"}

    def test_via_relation_recorded(self) -> None:
        g = _make_graph()
        hits = affected_nodes(g, "node.B", depth=1)
        d_hit = next(h for h in hits if h.node_id == "node.D")
        assert d_hit.via_relation == "imports"

    def test_no_duplicate_hits(self) -> None:
        g = _make_graph()
        g.add_edge("node.D", "node.C", relation="calls")
        hits = affected_nodes(g, "node.B", depth=3)
        ids = [h.node_id for h in hits]
        assert len(ids) == len(set(ids))


# ─────────────────────────── load_graph ──────────────────────────────────────


class TestLoadGraph:
    def test_roundtrip_nodes_and_edges(self, tmp_path) -> None:
        import json

        data = {
            "directed": True,
            "nodes": [{"id": "n1", "label": "N1"}, {"id": "n2", "label": "N2"}],
            "edges": [{"source": "n1", "target": "n2", "relation": "calls"}],
        }
        p = tmp_path / "graph.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        g = load_graph(p)
        assert "n1" in g.nodes and "n2" in g.nodes
        assert g.has_edge("n1", "n2")

    def test_returns_directed_graph(self, tmp_path) -> None:
        import json

        p = tmp_path / "g.json"
        p.write_text(
            json.dumps({"nodes": [{"id": "a"}], "edges": []}), encoding="utf-8"
        )
        g = load_graph(p)
        assert g.is_directed()
