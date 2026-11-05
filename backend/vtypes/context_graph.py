"""Contratos do documento JSON usado pelo Context Graph."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

GraphFileType = Literal["code", "document", "paper", "image", "rationale", "concept"]
GraphConfidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]
GraphRelation = Literal[
    "calls",
    "implements",
    "references",
    "cites",
    "conceptually_related_to",
    "shares_data_with",
]


class GraphNode(TypedDict, total=False):
    """Entidade indexada pelo grafo, com campos opcionais para dados legados."""

    id: str
    label: str
    file_type: GraphFileType
    source_file: str
    source_location: str | None
    docstring: str
    rationale: str


class GraphEdge(TypedDict, total=False):
    """Relação orientada entre duas entidades do grafo."""

    source: str
    target: str
    relation: GraphRelation
    confidence: GraphConfidence
    confidence_score: float
    source_file: str
    source_location: str | None
    weight: float


class GraphHyperedge(TypedDict, total=False):
    """Relação que envolve três ou mais nós simultaneamente."""

    id: str
    label: str
    nodes: list[str]
    relation: Literal["participate_in", "implement", "form"]
    confidence: Literal["EXTRACTED", "INFERRED"]
    confidence_score: float
    source_file: str


class GraphDocument(TypedDict, total=False):
    """Documento completo persistido em ``graph.json``."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    hyperedges: list[GraphHyperedge]
    input_tokens: int
    output_tokens: int
