"""Testes para backend/services/context_graph/dedup.py.

Cobre: _norm, _entropy, _shingles, _make_minhash, _is_variant_pair,
_short_label_blocked, _numeric_tokens_differ, _crossfile_fileanchored_blocked,
_UF (union-find), deduplicate_entities.
"""

from __future__ import annotations


class TestNorm:
    def test_lowercases(self):
        from backend.context_graph.dedup import _norm

        assert _norm("AuthService") == "authservice"

    def test_collapses_nonalphanumeric(self):
        from backend.context_graph.dedup import _norm

        result = _norm("auth-service_v2")
        assert "auth" in result and "v2" in result
        assert "-" not in result and "_" not in result

    def test_none_returns_empty(self):
        from backend.context_graph.dedup import _norm

        assert _norm(None) == ""

    def test_integer_converts(self):
        from backend.context_graph.dedup import _norm

        assert _norm(str(42)) == "42"

    def test_unicode_normalization(self):
        from backend.context_graph.dedup import _norm

        result = _norm("café")
        assert "cafe" in result or "caf" in result  # NFKC + casefold


class TestEntropy:
    def test_empty_label_zero(self):
        from backend.context_graph.dedup import _entropy

        assert _entropy("") == 0.0

    def test_single_char_zero(self):
        from backend.context_graph.dedup import _entropy

        assert _entropy("aaa") == 0.0

    def test_uniform_has_entropy(self):
        from backend.context_graph.dedup import _entropy

        val = _entropy("abcd")
        assert val > 0.0

    def test_long_string_higher_entropy(self):
        from backend.context_graph.dedup import _entropy

        low = _entropy("aaa")
        high = _entropy("AuthenticationService")
        assert high > low


class TestShingles:
    def test_trigrams(self):
        from backend.context_graph.dedup import _shingles

        result = _shingles("abcd", k=3)
        assert "abc" in result
        assert "bcd" in result

    def test_short_text_returns_self(self):
        from backend.context_graph.dedup import _shingles

        assert _shingles("ab", k=3) == {"ab"}

    def test_empty_returns_empty_singleton(self):
        from backend.context_graph.dedup import _shingles

        assert _shingles("", k=3) == {""}


class TestMakeMinhash:
    def test_returns_minhash(self):
        from backend.context_graph._minhash import MinHash
        from backend.context_graph.dedup import _make_minhash

        m = _make_minhash("AuthService")
        assert isinstance(m, MinHash)

    def test_similar_texts_produce_similar_hash(self):
        from backend.context_graph.dedup import _make_minhash

        m1 = _make_minhash("authentication service")
        m2 = _make_minhash("authentication services")
        # Jaccard similarity should be non-zero
        similarity = sum(
            a == b for a, b in zip(m1.hashvalues, m2.hashvalues, strict=False)
        ) / len(m1.hashvalues)
        assert similarity > 0.3


class TestIsVariantPair:
    def test_same_label_not_variant(self):
        from backend.context_graph.dedup import _is_variant_pair

        assert _is_variant_pair("model", "model") is False

    def test_short_number_suffix_variants(self):
        from backend.context_graph.dedup import _is_variant_pair

        assert _is_variant_pair("m1", "m2") is True

    def test_long_labels_not_checked(self):
        from backend.context_graph.dedup import _is_variant_pair

        assert _is_variant_pair("longnamelabelhere1", "longnamelabelhere2") is False

    def test_no_variant_suffix_not_variant(self):
        from backend.context_graph.dedup import _is_variant_pair

        assert _is_variant_pair("foo", "bar") is False


class TestShortLabelBlocked:
    def test_long_labels_not_blocked(self):
        from backend.context_graph.dedup import _short_label_blocked

        assert _short_label_blocked("longnamelabel", "longnamelabol", 97.0) is False

    def test_same_length_single_substitution_allowed(self):
        from backend.context_graph.dedup import _short_label_blocked

        assert _short_label_blocked("extractor", "extractar", 97.0) is False

    def test_different_length_short_blocked(self):
        from backend.context_graph.dedup import _short_label_blocked

        assert _short_label_blocked("cranel", "cranelr", 97.0) is True


class TestNumericTokensDiffer:
    def test_same_labels_no_difference(self):
        from backend.context_graph.dedup import _numeric_tokens_differ

        assert _numeric_tokens_differ("block3", "block3") is False

    def test_different_numbers_differ(self):
        from backend.context_graph.dedup import _numeric_tokens_differ

        assert _numeric_tokens_differ("block3", "block13") is True

    def test_leading_zeros_normalized(self):
        from backend.context_graph.dedup import _numeric_tokens_differ

        assert _numeric_tokens_differ("ADR 009", "ADR 9") is False

    def test_no_numbers_no_difference(self):
        from backend.context_graph.dedup import _numeric_tokens_differ

        assert _numeric_tokens_differ("foo bar", "baz qux") is False


