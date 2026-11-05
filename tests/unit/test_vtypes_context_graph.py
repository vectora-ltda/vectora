"""Testes dos contratos do documento Context Graph."""

from __future__ import annotations

from typing import get_type_hints

from backend.vtypes.context_graph import GraphDocument, GraphEdge, GraphNode


def test_context_graph_contracts_cover_nodes_edges_and_usage() -> None:
    """O documento tipado preserva os campos consumidos por tools e handlers."""
    assert {"nodes", "edges", "hyperedges"} <= set(get_type_hints(GraphDocument))
    assert {"id", "label", "file_type"} <= set(get_type_hints(GraphNode))
    assert {"source", "target", "relation"} <= set(get_type_hints(GraphEdge))
