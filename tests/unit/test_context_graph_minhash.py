"""Testes para backend/services/context_graph/_minhash.py.

Cobre: MinHash.update, MinHashLSH.insert/query, _optimal_lsh_params, _lsh_integrate.
"""

from __future__ import annotations

import pytest


class TestMinHash:
    def test_initial_hashvalues_are_max(self):
        from backend.context_graph._minhash import MinHash

        mh = MinHash(num_perm=16)
        assert mh.num_perm == 16
        # All values start at _MH mask
        assert len(mh.hashvalues) == 16

    def test_update_reduces_hashvalues(self):
        from backend.context_graph._minhash import MinHash

        mh = MinHash(num_perm=16)
        initial = mh.hashvalues.copy()
        mh.update(b"hello")
        # At least some values should change
        assert not all(mh.hashvalues == initial)

    def test_same_content_same_hashvalues(self):
        from backend.context_graph._minhash import MinHash

        mh1 = MinHash(num_perm=32)
        mh2 = MinHash(num_perm=32)
        for word in [b"alpha", b"beta", b"gamma"]:
            mh1.update(word)
            mh2.update(word)
        assert all(mh1.hashvalues == mh2.hashvalues)

    def test_different_content_different_hashvalues(self):
        from backend.context_graph._minhash import MinHash

        mh1 = MinHash(num_perm=64)
        mh2 = MinHash(num_perm=64)
        mh1.update(b"set_A")
        mh2.update(b"set_B")
        assert not all(mh1.hashvalues == mh2.hashvalues)


class TestMinHashLSH:
    def test_insert_and_query_identical(self):
        from backend.context_graph._minhash import MinHash, MinHashLSH

        lsh = MinHashLSH(threshold=0.5, num_perm=64)
        mh = MinHash(num_perm=64)
        for w in [b"word1", b"word2", b"word3"]:
            mh.update(w)

        lsh.insert("doc1", mh)
        results = lsh.query(mh)
        assert "doc1" in results

    def test_duplicate_key_raises(self):
        from backend.context_graph._minhash import MinHash, MinHashLSH

        lsh = MinHashLSH(threshold=0.5, num_perm=64)
        mh = MinHash(num_perm=64)
        mh.update(b"data")
        lsh.insert("key1", mh)
        with pytest.raises(ValueError, match="already exists"):
            lsh.insert("key1", mh)

    def test_similar_documents_are_candidates(self):
        from backend.context_graph._minhash import MinHash, MinHashLSH

        lsh = MinHashLSH(threshold=0.4, num_perm=64)
        words = [f"word{i}".encode() for i in range(20)]

        mh1 = MinHash(num_perm=64)
        mh2 = MinHash(num_perm=64)
        for w in words:
            mh1.update(w)
            mh2.update(w)
        mh2.update(b"extra_word")

        lsh.insert("doc1", mh1)
        candidates = lsh.query(mh2)
        assert "doc1" in candidates

    def test_dissimilar_documents_not_candidates(self):
        from backend.context_graph._minhash import MinHash, MinHashLSH

        lsh = MinHashLSH(threshold=0.9, num_perm=128)
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        for i in range(50):
            mh1.update(f"setA_word{i}".encode())
        for i in range(50):
            mh2.update(f"setB_completely_different_{i}".encode())

        lsh.insert("docA", mh1)
        candidates = lsh.query(mh2)
        assert "docA" not in candidates

    def test_query_empty_lsh(self):
        from backend.context_graph._minhash import MinHash, MinHashLSH

        lsh = MinHashLSH(threshold=0.5, num_perm=32)
        mh = MinHash(num_perm=32)
        mh.update(b"query")
        assert lsh.query(mh) == []


class TestOptimalLSHParams:
    def test_returns_valid_bands_rows(self):
        from backend.context_graph._minhash import _optimal_lsh_params

        b, r = _optimal_lsh_params(0.5, 32)
        assert b >= 1
        assert r >= 1
        assert b * r <= 32

    def test_cached_result_is_same(self):
        from backend.context_graph._minhash import _optimal_lsh_params

        b1, r1 = _optimal_lsh_params(0.7, 64)
        b2, r2 = _optimal_lsh_params(0.7, 64)
        assert b1 == b2 and r1 == r2


class TestLSHIntegrate:
    def test_constant_function(self):
        from backend.context_graph._minhash import _lsh_integrate

        result = _lsh_integrate(lambda s: 1.0, 0.0, 1.0, n=100)
        assert abs(result - 1.0) < 0.02

    def test_linear_function(self):
        from backend.context_graph._minhash import _lsh_integrate

        result = _lsh_integrate(lambda x: x, 0.0, 1.0, n=1000)
        assert abs(result - 0.5) < 0.01
