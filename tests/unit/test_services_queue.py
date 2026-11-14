"""Tests for src/services/queue.py"""

from __future__ import annotations

import pytest

import backend.embedding.queue as _queue_mod
from backend.embedding.queue import get_embedding_queue

_DSN = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the global queue singleton between tests."""
    original = _queue_mod._queue
    _queue_mod._queue = None
    yield
    _queue_mod._queue = original


@pytest.fixture
async def queue():
    return await get_embedding_queue(_DSN)


class TestEmbeddingQueue:
    @pytest.mark.asyncio
    async def test_enqueue_returns_id(self, queue):
        qid = await queue.enqueue("texto", "articles", {})
        assert isinstance(qid, str)
        assert len(qid) > 0

    @pytest.mark.asyncio
    async def test_get_pending_after_enqueue(self, queue):
        await queue.enqueue("texto pendente", "articles", {})
        items = await queue.get_pending(limit=10)
        assert len(items) >= 1
        texts = [i.text for i in items]
        assert "texto pendente" in texts

    @pytest.mark.asyncio
    async def test_mark_processing_then_success(self, queue):
        qid = await queue.enqueue("texto done", "articles", {})
        await queue.mark_processing(qid)
        await queue.mark_success(qid)
        pending = await queue.get_pending(limit=10)
        ids = [i.id for i in pending]
        assert qid not in ids

    @pytest.mark.asyncio
    async def test_count_pending(self, queue):
        count = await queue.count_pending()
        assert isinstance(count, int)
        assert count >= 0

    @pytest.mark.asyncio
    async def test_reconcile_runs_without_error(self, queue):
        # enqueue and mark processing — reconcile won't reset because it's recent
        qid = await queue.enqueue("reconcile text", "articles", {})
        await queue.mark_processing(qid)
        await queue.reconcile()  # should not raise
        pending = await queue.get_pending(limit=10)
        # item is still "processing" (updated_at < 2 min ago), won't be reset
        assert isinstance(pending, list)

    @pytest.mark.asyncio
    async def test_close_disposes_engine(self, queue):
        await queue.close()
        # after close engine should be disposed; calling close again is safe
        await queue.close()

    @pytest.mark.asyncio
    async def test_archive_pending_empty_returns_zero(self, queue):
        assert await queue.archive_pending("motivo") == 0

    @pytest.mark.asyncio
    async def test_get_embedding_queue_raises_if_none_dsn(self):
        with pytest.raises(ValueError, match="embedding_queue_dsn"):
            await get_embedding_queue(None)

    @pytest.mark.asyncio
    async def test_get_embedding_queue_singleton(self):
        q1 = await get_embedding_queue(_DSN)
        q2 = await get_embedding_queue(_DSN)
        assert q1 is q2
