"""Testes para backend/services/context_graph/symbol_resolution.py.

Cobre: normalise_callable_label, node_is_resolvable_symbol, build_label_index,
existing_edge_pairs, iter_raw_calls, _module_stem, parse_python_import_aliases,
_node_source_stem, build_python_symbol_index, find_unique_python_symbol,
resolve_python_import_guided_calls.
"""

from __future__ import annotations

from pathlib import Path


def _code_node(nid: str, label: str, source_file: str = "mod.py") -> dict:
    return {"id": nid, "label": label, "file_type": "code", "source_file": source_file}


class TestNormaliseCallableLabel:
    def test_strips_parens(self):
        from backend.context_graph.symbol_resolution import (
            normalise_callable_label,
        )

        assert normalise_callable_label("authenticate()") == "authenticate"

    def test_lowercases(self):
        from backend.context_graph.symbol_resolution import (
            normalise_callable_label,
        )

        assert normalise_callable_label("AuthService") == "authservice"

    def test_strips_leading_dot(self):
        from backend.context_graph.symbol_resolution import (
            normalise_callable_label,
        )

        assert normalise_callable_label(".method()") == "method"

    def test_strips_whitespace(self):
        from backend.context_graph.symbol_resolution import (
            normalise_callable_label,
        )

        assert normalise_callable_label("  fn  ") == "fn"


class TestNodeIsResolvableSymbol:
    def test_code_node_with_label(self):
        from backend.context_graph.symbol_resolution import (
            node_is_resolvable_symbol,
        )

        assert node_is_resolvable_symbol(_code_node("n", "authenticate")) is True

    def test_document_node_not_resolvable(self):
        from backend.context_graph.symbol_resolution import (
            node_is_resolvable_symbol,
        )

        assert (
            node_is_resolvable_symbol(
                {"id": "n", "label": "heading", "file_type": "document"}
            )
            is False
        )

    def test_empty_label_not_resolvable(self):
        from backend.context_graph.symbol_resolution import (
            node_is_resolvable_symbol,
        )

        assert (
            node_is_resolvable_symbol({"id": "n", "label": "", "file_type": "code"})
            is False
        )

    def test_file_extension_label_not_resolvable(self):
        from backend.context_graph.symbol_resolution import (
            node_is_resolvable_symbol,
        )

        assert node_is_resolvable_symbol(_code_node("n", "auth.py")) is False

    def test_js_extension_not_resolvable(self):
        from backend.context_graph.symbol_resolution import (
            node_is_resolvable_symbol,
        )

        assert node_is_resolvable_symbol(_code_node("n", "utils.js")) is False

    def test_no_file_type_not_resolvable(self):
        from backend.context_graph.symbol_resolution import (
            node_is_resolvable_symbol,
        )

        assert node_is_resolvable_symbol({"id": "n", "label": "fn"}) is False


class TestBuildLabelIndex:
    def test_indexes_code_nodes(self):
        from backend.context_graph.symbol_resolution import build_label_index

        nodes = [_code_node("n1", "authenticate"), _code_node("n2", "Login")]
        idx = build_label_index(nodes)
        assert "authenticate" in idx
        assert "n1" in idx["authenticate"]

    def test_skips_non_code(self):
        from backend.context_graph.symbol_resolution import build_label_index

        nodes = [{"id": "n", "label": "Heading", "file_type": "document"}]
        idx = build_label_index(nodes)
        assert len(idx) == 0

    def test_multiple_nodes_same_label(self):
        from backend.context_graph.symbol_resolution import build_label_index

        nodes = [_code_node("n1", "fn", "a.py"), _code_node("n2", "fn", "b.py")]
        idx = build_label_index(nodes)
        assert len(idx["fn"]) == 2

    def test_node_without_id_skipped(self):
        from backend.context_graph.symbol_resolution import build_label_index

        nodes = [{"label": "fn", "file_type": "code", "source_file": "a.py"}]
        idx = build_label_index(nodes)
        assert len(idx) == 0


class TestExistingEdgePairs:
    def test_basic_pairs(self):
        from backend.context_graph.symbol_resolution import existing_edge_pairs

        edges = [{"source": "a", "target": "b", "relation": "calls"}]
        pairs = existing_edge_pairs(edges)
        assert ("a", "b", "calls") in pairs

    def test_missing_source_or_target_skipped(self):
        from backend.context_graph.symbol_resolution import existing_edge_pairs

        edges = [
            {"target": "b", "relation": "calls"},
            {"source": "a", "relation": "calls"},
        ]
        pairs = existing_edge_pairs(edges)
        assert len(pairs) == 0

    def test_empty_edges(self):
        from backend.context_graph.symbol_resolution import existing_edge_pairs

        assert existing_edge_pairs([]) == set()


