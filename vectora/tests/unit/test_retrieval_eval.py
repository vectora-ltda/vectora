from __future__ import annotations

import pytest

from backend.services.retrieval_eval import (
    evaluate_retrieval,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_at_k_ignora_resultados_fora_da_janela() -> None:
    """Verifica o comportamento de recall at k ignora resultados fora da janela."""
    assert recall_at_k(["a", "b", "c"], ["b", "c"], 2) == pytest.approx(0.5)


def test_reciprocal_rank_retorna_primeiro_relevante() -> None:
    """Verifica o comportamento de reciprocal rank retorna primeiro relevante."""
    assert reciprocal_rank(["x", "b", "a"], ["a", "b"]) == pytest.approx(0.5)
    assert reciprocal_rank(["x"], ["a"]) == 0.0


def test_evaluate_retrieval_agrega_casos() -> None:
    """Verifica o comportamento de evaluate retrieval agrega casos."""
    result = evaluate_retrieval(
        [
            {"query": "auth", "relevant_ids": ["a"], "retrieved_ids": ["a"]},
            {"query": "cache", "relevant_ids": ["b"], "retrieved_ids": ["x", "b"]},
        ],
        k=2,
    )
    assert result == {
        "cases": 2,
        "recall_at_k": 1.0,
        "mean_reciprocal_rank_at_k": 0.75,
    }


def test_evaluate_retrieval_rejeita_k_invalido() -> None:
    """Verifica o comportamento de evaluate retrieval rejeita k invalido."""
    with pytest.raises(ValueError, match="positive"):
        evaluate_retrieval([], k=0)
