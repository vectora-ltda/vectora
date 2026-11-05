"""Testes para backend/services/context_graph/semantic_cleanup.py."""

from __future__ import annotations

import json

from backend.context_graph.semantic_cleanup import (
    MAX_SEMANTIC_FRAGMENT_BYTES,
    MAX_SEMANTIC_HYPEREDGE_NODES,
    MAX_SEMANTIC_ID_LENGTH,
    _append_rationale_attr,
    _is_sentence_like_rationale_label,
    _validate_semantic_id,
    load_validated_semantic_fragment,
    sanitize_semantic_fragment,
    validate_semantic_fragment,
)

# ─────────────────────────── validate_semantic_fragment ──────────────────────


class TestValidateSemanticFragment:
    def test_valid_minimal_fragment(self) -> None:
        fragment = {
            "nodes": [{"id": "node.A", "label": "A", "file_type": "code"}],
            "edges": [{"source": "node.A", "target": "node.A", "relation": "calls"}],
            "hyperedges": [],
        }
        assert validate_semantic_fragment(fragment) == []

    def test_not_a_dict_returns_error(self) -> None:
        errors = validate_semantic_fragment(["not", "a", "dict"])
        assert any("JSON object" in e or "must be a JSON object" in e for e in errors)

    def test_payload_too_large(self) -> None:
        big_text = "x" * (MAX_SEMANTIC_FRAGMENT_BYTES + 1)
        errors = validate_semantic_fragment({"nodes": [], "extra": big_text})
        assert any("bytes" in e for e in errors)

    def test_invalid_node_id(self) -> None:
        fragment = {
            "nodes": [{"id": "bad/id", "label": "A", "file_type": "code"}],
            "edges": [],
        }
        errors = validate_semantic_fragment(fragment)
        assert any("path separators" in e or "unsupported" in e for e in errors)

    def test_invalid_file_type(self) -> None:
        fragment = {
            "nodes": [{"id": "node.A", "label": "A", "file_type": "illegal_type"}],
            "edges": [],
        }
        errors = validate_semantic_fragment(fragment)
        assert any("file_type" in e for e in errors)

    def test_hyperedge_too_many_nodes(self) -> None:
        many_nodes = [f"n{i}" for i in range(MAX_SEMANTIC_HYPEREDGE_NODES + 1)]
        fragment = {
            "nodes": [],
            "edges": [],
            "hyperedges": [{"id": "he.1", "nodes": many_nodes}],
        }
        errors = validate_semantic_fragment(fragment)
        assert any("hyperedge" in e.lower() or "256" in e for e in errors)

    def test_valid_fragment_all_file_types(self) -> None:
        for ft in ("code", "document", "paper", "image", "rationale", "concept"):
            errors = validate_semantic_fragment(
                {"nodes": [{"id": "n.1", "label": "N", "file_type": ft}], "edges": []}
            )
            assert errors == [], f"file_type {ft!r} should be valid"


# ─────────────────────────── sanitize_semantic_fragment ──────────────────────


