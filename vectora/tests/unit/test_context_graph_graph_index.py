"""Contract tests for semantic indexing failure visibility."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_strict_indexing_propagates_embedding_failure(monkeypatch):
    from backend.context_graph import graph_index

    async def fail(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Cohere billing blocked")

    monkeypatch.setattr(graph_index, "_embed_texts", fail)

    with pytest.raises(RuntimeError, match="falha ao indexar"):
        await graph_index.index_graph_nodes(
            "workspace-1",
            {"nodes": [{"id": "node-1", "label": "Node"}]},
            strict=True,
        )


@pytest.mark.asyncio
async def test_non_strict_indexing_keeps_optional_search_defensive(monkeypatch):
    from backend.context_graph import graph_index

    async def fail(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(graph_index, "_embed_texts", fail)

    assert (
        await graph_index.index_graph_nodes(
            "workspace-1",
            {"nodes": [{"id": "node-1", "label": "Node"}]},
        )
        == 0
    )
