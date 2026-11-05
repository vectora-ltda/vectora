"""Testes para backend/services/context_graph/analyze.py.

Cobre: _is_file_node, _is_concept_node, _is_json_key_node, god_nodes,
surprising_connections, _cross_file_surprises, _cross_community_surprises,
suggest_questions, graph_diff, find_import_cycles.
"""

from __future__ import annotations

import networkx as nx


def _make_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("auth", label="AuthService", source_file="auth.py", file_type="code")
    G.add_node("login", label="login", source_file="auth.py", file_type="code")
    G.add_node("token", label="Token", source_file="token.py", file_type="code")
    G.add_edge(
        "auth",
        "login",
        relation="calls",
        confidence="EXTRACTED",
        source_file="auth.py",
        _src="auth",
        _tgt="login",
    )
    G.add_edge(
        "login",
        "token",
        relation="calls",
        confidence="INFERRED",
        source_file="auth.py",
        _src="login",
        _tgt="token",
    )
    return G


def _make_cross_file_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("auth", label="AuthService", source_file="auth.py", file_type="code")
    G.add_node("ui_login", label="Login", source_file="ui/login.ts", file_type="code")
    G.add_node(
        "docs_api", label="API Docs", source_file="docs/api.md", file_type="document"
    )
    G.add_edge(
        "auth",
        "ui_login",
        relation="calls",
        confidence="AMBIGUOUS",
        source_file="auth.py",
        _src="auth",
        _tgt="ui_login",
    )
    G.add_edge(
        "auth",
        "docs_api",
        relation="references",
        confidence="EXTRACTED",
        source_file="auth.py",
        _src="auth",
        _tgt="docs_api",
    )
    return G


class TestIsFileNode:
    def test_method_stub_is_file_node(self):
        from backend.context_graph.analyze import _is_file_node

        G = nx.Graph()
        G.add_node("n", label=".method_name()", source_file="a.py")
        assert _is_file_node(G, "n") is True

    def test_label_matches_filename_is_file_node(self):
        from backend.context_graph.analyze import _is_file_node

        G = nx.Graph()
        G.add_node("n", label="auth.py", source_file="src/auth.py")
        assert _is_file_node(G, "n") is True

    def test_regular_node_is_not_file_node(self):
        from backend.context_graph.analyze import _is_file_node

        G = nx.Graph()
        G.add_node("n", label="AuthService", source_file="auth.py")
        assert _is_file_node(G, "n") is False

    def test_no_label_is_not_file_node(self):
        from backend.context_graph.analyze import _is_file_node

        G = nx.Graph()
        G.add_node("n", label="", source_file="auth.py")
        assert _is_file_node(G, "n") is False

    def test_lone_function_stub_low_degree(self):
        from backend.context_graph.analyze import _is_file_node

        G = nx.Graph()
        G.add_node("n", label="do_something()", source_file="a.py")
        assert _is_file_node(G, "n") is True


class TestIsConceptNode:
    def test_empty_source_is_concept(self):
        from backend.context_graph.analyze import _is_concept_node

        G = nx.Graph()
        G.add_node("n", label="Concept", source_file="")
        assert _is_concept_node(G, "n") is True

    def test_no_extension_is_concept(self):
        from backend.context_graph.analyze import _is_concept_node

        G = nx.Graph()
        G.add_node("n", label="Concept", source_file="no_extension")
        assert _is_concept_node(G, "n") is True

    def test_real_file_is_not_concept(self):
        from backend.context_graph.analyze import _is_concept_node

        G = nx.Graph()
        G.add_node("n", label="RealNode", source_file="src/auth.py")
        assert _is_concept_node(G, "n") is False


class TestIsJsonKeyNode:
    def test_json_noise_label_is_json_key_node(self):
        from backend.context_graph.analyze import _is_json_key_node

        G = nx.Graph()
        G.add_node("n", label="name", source_file="config.json")
        assert _is_json_key_node(G, "n") is True

    def test_non_json_file_not_json_key_node(self):
        from backend.context_graph.analyze import _is_json_key_node

        G = nx.Graph()
        G.add_node("n", label="name", source_file="auth.py")
        assert _is_json_key_node(G, "n") is False

    def test_non_noise_label_not_json_key_node(self):
        from backend.context_graph.analyze import _is_json_key_node

        G = nx.Graph()
        G.add_node("n", label="AuthService", source_file="config.json")
        assert _is_json_key_node(G, "n") is False


class TestGodNodes:
    def test_returns_most_connected(self):
        from backend.context_graph.analyze import god_nodes

        G = _make_graph()
        result = god_nodes(G)
        assert len(result) >= 1
        assert all("id" in n and "label" in n and "degree" in n for n in result)

    def test_excludes_builtins(self):
        from backend.context_graph.analyze import god_nodes

        G = nx.Graph()
        G.add_node("str_node", label="str", source_file="a.py", file_type="code")
        for i in range(10):
            G.add_node(f"n{i}", label=f"N{i}", source_file="a.py", file_type="code")
            G.add_edge("str_node", f"n{i}", relation="calls", source_file="a.py")
        result = god_nodes(G)
        ids = [n["id"] for n in result]
        assert "str_node" not in ids

    def test_empty_graph_returns_empty(self):
        from backend.context_graph.analyze import god_nodes

        assert god_nodes(nx.Graph()) == []


