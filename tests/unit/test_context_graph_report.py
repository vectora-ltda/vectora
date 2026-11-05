"""Testes para backend/services/context_graph/report.py.

Cobre: generate (todos os branches), _safe_community_name.
"""

from __future__ import annotations

import networkx as nx


def _minimal_graph() -> nx.Graph:
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


class TestSafeCommunityName:
    def test_strips_invalid_chars(self):
        from backend.context_graph.report import _safe_community_name

        assert _safe_community_name("My*Community") == "MyCommunity"

    def test_strips_md_extension(self):
        from backend.context_graph.report import _safe_community_name

        assert _safe_community_name("topic.md") == "topic"

    def test_empty_label_returns_unnamed(self):
        from backend.context_graph.report import _safe_community_name

        assert _safe_community_name("") == "unnamed"
        assert _safe_community_name("***") == "unnamed"

    def test_normal_label_unchanged(self):
        from backend.context_graph.report import _safe_community_name

        assert _safe_community_name("AuthSystem") == "AuthSystem"

    def test_newlines_replaced_with_space(self):
        from backend.context_graph.report import _safe_community_name

        result = _safe_community_name("line1\nline2")
        assert "\n" not in result


class TestGenerate:
    def _generate(self, graph: nx.Graph | None = None, **kwargs) -> str:
        from backend.context_graph.report import generate

        G = graph or _minimal_graph()
        communities = kwargs.pop("communities", {0: ["a", "b"]})
        cohesion = kwargs.pop("cohesion", {0: 0.5})
        labels = kwargs.pop("labels", {0: "Auth System"})
        god_list = kwargs.pop("god_list", [{"label": "AuthService", "degree": 5}])
        surprise = kwargs.pop("surprise", [])
        detection = kwargs.pop("detection", {"total_files": 10, "total_words": 500})
        token_cost = kwargs.pop("token_cost", {"input": 100, "output": 50})
        root = kwargs.pop("root", "/project")
        questions = kwargs.pop("questions", None)

        return generate(
            G,
            communities,
            cohesion,
            labels,
            god_list,
            surprise,
            detection,
            token_cost,
            root,
            questions,
            **kwargs,
        )

    def test_basic_report_generated(self):
        text = self._generate()
        assert "Graph Report" in text
        assert "God Nodes" in text

    def test_detection_warning_shown(self):
        text = self._generate(detection={"warning": "Corpus too small"})
        assert "Corpus too small" in text

    def test_detection_stats_shown(self):
        text = self._generate(detection={"total_files": 42, "total_words": 1000})
        assert "42 files" in text

    def test_built_at_commit_shown(self):
        text = self._generate(built_at_commit="abc123def456")
        assert "abc123d" in text

    def test_community_hubs_shown_when_nonempty(self):
        text = self._generate(
            communities={0: ["a", "b"]},
            labels={0: "Auth System"},
        )
        assert "Community Hubs" in text

    def test_thin_community_omitted(self):
        G = _minimal_graph()
        G.add_node("c", label="lone", source_file="c.py", file_type="code")
        text = self._generate(
            graph=G,
            communities={0: ["a", "b"], 1: ["c"]},
            labels={0: "Main", 1: "Thin"},
            min_community_size=2,
        )
        assert "thin omitted" in text

    def test_surprise_connections_shown(self):
        surprises = [
            {
                "source": "auth",
                "target": "ui_comp",
                "relation": "calls",
                "confidence": "AMBIGUOUS",
                "confidence_score": 0.3,
                "source_files": ["auth.py", "ui.ts"],
            }
        ]
        text = self._generate(surprise=surprises)
        assert "auth" in text
        assert "ui_comp" in text

    def test_inferred_surprise_shows_score(self):
        surprises = [
            {
                "source": "a",
                "target": "b",
                "relation": "references",
                "confidence": "INFERRED",
                "confidence_score": 0.75,
                "source_files": ["a.py", "b.py"],
            }
        ]
        text = self._generate(surprise=surprises)
        assert "INFERRED" in text
        assert "0.75" in text

    def test_no_surprises_shows_message(self):
        text = self._generate(surprise=[])
        assert "None detected" in text

    def test_import_cycles_shown(self):
        G = nx.DiGraph()
        G.add_node("a", label="a", source_file="a.ts")
        G.add_node("b", label="b", source_file="b.ts")
        G.add_edge(
            "a", "b", relation="imports_from", source_file="a.ts", _src="a", _tgt="b"
        )
        G.add_edge(
            "b", "a", relation="imports_from", source_file="b.ts", _src="b", _tgt="a"
        )
        text = self._generate(graph=G)
        assert "Import Cycles" in text

    def test_no_import_cycles_shows_none(self):
        text = self._generate()
        assert "None detected." in text

    def test_ambiguous_edges_section_shown(self):
        G = nx.Graph()
        G.add_node("a", label="A", source_file="a.py", file_type="code")
        G.add_node("b", label="B", source_file="b.py", file_type="code")
        G.add_edge(
            "a", "b", relation="calls", confidence="AMBIGUOUS", source_file="a.py"
        )
        text = self._generate(graph=G, communities={0: ["a", "b"]})
        assert "Ambiguous Edges" in text

    def test_suggested_questions_no_signal(self):
        questions = [
            {"type": "no_signal", "question": None, "why": "Not enough signal"}
        ]
        text = self._generate(questions=questions)
        assert "Not enough signal" in text

    def test_suggested_questions_with_real_questions(self):
        questions = [
            {
                "type": "bridge_node",
                "question": "Why does X connect Y?",
                "why": "High betweenness",
            }
        ]
        text = self._generate(questions=questions)
        assert "Why does X connect Y?" in text

    def test_hyperedges_shown(self):
        G = _minimal_graph()
        G.graph["hyperedges"] = [
            {
                "id": "he1",
                "label": "GroupA",
                "nodes": ["a", "b"],
                "confidence": "INFERRED",
                "confidence_score": 0.8,
            }
        ]
        text = self._generate(graph=G)
        assert "Hyperedges" in text
        assert "GroupA" in text

    def test_knowledge_gaps_shown_when_isolated(self):
        G = nx.Graph()
        G.add_node("a", label="A", source_file="a.py", file_type="code")
        G.add_node("b", label="B", source_file="b.py", file_type="code")
        # b is isolated (degree 0)
        text = self._generate(graph=G, communities={0: ["a", "b"]})
        assert "Knowledge Gaps" in text

    def test_string_community_labels_normalized(self):
        G = _minimal_graph()
        text = self._generate(
            graph=G,
            communities={"0": ["a", "b"]},
            labels={"0": "Auth System"},
        )
        assert "Graph Report" in text