class TestSanitizeSemanticFragment:
    def _base(self) -> dict:
        return {
            "nodes": [
                {"id": "n.real", "label": "RealNode", "file_type": "code"},
            ],
            "edges": [],
            "hyperedges": [],
        }

    def test_removes_rationale_type_node(self) -> None:
        fragment = {
            "nodes": [
                {"id": "n.real", "label": "RealNode", "file_type": "code"},
                {
                    "id": "n.rat",
                    "label": "This is a rationale sentence that explains the design decision made here.",
                    "file_type": "rationale",
                },
            ],
            "edges": [],
            "hyperedges": [],
        }
        result = sanitize_semantic_fragment(fragment)
        ids = {n["id"] for n in result["nodes"]}
        assert "n.rat" not in ids
        assert "n.real" in ids

    def test_converts_rationale_for_edge_to_attribute(self) -> None:
        rationale_text = (
            "This node was created to handle the asynchronous dispatch of all "
            "incoming authentication tokens in the pipeline."
        )
        fragment = {
            "nodes": [
                {"id": "n.target", "label": "AuthHandler", "file_type": "code"},
                {"id": "n.rat", "label": rationale_text, "file_type": "rationale"},
            ],
            "edges": [
                {"source": "n.rat", "target": "n.target", "relation": "rationale_for"}
            ],
            "hyperedges": [],
        }
        result = sanitize_semantic_fragment(fragment)
        ids = {n["id"] for n in result["nodes"]}
        assert "n.rat" not in ids
        target = next(n for n in result["nodes"] if n["id"] == "n.target")
        assert "rationale" in target
        assert rationale_text in target["rationale"]

    def test_removes_orphan_edges(self) -> None:
        fragment = {
            "nodes": [
                {"id": "n.real", "label": "Real", "file_type": "code"},
                {
                    "id": "n.rat",
                    "label": "This is a long rationale sentence that triggers cleanup logic.",
                    "file_type": "rationale",
                },
            ],
            "edges": [
                {"source": "n.rat", "target": "n.real", "relation": "references"},
                {"source": "n.real", "target": "n.real", "relation": "calls"},
            ],
            "hyperedges": [],
        }
        result = sanitize_semantic_fragment(fragment)
        for e in result["edges"]:
            assert e.get("source") != "n.rat"
            assert e.get("target") != "n.rat"

    def test_filters_hyperedge_with_one_member(self) -> None:
        fragment = {
            "nodes": [
                {"id": "n.real", "label": "Real", "file_type": "code"},
                {
                    "id": "n.rat",
                    "label": "Another long sentence that reads like prose and should be cleaned.",
                    "file_type": "rationale",
                },
            ],
            "edges": [],
            "hyperedges": [
                {"id": "he.1", "nodes": ["n.real", "n.rat"]},
            ],
        }
        result = sanitize_semantic_fragment(fragment)
        assert result["hyperedges"] == []

    def test_keeps_hyperedge_with_two_surviving_members(self) -> None:
        fragment = {
            "nodes": [
                {"id": "n.a", "label": "A", "file_type": "code"},
                {"id": "n.b", "label": "B", "file_type": "code"},
                {
                    "id": "n.rat",
                    "label": "This rationale node should be removed from the hyperedge nodes list.",
                    "file_type": "rationale",
                },
            ],
            "edges": [],
            "hyperedges": [{"id": "he.1", "nodes": ["n.a", "n.b", "n.rat"]}],
        }
        result = sanitize_semantic_fragment(fragment)
        assert len(result["hyperedges"]) == 1
        assert set(result["hyperedges"][0]["nodes"]) == {"n.a", "n.b"}

    def test_no_nodes_removed_when_clean(self) -> None:
        fragment = {
            "nodes": [
                {"id": "n.a", "label": "Foo", "file_type": "code"},
                {"id": "n.b", "label": "Bar", "file_type": "document"},
            ],
            "edges": [{"source": "n.a", "target": "n.b", "relation": "imports"}],
            "hyperedges": [],
        }
        result = sanitize_semantic_fragment(fragment)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1


# ─────────────────────────── _is_sentence_like_rationale_label ───────────────


class TestIsSentenceLikeRationaleLabel:
    def test_short_label_is_false(self) -> None:
        assert _is_sentence_like_rationale_label("AuthService") is False

    def test_long_with_punctuation_is_true(self) -> None:
        label = (
            "This node was created to manage the asynchronous dispatch of all incoming "
            "authentication requests across the pipeline."
        )
        assert _is_sentence_like_rationale_label(label) is True

    def test_long_without_punctuation_is_false(self) -> None:
        label = "A" * 90
        assert _is_sentence_like_rationale_label(label) is False

    def test_empty_string_is_false(self) -> None:
        assert _is_sentence_like_rationale_label("") is False

    def test_many_words_with_colon_is_true(self) -> None:
        label = "Decision: use Redis for session storage because it is fast"
        assert _is_sentence_like_rationale_label(label) is True

    def test_long_chars_with_punctuation_is_true(self) -> None:
        label = "x" * 80 + "."
        assert _is_sentence_like_rationale_label(label) is True

    def test_few_words_short_is_false(self) -> None:
        assert _is_sentence_like_rationale_label("Short. Phrase.") is False


# ─────────────────────────── validate (extras) ───────────────────────────────


class TestValidateSemanticFragmentExtra:
    def test_empty_fragment_is_valid(self) -> None:
        assert validate_semantic_fragment({}) == []

    def test_nodes_not_a_list(self) -> None:
        errors = validate_semantic_fragment({"nodes": "x", "edges": []})
        assert any("nodes must be a list" in e for e in errors)

    def test_edges_not_a_list(self) -> None:
        errors = validate_semantic_fragment({"nodes": [], "edges": "x"})
        assert any("edges must be a list" in e for e in errors)

    def test_node_not_an_object(self) -> None:
        errors = validate_semantic_fragment({"nodes": [123], "edges": []})
        assert any("must be an object" in e for e in errors)

    def test_edge_missing_source_is_error(self) -> None:
        errors = validate_semantic_fragment({"nodes": [], "edges": [{"target": "a"}]})
        assert any("source" in e for e in errors)

    def test_hyperedges_not_a_list(self) -> None:
        errors = validate_semantic_fragment(
            {"nodes": [], "edges": [], "hyperedges": "x"}
        )
        assert any("hyperedges must be a list" in e for e in errors)

    def test_hyperedge_not_an_object(self) -> None:
        errors = validate_semantic_fragment(
            {"nodes": [], "edges": [], "hyperedges": [123]}
        )
        assert any("must be an object" in e for e in errors)

    def test_hyperedge_nodes_not_a_list(self) -> None:
        errors = validate_semantic_fragment(
            {"nodes": [], "edges": [], "hyperedges": [{"id": "h", "nodes": "x"}]}
        )
        assert any("nodes must be a list" in e for e in errors)

    def test_node_id_too_long(self) -> None:
        long_id = "a" * (MAX_SEMANTIC_ID_LENGTH + 1)
        errors = validate_semantic_fragment(
            {"nodes": [{"id": long_id, "file_type": "code"}], "edges": []}
        )
        assert any("chars; max" in e for e in errors)

    def test_valid_hyperedge_passes(self) -> None:
        fragment = {
            "nodes": [
                {"id": "a", "file_type": "code"},
                {"id": "b", "file_type": "code"},
            ],
            "edges": [],
            "hyperedges": [{"id": "h", "nodes": ["a", "b"]}],
        }
        assert validate_semantic_fragment(fragment) == []