class TestSurprisingConnections:
    def test_single_source_uses_cross_community(self):
        from backend.context_graph.analyze import surprising_connections

        G = _make_graph()
        communities = {0: ["auth", "login"], 1: ["token"]}
        result = surprising_connections(G, communities=communities)
        assert isinstance(result, list)

    def test_multi_source_uses_cross_file(self):
        from backend.context_graph.analyze import surprising_connections

        G = _make_cross_file_graph()
        result = surprising_connections(G)
        assert isinstance(result, list)

    def test_empty_graph_returns_empty(self):
        from backend.context_graph.analyze import surprising_connections

        assert surprising_connections(nx.Graph()) == []


class TestSuggestQuestions:
    def test_returns_list_of_questions(self):
        from backend.context_graph.analyze import suggest_questions

        G = _make_graph()
        communities = {0: ["auth", "login"], 1: ["token"]}
        result = suggest_questions(G, communities, {0: "Auth", 1: "Token"})
        assert isinstance(result, list)
        for q in result:
            assert "type" in q

    def test_returns_no_signal_when_empty_graph(self):
        from backend.context_graph.analyze import suggest_questions

        G = nx.Graph()
        result = suggest_questions(G, {}, {})
        assert result[0]["type"] == "no_signal"

    def test_ambiguous_edges_generate_questions(self):
        from backend.context_graph.analyze import suggest_questions

        G = nx.Graph()
        G.add_node("a", label="A", source_file="a.py")
        G.add_node("b", label="B", source_file="b.py")
        G.add_edge(
            "a", "b", relation="related_to", confidence="AMBIGUOUS", source_file="a.py"
        )
        result = suggest_questions(G, {}, {})
        types = [q["type"] for q in result]
        assert "ambiguous_edge" in types


class TestGraphDiff:
    def test_detects_new_nodes(self):
        from backend.context_graph.analyze import graph_diff

        G_old = nx.Graph()
        G_old.add_node("a", label="A")
        G_new = nx.Graph()
        G_new.add_node("a", label="A")
        G_new.add_node("b", label="B")
        diff = graph_diff(G_old, G_new)
        assert len(diff["new_nodes"]) == 1
        assert diff["new_nodes"][0]["id"] == "b"

    def test_detects_removed_nodes(self):
        from backend.context_graph.analyze import graph_diff

        G_old = nx.Graph()
        G_old.add_node("a", label="A")
        G_old.add_node("b", label="B")
        G_new = nx.Graph()
        G_new.add_node("a", label="A")
        diff = graph_diff(G_old, G_new)
        assert len(diff["removed_nodes"]) == 1

    def test_no_changes_summary(self):
        from backend.context_graph.analyze import graph_diff

        G = nx.Graph()
        G.add_node("a", label="A")
        diff = graph_diff(G, G)
        assert diff["summary"] == "no changes"

    def test_detects_new_edges(self):
        from backend.context_graph.analyze import graph_diff

        G_old = nx.Graph()
        G_old.add_node("a")
        G_old.add_node("b")
        G_new = nx.Graph()
        G_new.add_node("a")
        G_new.add_node("b")
        G_new.add_edge("a", "b", relation="calls")
        diff = graph_diff(G_old, G_new)
        assert len(diff["new_edges"]) == 1


class TestFindImportCycles:
    def test_detects_cycle(self):
        from backend.context_graph.analyze import find_import_cycles

        G = nx.DiGraph()
        G.add_node("a", label="a", source_file="a.ts")
        G.add_node("b", label="b", source_file="b.ts")
        G.add_edge(
            "a", "b", relation="imports_from", source_file="a.ts", _src="a", _tgt="b"
        )
        G.add_edge(
            "b", "a", relation="imports_from", source_file="b.ts", _src="b", _tgt="a"
        )
        cycles = find_import_cycles(G)
        assert len(cycles) >= 1
        assert "a.ts" in cycles[0]["cycle"] or "b.ts" in cycles[0]["cycle"]

    def test_no_cycle_returns_empty(self):
        from backend.context_graph.analyze import find_import_cycles

        G = nx.DiGraph()
        G.add_node("a", label="a", source_file="a.ts")
        G.add_node("b", label="b", source_file="b.ts")
        G.add_edge(
            "a", "b", relation="imports_from", source_file="a.ts", _src="a", _tgt="b"
        )
        assert find_import_cycles(G) == []

    def test_empty_graph_returns_empty(self):
        from backend.context_graph.analyze import find_import_cycles

        assert find_import_cycles(nx.DiGraph()) == []

    def test_non_import_edges_skipped(self):
        from backend.context_graph.analyze import find_import_cycles

        G = nx.DiGraph()
        G.add_node("a", label="a", source_file="a.py")
        G.add_node("b", label="b", source_file="b.py")
        G.add_edge("a", "b", relation="calls", source_file="a.py", _src="a", _tgt="b")
        G.add_edge("b", "a", relation="calls", source_file="b.py", _src="b", _tgt="a")
        assert find_import_cycles(G) == []
