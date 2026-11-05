"""Métricas determinísticas para avaliar retrieval sem chamar providers.

Os casos de avaliação são deliberadamente pequenos e serializáveis. Isso
permite manter um conjunto de consultas de regressão no repositório e medir
mudanças de ranking de LanceDB, Qdrant ou de um reranker sem transformar um
benchmark live em prova funcional.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict


class RetrievalEvalCase(TypedDict):
    """Caso mínimo: IDs relevantes e IDs devolvidos na ordem observada."""

    query: str
    relevant_ids: list[str]
    retrieved_ids: list[str]


class RetrievalEvalResult(TypedDict):
    """Métricas agregadas e quantidade de casos avaliados."""

    cases: int
    recall_at_k: float
    mean_reciprocal_rank_at_k: float


def recall_at_k(
    retrieved_ids: Sequence[str], relevant_ids: Sequence[str], k: int
) -> float:
    """Calcula recall@k, preservando a definição por conjunto."""
    if k <= 0 or not relevant_ids:
        return 0.0
    retrieved = set(retrieved_ids[:k])
    relevant = set(relevant_ids)
    return len(retrieved & relevant) / len(relevant)


def reciprocal_rank(
    retrieved_ids: Sequence[str], relevant_ids: Sequence[str], k: int | None = None
) -> float:
    """Retorna o inverso da posição do primeiro resultado relevante."""
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    candidates = retrieved_ids if k is None else retrieved_ids[: max(0, k)]
    for rank, document_id in enumerate(candidates, start=1):
        if document_id in relevant:
            return 1.0 / rank
    return 0.0


def evaluate_retrieval(
    cases: Sequence[RetrievalEvalCase], *, k: int
) -> RetrievalEvalResult:
    """Agrega recall@k e MRR de um conjunto de casos de regressão."""
    if k <= 0:
        raise ValueError("k must be positive")
    if not cases:
        return {"cases": 0, "recall_at_k": 0.0, "mean_reciprocal_rank_at_k": 0.0}

    recall_total = 0.0
    rank_total = 0.0
    for case in cases:
        recall_total += recall_at_k(case["retrieved_ids"], case["relevant_ids"], k)
        rank_total += reciprocal_rank(case["retrieved_ids"], case["relevant_ids"], k)
    count = len(cases)
    return {
        "cases": count,
        "recall_at_k": recall_total / count,
        "mean_reciprocal_rank_at_k": rank_total / count,
    }


__all__ = [
    "RetrievalEvalCase",
    "RetrievalEvalResult",
    "evaluate_retrieval",
    "recall_at_k",
    "reciprocal_rank",
]