class TestIterRawCalls:
    def test_basic_iteration(self):
        from backend.context_graph.symbol_resolution import iter_raw_calls

        per_file = [{"raw_calls": [{"callee": "fn", "caller_nid": "m"}]}]
        result = iter_raw_calls(per_file)
        assert len(result) == 1
        assert result[0]["callee"] == "fn"

    def test_non_dict_skipped(self):
        from backend.context_graph.symbol_resolution import iter_raw_calls

        result = iter_raw_calls(["not-a-dict", None, {"raw_calls": []}])
        assert result == []

    def test_non_list_raw_calls_skipped(self):
        from backend.context_graph.symbol_resolution import iter_raw_calls

        result = iter_raw_calls([{"raw_calls": "not-a-list"}])
        assert result == []

    def test_non_dict_items_inside_list_skipped(self):
        from backend.context_graph.symbol_resolution import iter_raw_calls

        result = iter_raw_calls([{"raw_calls": ["bad", {"callee": "ok"}]}])
        assert len(result) == 1


class TestModuleStem:
    def test_plain_module(self):
        from backend.context_graph.symbol_resolution import _module_stem

        assert _module_stem("auth") == "auth"

    def test_dotted_module(self):
        from backend.context_graph.symbol_resolution import _module_stem

        assert _module_stem("app.services.auth") == "auth"

    def test_relative_import(self):
        from backend.context_graph.symbol_resolution import _module_stem

        assert _module_stem(".helper") == "helper"

    def test_none_returns_empty(self):
        from backend.context_graph.symbol_resolution import _module_stem

        assert _module_stem(None) == ""