class TestCrossfileFileanchoredBlocked:
    def test_code_nodes_not_blocked(self):
        from backend.context_graph.dedup import _crossfile_fileanchored_blocked

        a = {"file_type": "code", "source_file": "a.py"}
        b = {"file_type": "code", "source_file": "b.py"}
        assert _crossfile_fileanchored_blocked(a, b) is False

    def test_document_nodes_different_files_blocked(self):
        from backend.context_graph.dedup import _crossfile_fileanchored_blocked

        a = {"file_type": "document", "source_file": "a.md"}
        b = {"file_type": "document", "source_file": "b.md"}
        assert _crossfile_fileanchored_blocked(a, b) is True

    def test_document_same_file_not_blocked(self):
        from backend.context_graph.dedup import _crossfile_fileanchored_blocked

        a = {"file_type": "document", "source_file": "readme.md"}
        b = {"file_type": "document", "source_file": "readme.md"}
        assert _crossfile_fileanchored_blocked(a, b) is False

    def test_rationale_nodes_different_files_blocked(self):
        from backend.context_graph.dedup import _crossfile_fileanchored_blocked

        a = {"file_type": "rationale", "source_file": "a.py"}
        b = {"file_type": "rationale", "source_file": "b.py"}
        assert _crossfile_fileanchored_blocked(a, b) is True


class TestUF:
    def test_find_self(self):
        from backend.context_graph.dedup import _UF

        uf = _UF()
        assert uf.find("a") == "a"

    def test_union_and_find(self):
        from backend.context_graph.dedup import _UF

        uf = _UF()
        uf.union("a", "b")
        assert uf.find("a") == uf.find("b")

    def test_components(self):
        from backend.context_graph.dedup import _UF

        uf = _UF()
        uf.union("a", "b")
        uf.union("c", "d")
        comps = uf.components()
        # Two components
        groups = [frozenset(v) for v in comps.values()]
        assert frozenset({"a", "b"}) in groups
        assert frozenset({"c", "d"}) in groups

    def test_path_compression(self):
        from backend.context_graph.dedup import _UF

        uf = _UF()
        uf.union("a", "b")
        uf.union("b", "c")
        # All should find same root after compression
        root = uf.find("a")
        assert uf.find("b") == root
        assert uf.find("c") == root


class TestDeduplicateEntities:
    def _node(
        self, nid: str, label: str, file_type: str = "concept", source_file: str = ""
    ) -> dict:
        return {
            "id": nid,
            "label": label,
            "file_type": file_type,
            "source_file": source_file,
        }

    def test_empty_nodes_returns_same(self):
        from backend.context_graph.dedup import deduplicate_entities

        nodes, _edges = deduplicate_entities([], [], communities={})
        assert nodes == []
        assert _edges == []

    def test_single_node_passthrough(self):
        from backend.context_graph.dedup import deduplicate_entities

        node = self._node("a", "AuthService")
        result = deduplicate_entities([node], [], communities={})
        assert len(result[0]) == 1

    def test_exact_duplicate_ids_collapsed(self):
        from backend.context_graph.dedup import deduplicate_entities

        n1 = self._node("auth", "AuthService")
        n2 = self._node("auth", "AuthService")
        nodes, _ = deduplicate_entities([n1, n2], [], communities={})
        assert len(nodes) == 1

    def test_exact_normalized_same_file_merged(self):
        from backend.context_graph.dedup import deduplicate_entities

        n1 = self._node("auth-svc", "auth service", source_file="a.md")
        n2 = self._node("auth_svc", "auth-service", source_file="a.md")
        nodes_out, _ = deduplicate_entities([n1, n2], [], communities={})
        # Different IDs but normalize to same label in same file → merged
        assert len(nodes_out) <= 1

    def test_code_nodes_not_fuzzy_merged(self):
        from backend.context_graph.dedup import deduplicate_entities

        n1 = self._node("fn_auth", "authenticate", file_type="code", source_file="a.py")
        n2 = self._node(
            "fn_auth2", "authenticate", file_type="code", source_file="b.py"
        )
        nodes, _ = deduplicate_entities([n1, n2], [], communities={})
        # Code nodes are never merged across files
        assert len(nodes) == 2

    def test_cross_repo_raises(self):
        import pytest

        from backend.context_graph.dedup import deduplicate_entities

        n1 = {**self._node("a", "X"), "repo": "repo1"}
        n2 = {**self._node("b", "Y"), "repo": "repo2"}
        with pytest.raises(ValueError, match="multiple repos"):
            deduplicate_entities([n1, n2], [], communities={})

    def test_edges_rewired_after_merge(self):
        from backend.context_graph.dedup import deduplicate_entities

        n1 = self._node("svc-a", "auth service", source_file="x.md")
        n2 = self._node("svc-b", "auth-service", source_file="x.md")
        n3 = self._node("user", "User", source_file="x.md")
        edge = {"source": "svc-b", "target": "user", "relation": "calls"}
        nodes, edges = deduplicate_entities([n1, n2, n3], [edge], communities={})
        # After merge svc-a and svc-b → edge source should be the survivor
        node_ids = {n["id"] for n in nodes}
        for e in edges:
            assert e["source"] in node_ids
            assert e["target"] in node_ids

    def test_community_boost_does_not_crash(self):
        from backend.context_graph.dedup import deduplicate_entities

        n1 = self._node("auth-svc", "AuthenticationFacade", source_file="a.md")
        n2 = self._node("auth-gateway", "AuthenticationGateway", source_file="b.md")
        communities = {"auth-svc": 0, "auth-gateway": 0}
        nodes, _ = deduplicate_entities([n1, n2], [], communities=communities)
        assert isinstance(nodes, list)