# ─────────────────────────── _validate_semantic_id ───────────────────────────


class TestValidateSemanticId:
    def test_valid_id_no_errors(self) -> None:
        errors: list[str] = []
        _validate_semantic_id(errors, "f", "node.A_1:2-3")
        assert errors == []

    def test_non_string(self) -> None:
        errors: list[str] = []
        _validate_semantic_id(errors, "f", 123)
        assert any("must be a string" in e for e in errors)

    def test_empty_string(self) -> None:
        errors: list[str] = []
        _validate_semantic_id(errors, "f", "")
        assert any("must not be empty" in e for e in errors)

    def test_slash_rejected(self) -> None:
        errors: list[str] = []
        _validate_semantic_id(errors, "f", "a/b")
        assert any("path separators" in e for e in errors)

    def test_dotdot_rejected(self) -> None:
        errors: list[str] = []
        _validate_semantic_id(errors, "f", "a..b")
        assert any("path separators" in e for e in errors)

    def test_unsupported_char(self) -> None:
        errors: list[str] = []
        _validate_semantic_id(errors, "f", "a b")
        assert any("unsupported characters" in e for e in errors)


# ─────────────────────────── load_validated_semantic_fragment ────────────────


class TestLoadValidatedFragment:
    def test_valid_file(self, tmp_path) -> None:
        p = tmp_path / "frag.json"
        p.write_text(
            json.dumps({"nodes": [{"id": "a", "file_type": "code"}], "edges": []}),
            encoding="utf-8",
        )
        fragment, errors = load_validated_semantic_fragment(p)
        assert errors == []
        assert fragment is not None

    def test_invalid_json(self, tmp_path) -> None:
        p = tmp_path / "frag.json"
        p.write_text("NOTJSON{", encoding="utf-8")
        fragment, errors = load_validated_semantic_fragment(p)
        assert fragment is None
        assert any("invalid JSON" in e for e in errors)

    def test_nonexistent_path(self, tmp_path) -> None:
        fragment, errors = load_validated_semantic_fragment(tmp_path / "ghost.json")
        assert fragment is None
        assert any("could not stat" in e for e in errors)

    def test_invalid_fragment_returns_none_with_errors(self, tmp_path) -> None:
        p = tmp_path / "frag.json"
        p.write_text(
            json.dumps({"nodes": [{"id": "bad/id"}], "edges": []}), encoding="utf-8"
        )
        fragment, errors = load_validated_semantic_fragment(p)
        assert fragment is None
        assert errors


# ─────────────────────────── _append_rationale_attr ──────────────────────────


class TestAppendRationaleAttr:
    def test_sets_when_empty(self) -> None:
        node: dict = {}
        _append_rationale_attr(node, ["reason one"])
        assert node["rationale"] == "reason one"

    def test_appends_to_existing(self) -> None:
        node = {"rationale": "first"}
        _append_rationale_attr(node, ["second"])
        assert node["rationale"] == "first\n\nsecond"

    def test_joins_multiple_texts(self) -> None:
        node: dict = {}
        _append_rationale_attr(node, ["a", "b"])
        assert "a" in node["rationale"] and "b" in node["rationale"]


# ─────────────────────────── sanitize (extras) ───────────────────────────────


class TestSanitizeExtra:
    def test_empty_fragment_returns_empty_lists(self) -> None:
        result = sanitize_semantic_fragment({})
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["hyperedges"] == []

    def test_hyperedges_none_becomes_list(self) -> None:
        result = sanitize_semantic_fragment(
            {"nodes": [], "edges": [], "hyperedges": None}
        )
        assert result["hyperedges"] == []

    def test_node_without_id_is_dropped(self) -> None:
        result = sanitize_semantic_fragment(
            {"nodes": [{"label": "NoId", "file_type": "code"}], "edges": []}
        )
        assert result["nodes"] == []

    def test_concept_sentence_node_removed(self) -> None:
        fragment = {
            "nodes": [
                {"id": "n.keep", "label": "Keep", "file_type": "code"},
                {
                    "id": "n.concept",
                    "label": "This concept describes the overall flow of authentication across services.",
                    "file_type": "concept",
                },
            ],
            "edges": [],
            "hyperedges": [],
        }
        result = sanitize_semantic_fragment(fragment)
        assert {n["id"] for n in result["nodes"]} == {"n.keep"}
