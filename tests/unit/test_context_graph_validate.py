"""Testes para backend/services/context_graph/validate.py.

Cobre: validate_extraction (todos os branches) e assert_valid.
"""

from __future__ import annotations

import pytest


class TestValidateExtraction:
    def test_non_dict_returns_error(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": None})
        assert any(e for e in errors)

    def test_missing_nodes_key(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"edges": []})
        assert any("nodes" in e for e in errors)

    def test_nodes_not_list(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": "bad", "edges": []})
        assert any("list" in e for e in errors)

    def test_missing_edges_key(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": []})
        assert any("edges" in e for e in errors)

    def test_edges_not_list(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": [], "edges": 42})
        assert any("list" in e for e in errors)

    def test_accepts_links_as_edges_fallback(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": [], "links": []})
        assert not any("edges" in e for e in errors)

    def test_node_not_dict_returns_error(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": ["not_a_dict"], "edges": []})
        assert any("object" in e for e in errors)

    def test_node_missing_required_field(self):
        from backend.context_graph.validate import validate_extraction

        # Missing 'id' among others
        errors = validate_extraction(
            {
                "nodes": [{"label": "X", "file_type": "code", "source_file": "x.py"}],
                "edges": [],
            }
        )
        assert any("missing required field" in e for e in errors)

    def test_node_invalid_file_type(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction(
            {
                "nodes": [
                    {
                        "id": "x",
                        "label": "X",
                        "file_type": "INVALID",
                        "source_file": "x.py",
                    }
                ],
                "edges": [],
            }
        )
        assert any("invalid file_type" in e for e in errors)

    def test_valid_node_all_file_types(self):
        from backend.context_graph.validate import (
            VALID_FILE_TYPES,
            validate_extraction,
        )

        for ft in VALID_FILE_TYPES:
            errors = validate_extraction(
                {
                    "nodes": [
                        {
                            "id": "n",
                            "label": "N",
                            "file_type": ft,
                            "source_file": "f.py",
                        }
                    ],
                    "edges": [],
                }
            )
            ft_errors = [e for e in errors if "file_type" in e]
            assert not ft_errors, f"file_type={ft!r} should be valid"

    def test_edge_not_dict_returns_error(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": [], "edges": [42]})
        assert any("object" in e for e in errors)

    def test_edge_missing_required_fields(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction(
            {
                "nodes": [],
                "edges": [{"source": "a", "target": "b"}],
            }
        )
        assert any("missing required field" in e for e in errors)

    def test_edge_invalid_confidence(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction(
            {
                "nodes": [
                    {
                        "id": "a",
                        "label": "A",
                        "file_type": "code",
                        "source_file": "a.py",
                    },
                    {
                        "id": "b",
                        "label": "B",
                        "file_type": "code",
                        "source_file": "b.py",
                    },
                ],
                "edges": [
                    {
                        "source": "a",
                        "target": "b",
                        "relation": "calls",
                        "confidence": "WRONG",
                        "source_file": "a.py",
                    }
                ],
            }
        )
        assert any("invalid confidence" in e for e in errors)

    def test_edge_source_not_in_nodes(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction(
            {
                "nodes": [
                    {
                        "id": "b",
                        "label": "B",
                        "file_type": "code",
                        "source_file": "b.py",
                    }
                ],
                "edges": [
                    {
                        "source": "GHOST",
                        "target": "b",
                        "relation": "calls",
                        "confidence": "EXTRACTED",
                        "source_file": "b.py",
                    }
                ],
            }
        )
        assert any("does not match any node id" in e for e in errors)

    def test_edge_target_not_in_nodes(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction(
            {
                "nodes": [
                    {
                        "id": "a",
                        "label": "A",
                        "file_type": "code",
                        "source_file": "a.py",
                    }
                ],
                "edges": [
                    {
                        "source": "a",
                        "target": "GHOST",
                        "relation": "calls",
                        "confidence": "EXTRACTED",
                        "source_file": "a.py",
                    }
                ],
            }
        )
        assert any("does not match any node id" in e for e in errors)

    def test_valid_extraction_empty(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction({"nodes": [], "edges": []})
        assert errors == []

    def test_valid_extraction_complete(self):
        from backend.context_graph.validate import validate_extraction

        errors = validate_extraction(
            {
                "nodes": [
                    {
                        "id": "a",
                        "label": "A",
                        "file_type": "code",
                        "source_file": "a.py",
                    },
                    {
                        "id": "b",
                        "label": "B",
                        "file_type": "document",
                        "source_file": "b.md",
                    },
                ],
                "edges": [
                    {
                        "source": "a",
                        "target": "b",
                        "relation": "references",
                        "confidence": "INFERRED",
                        "source_file": "a.py",
                    }
                ],
            }
        )
        assert errors == []


class TestAssertValid:
    def test_raises_on_invalid(self):
        from backend.context_graph.validate import assert_valid

        with pytest.raises(ValueError, match=r"error\(s\)"):
            assert_valid({"nodes": [], "edges": 42})

    def test_does_not_raise_on_valid(self):
        from backend.context_graph.validate import assert_valid

        assert_valid({"nodes": [], "edges": []})  # should not raise
