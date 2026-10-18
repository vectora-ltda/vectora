"""Testes de contrato e paridade para backends vetoriais (LanceDB vs Qdrant) e rotas de eventos (#352, #367)."""

from __future__ import annotations

import tempfile
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.server import create_app
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.storage.vectorstore.base import VectorHit, VectorRow
from backend.storage.vectorstore.lancedb_backend import LanceDBBackend


@pytest.mark.asyncio
async def test_lancedb_upsert_idempotence():
    """Testa que LanceDBBackend.upsert com mesmo ID atualiza em vez de duplicar."""
    with tempfile.TemporaryDirectory() as td:
        backend = LanceDBBackend(td)

        # 1. Inserir documento inicial
        doc1 = VectorRow(
            id="doc-1",
            vector=[0.1, 0.2, 0.3],
            text="versão original",
            metadata={"source": "test"},
        )
        await backend.upsert("test_col", [doc1])
        assert await backend.count("test_col") == 1

        # 2. Upsert com o mesmo ID e texto modificado
        doc1_v2 = VectorRow(
            id="doc-1",
            vector=[0.1, 0.2, 0.3],
            text="versão atualizada",
            metadata={"source": "test", "v": 2},
        )
        await backend.upsert("test_col", [doc1_v2])
        assert await backend.count("test_col") == 1

        # 3. Conferir se o conteúdo foi realmente atualizado
        rows = await backend.list_rows("test_col")
        assert len(rows) == 1
        assert rows[0].id == "doc-1"
        assert rows[0].text == "versão atualizada"
        assert rows[0].metadata.get("v") == 2


@pytest.mark.asyncio
async def test_lancedb_delete_exact_count():
    """Testa que LanceDBBackend.delete retorna a contagem exata de deletados."""
    with tempfile.TemporaryDirectory() as td:
        backend = LanceDBBackend(td)

        rows = [
            VectorRow(id="doc-1", vector=[0.1, 0.2], text="one", metadata={}),
            VectorRow(id="doc-2", vector=[0.3, 0.4], text="two", metadata={}),
        ]
        await backend.upsert("test_col", rows)
        assert await backend.count("test_col") == 2

        # Deletar um id existente e um inexistente
        deleted_count = await backend.delete("test_col", ["doc-1", "non-existent"])
        assert deleted_count == 1
        assert await backend.count("test_col") == 1

        # Deletar coleção ausente
        assert await backend.delete("missing_col", ["doc-1"]) == 0


@pytest.mark.asyncio
async def test_lancedb_health():
    """Testa o método health() do LanceDBBackend."""
    with tempfile.TemporaryDirectory() as td:
        backend = LanceDBBackend(td)
        h = await backend.health()
        assert h["ok"] is True
        assert "tables" in h


@pytest.mark.asyncio
async def test_thread_events_sse_stream_and_read():
    """Testa o streaming SSE de eventos do ledger e mark_thread_read com seen_seq."""
    app = create_app(serve_static=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Criar thread
        resp = await client.post(
            "/vectora.chat.v1.ThreadService/CreateThread",
            json={"workspace_id": "", "mode": "chat"},
        )
        assert resp.status_code == 200
        thread_id = resp.json()["id"]

        # Consultar eventos via SSE
        sse_resp = await client.get(f"/threads/{thread_id}/events?from_seq=0")
        assert sse_resp.status_code == 200
        assert "text/event-stream" in sse_resp.headers["content-type"]
        assert "keep-alive" in sse_resp.text

        # Marcar leitura com seen_seq
        read_resp = await client.post(
            f"/threads/{thread_id}/read",
            json={"seen_seq": 10, "device_id": "test-device"},
        )
        assert read_resp.status_code == 200
        data = read_resp.json()
        assert data["thread_id"] == thread_id
        assert "read_seq" in data
        assert "unread_count" in data


@pytest.mark.asyncio
async def test_thread_not_found_uniform_404():
    """Testa que threads inexistentes retornam 404 padronizado para o frontend."""
    app = create_app(serve_static=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/vectora.chat.v1.ThreadService/GetThread",
            json={"thread_id": "non-existent-thread-id"},
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail.get("code") == "thread_not_found"
        assert detail.get("thread_id") == "non-existent-thread-id"
        assert detail.get("deleted") is True
