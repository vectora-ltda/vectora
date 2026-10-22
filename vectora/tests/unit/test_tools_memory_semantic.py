"""Tests para o search_memory tool (busca semântica via store persistente)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.tools.context import ToolContext


class _FakeStoreItem:
    """Item retornado pelo store.asearch — com .key, .value, .score.

    ``search_memory`` delega ao store persistente injetado (``_get_store()``);
    a flag ``semantic`` é derivada de o item ter ``.score`` não-nulo (o store
    com índice vetorial retorna score; sem índice, score é None).
    """

    def __init__(
        self,
        key: str,
        content: str,
        score: float | None = None,
        valid_until: str | None = None,
    ) -> None:
        self.key = key
        self.value = {"content": content, "updated_at": "2025"}
        if valid_until is not None:
            self.value["valid_until"] = valid_until
        self.score = score


class TestSearchMemoryTool:
    @pytest.mark.asyncio
    async def test_search_with_scores_is_semantic(self):
        """Itens com score (store indexado) → semantic=True e score exposto."""
        from backend.tools.memory import search_memory

        ctx = ToolContext(thread_id="t1")
        store = AsyncMock()
        store.asearch = AsyncMock(
            return_value=[_FakeStoreItem("k1", "resultado semântico", score=0.9)]
        )

        with patch("backend.tools.memory._get_store", return_value=store):
            raw = await search_memory(ctx=ctx, query="jwt auth", limit=5)

        data = json.loads(raw)
        assert data["status"] == "success"
        assert data["semantic"] is True
        assert data["count"] == 1
        assert data["memories"][0]["key"] == "k1"
        assert data["memories"][0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_search_without_scores_is_not_semantic(self):
        """Itens sem score (store sem índice) → semantic=False, sem ranqueamento."""
        from backend.tools.memory import search_memory

        ctx = ToolContext(thread_id="t2")
        store = AsyncMock()
        store.asearch = AsyncMock(
            return_value=[_FakeStoreItem("k1", "m1"), _FakeStoreItem("k2", "m2")]
        )

        with patch("backend.tools.memory._get_store", return_value=store):
            raw = await search_memory(ctx=ctx, query="qualquer coisa", limit=5)

        data = json.loads(raw)
        assert data["status"] == "success"
        assert data["semantic"] is False
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        """Store vazio → success com count 0."""
        from backend.tools.memory import search_memory

        ctx = ToolContext(thread_id="t3")
        store = AsyncMock()
        store.asearch = AsyncMock(return_value=[])

        with patch("backend.tools.memory._get_store", return_value=store):
            raw = await search_memory(ctx=ctx, query="teste", limit=5)

        data = json.loads(raw)
        assert data["status"] == "success"
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_search_ignora_expiradas_sem_consumir_limit(self):
        from backend.tools.memory import search_memory

        ctx = ToolContext(thread_id="t5")
        store = AsyncMock()
        store.asearch = AsyncMock(
            return_value=[
                _FakeStoreItem(
                    "old", "expirada", score=1.0, valid_until="2000-01-01T00:00:00"
                ),
                _FakeStoreItem("live", "válida", score=0.5),
            ]
        )

        with patch("backend.tools.memory._get_store", return_value=store):
            data = json.loads(await search_memory(ctx=ctx, query="x", limit=1))

        assert data["count"] == 1
        assert data["memories"][0]["key"] == "live"
        store.asearch.assert_awaited_once_with(
            ("user", "session_t5", "memories"), query="x", limit=1000
        )

    @pytest.mark.asyncio
    async def test_error_returns_failed(self):
        """Exceção ao obter o store retorna status failed com a mensagem."""
        from backend.tools.memory import search_memory

        ctx = ToolContext(thread_id="t4")

        with patch(
            "backend.tools.memory._get_store",
            side_effect=Exception("db error"),
        ):
            raw = await search_memory(ctx=ctx, query="teste", limit=5)

        data = json.loads(raw)
        assert data["status"] == "failed"
        assert "db error" in data["error"]