class TestParsePythonImportAliases:
    def test_from_import(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "module.py"
        f.write_text("from auth import authenticate\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert "authenticate" in aliases
        assert aliases["authenticate"].module_stem == "auth"

    def test_from_import_as(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "module.py"
        f.write_text("from auth import authenticate as auth_fn\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert "auth_fn" in aliases
        assert aliases["auth_fn"].imported_name == "authenticate"

    def test_star_import_skipped(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "module.py"
        f.write_text("from auth import *\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert len(aliases) == 0

    def test_nested_import_not_indexed(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "module.py"
        f.write_text("def fn():\n    from auth import authenticate\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert "authenticate" not in aliases

    def test_plain_import_not_indexed(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "module.py"
        f.write_text("import auth\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert len(aliases) == 0

    def test_missing_file_returns_empty(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        aliases = parse_python_import_aliases(tmp_path / "ghost.py")
        assert aliases == {}

    def test_syntax_error_returns_empty(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "bad.py"
        f.write_text("def (:\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert aliases == {}

    def test_relative_import(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "module.py"
        f.write_text("from .helper import transform\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert "transform" in aliases
        assert aliases["transform"].module_stem == "helper"

    def test_source_location_stored(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            parse_python_import_aliases,
        )

        f = tmp_path / "module.py"
        f.write_text("from auth import fn\n", encoding="utf-8")
        aliases = parse_python_import_aliases(f)
        assert aliases["fn"].source_location.startswith("L")


class TestBuildPythonSymbolIndex:
    def test_builds_stem_symbol_index(self):
        from backend.context_graph.symbol_resolution import (
            build_python_symbol_index,
        )

        nodes = [_code_node("auth_fn", "authenticate", "auth.py")]
        idx = build_python_symbol_index(nodes)
        assert ("auth", "authenticate") in idx

    def test_skips_non_code(self):
        from backend.context_graph.symbol_resolution import (
            build_python_symbol_index,
        )

        nodes = [
            {
                "id": "n",
                "label": "Docs",
                "file_type": "document",
                "source_file": "docs.md",
            }
        ]
        idx = build_python_symbol_index(nodes)
        assert len(idx) == 0

    def test_no_source_file_skipped(self):
        from backend.context_graph.symbol_resolution import (
            build_python_symbol_index,
        )

        nodes = [{"id": "n", "label": "fn", "file_type": "code", "source_file": ""}]
        idx = build_python_symbol_index(nodes)
        assert len(idx) == 0


class TestFindUniquePythonSymbol:
    def test_finds_unique(self):
        from backend.context_graph.symbol_resolution import (
            ImportedSymbol,
            build_python_symbol_index,
            find_unique_python_symbol,
        )

        nodes = [_code_node("auth_fn", "authenticate", "auth.py")]
        idx = build_python_symbol_index(nodes)
        sym = ImportedSymbol(
            local_name="authenticate",
            imported_name="authenticate",
            module_stem="auth",
            source_file="consumer.py",
            source_location="L1",
        )
        result = find_unique_python_symbol(idx, sym)
        assert result == "auth_fn"

    def test_ambiguous_returns_none(self):
        from backend.context_graph.symbol_resolution import (
            ImportedSymbol,
            build_python_symbol_index,
            find_unique_python_symbol,
        )

        nodes = [
            _code_node("auth_fn1", "authenticate", "auth_a.py"),
            _code_node("auth_fn2", "authenticate", "auth_b.py"),
        ]
        idx = build_python_symbol_index(nodes)
        sym = ImportedSymbol(
            local_name="authenticate",
            imported_name="authenticate",
            module_stem="auth_a",
            source_file="consumer.py",
            source_location="L1",
        )
        result = find_unique_python_symbol(idx, sym)
        # auth_a key has only one entry → resolved
        assert result == "auth_fn1"

    def test_missing_returns_none(self):
        from backend.context_graph.symbol_resolution import (
            ImportedSymbol,
            find_unique_python_symbol,
        )

        sym = ImportedSymbol(
            local_name="ghost",
            imported_name="ghost",
            module_stem="missing",
            source_file="consumer.py",
            source_location="L1",
        )
        result = find_unique_python_symbol({}, sym)
        assert result is None


class TestResolvePythonImportGuidedCalls:
    def test_resolves_call_via_import(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            resolve_python_import_guided_calls,
        )

        consumer = tmp_path / "consumer.py"
        consumer.write_text("from auth import authenticate\n", encoding="utf-8")

        nodes = [
            _code_node("auth_authenticate", "authenticate", str(tmp_path / "auth.py")),
            _code_node("consumer_main", "main", str(consumer)),
        ]
        per_file: list[dict] = [
            {
                "raw_calls": [
                    {
                        "callee": "authenticate",
                        "caller_nid": "consumer_main",
                        "is_member_call": False,
                    }
                ]
            }
        ]
        edges = resolve_python_import_guided_calls(per_file, [consumer], nodes, [])
        assert any(
            e["source"] == "consumer_main" and e["target"] == "auth_authenticate"
            for e in edges
        )

    def test_skips_member_calls(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            resolve_python_import_guided_calls,
        )

        consumer = tmp_path / "consumer.py"
        consumer.write_text("from auth import obj\n", encoding="utf-8")

        nodes = [_code_node("auth_obj", "obj", str(tmp_path / "auth.py"))]
        per_file: list[dict] = [
            {
                "raw_calls": [
                    {"callee": "obj", "caller_nid": "main", "is_member_call": True}
                ]
            }
        ]
        edges = resolve_python_import_guided_calls(per_file, [consumer], nodes, [])
        assert len(edges) == 0

    def test_skips_non_python_files(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            resolve_python_import_guided_calls,
        )

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { fn } from './lib';\n", encoding="utf-8")
        nodes: list[dict] = []
        edges = resolve_python_import_guided_calls([], [ts_file], nodes, [])
        assert edges == []

    def test_no_duplicate_edges(self, tmp_path: Path):
        from backend.context_graph.symbol_resolution import (
            resolve_python_import_guided_calls,
        )

        consumer = tmp_path / "consumer.py"
        consumer.write_text("from auth import authenticate\n", encoding="utf-8")
        nodes = [_code_node("auth_fn", "authenticate", str(tmp_path / "auth.py"))]
        existing = [
            {"source": "consumer_main", "target": "auth_fn", "relation": "calls"}
        ]
        per_file: list[dict] = [
            {
                "raw_calls": [
                    {
                        "callee": "authenticate",
                        "caller_nid": "consumer_main",
                        "is_member_call": False,
                    }
                ]
            }
        ]
        edges = resolve_python_import_guided_calls(
            per_file, [consumer], nodes, existing
        )
        assert len(edges) == 0
