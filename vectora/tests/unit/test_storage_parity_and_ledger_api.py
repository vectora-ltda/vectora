"""Testes de contrato e paridade para backends vetoriais (LanceDB vs Qdrant) e rotas de eventos (#352, #367)."""

from __future__ import annotations

import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.server import create_app
from backend.persistence.native.ledger import ThreadLedgerEvent
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
async def test_thread_events_sse_stream_and_read(monkeypatch: pytest.MonkeyPatch):
    """Testa o streaming SSE de eventos do ledger e mark_thread_read com seen_seq (#352, #367)."""
    from unittest.mock import AsyncMock, MagicMock

    import aiosqlite

    import backend.api.handlers.threads as th

    # Isola banco de metadados e session store em memória para evitar concorrência com o host
    checkpoints_conn = await aiosqlite.connect(":memory:")
    await th._ensure_schema(checkpoints_conn)

    pool = AsyncConnectionPool(":memory:", min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()

    monkeypatch.setattr(th, "_get_db", AsyncMock(return_value=checkpoints_conn))
    monkeypatch.setattr(th, "_get_session_store", AsyncMock(return_value=store))

    try:
        # 1. Criar thread via CreateThread
        from backend.api.schemas import CreateThreadRequest

        mock_http = MagicMock()
        mock_http.state = MagicMock(user=None)
        created = await th.create_thread(
            CreateThreadRequest(workspace_id="", mode="chat"),
            mock_http,
        )
        thread_id = created.id

        # 2. Inserir eventos de teste no ledger
        ev1 = await store.append_event(
            thread_id,
            ThreadLedgerEvent(
                thread_id=thread_id,
                event_key=f"run_started:{thread_id}:1",
                type="agent_turn_started",
                run_id="run-1",
                payload={"model": "test-model"},
            ),
        )
        ev2 = await store.append_event(
            thread_id,
            ThreadLedgerEvent(
                thread_id=thread_id,
                event_key=f"run_completed:{thread_id}:1",
                type="run_completed",
                run_id="run-1",
                payload={"stopped_reason": "stop"},
            ),
        )
        assert ev1.seq < ev2.seq

        # 3. Consumir SSE desde seq 0 (replay completo)
        req_stream = MagicMock()
        req_stream.state = MagicMock(user=None)
        req_stream.is_disconnected = AsyncMock(return_value=False)
        stream_resp = await th.stream_thread_ledger_events(
            thread_id,
            req_stream,
            from_seq=0,
        )
        chunks: list[str] = []
        async for chunk in stream_resp.body_iterator:
            text = chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8")
            chunks.append(text)
            if len(chunks) >= 2:
                break

        def _parse_chunk(chunk: str) -> dict[str, str]:
            parsed: dict[str, str] = {}
            for line in chunk.strip().split("\n"):
                if line.startswith("id: "):
                    parsed["id"] = line[4:].strip()
                elif line.startswith("event: "):
                    parsed["event"] = line[7:].strip()
                elif line.startswith("data: "):
                    parsed["data"] = line[6:].strip()
            return parsed

        p1 = _parse_chunk(chunks[0])
        p2 = _parse_chunk(chunks[1])

        assert p1["id"] == str(ev1.seq)
        assert p1["event"] == "agent_turn_started"
        assert p2["id"] == str(ev2.seq)
        assert p2["event"] == "run_completed"
        assert int(p1["id"]) < int(p2["id"])

        # 4. Validar Last-Event-ID header (retorna apenas eventos posteriores)
        resp_last = await th.stream_thread_ledger_events(
            thread_id,
            req_stream,
            last_event_id=str(ev1.seq),
        )
        chunk_last_text = ""
        async for chunk in resp_last.body_iterator:
            chunk_last_text = (
                chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8")
            )
            break
        p_last = _parse_chunk(chunk_last_text)
        assert p_last["id"] == str(ev2.seq)
        assert p_last["event"] == "run_completed"

        # 5. Marcar leitura com seen_seq e device_id válido
        device_id = "vdev_12345678-1234-1234-1234-123456789abc"
        read_req = th.MarkThreadReadRequest(seen_seq=ev2.seq, device_id=device_id)
        read_resp = await th.mark_thread_read(thread_id, mock_http, read_req)
        assert read_resp["thread_id"] == thread_id
        assert read_resp["read_seq"] == ev2.seq
        assert "unread_count" in read_resp

    finally:
        await pool.close()
        await checkpoints_conn.close()


@pytest.mark.asyncio
async def test_thread_not_found_uniform_404(monkeypatch: pytest.MonkeyPatch):
    """Testa que threads inexistentes retornam 404 padronizado para o frontend."""
    from unittest.mock import AsyncMock, MagicMock

    import aiosqlite
    from fastapi import HTTPException

    import backend.api.handlers.threads as th
    from backend.api.schemas import GetThreadRequest

    checkpoints_conn = await aiosqlite.connect(":memory:")
    await th._ensure_schema(checkpoints_conn)

    pool = AsyncConnectionPool(":memory:", min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()

    monkeypatch.setattr(th, "_get_db", AsyncMock(return_value=checkpoints_conn))
    monkeypatch.setattr(th, "_get_session_store", AsyncMock(return_value=store))

    try:
        mock_http = MagicMock()
        mock_http.state = MagicMock(user=None)
        with pytest.raises(HTTPException) as exc_info:
            await th.get_thread(
                GetThreadRequest(thread_id="non-existent-thread-id"),
                mock_http,
            )
        assert exc_info.value.status_code == 404
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail.get("code") == "thread_not_found"
        assert detail.get("thread_id") == "non-existent-thread-id"
        assert detail.get("deleted") is True
    finally:
        await pool.close()
        await checkpoints_conn.close()
