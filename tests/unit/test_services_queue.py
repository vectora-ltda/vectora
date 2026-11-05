"""Tests for src/services/queue.py"""

from __future__ import annotations

import pytest

import backend.embedding.queue as _queue_mod
from backend.embedding.queue import EmbeddingQueue, get_embedding_queue

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
    async def test_mark_failed(self, queue):
        qid = await queue.enqueue("texto failed", "articles", {})
        await queue.mark_processing(qid)
        await queue.mark_failed(qid, "test error")
        pending = await queue.get_pending(limit=10)
        ids = [i.id for i in pending]
        assert qid not in ids

    @pytest.mark.asyncio
    async def test_get_stats(self, queue):
        stats = await queue.get_stats()
        assert isinstance(stats, dict)

    @pytest.mark.asyncio
    async def test_count_pending(self, queue):
        count = await queue.count_pending()
        assert isinstance(count, int)
        assert count >= 0

    @pytest.mark.asyncio
    async def test_mark_dlq(self, queue):
        qid = await queue.enqueue("dlq text", "articles", {})
        await queue.mark_processing(qid)
        await queue.mark_dlq(qid, "max retries exceeded")
        failed = await queue.get_failed(limit=10)
        q_ids = [r.queue_id for r in failed]
        assert qid in q_ids

    @pytest.mark.asyncio
    async def test_get_failed_returns_failed_and_dlq(self, queue):
        qid_f = await queue.enqueue("failed item", "articles", {})
        qid_d = await queue.enqueue("dlq item", "articles", {})
        await queue.mark_processing(qid_f)
        await queue.mark_failed(qid_f, "some error")
        await queue.mark_processing(qid_d)
        await queue.mark_dlq(qid_d, "dlq reason")
        failed = await queue.get_failed(limit=10)
        q_ids = [r.queue_id for r in failed]
        assert qid_f in q_ids
        assert qid_d in q_ids

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
    async def test_get_stats_has_all_statuses(self, queue):
        stats = await queue.get_stats()
        for key in ("pending", "processing", "success", "failed", "dlq"):
            assert key in stats

    @pytest.mark.asyncio
    async def test_archive_pending_moves_to_dlq(self, queue):
        """archive_pending arquiva pending + processing para DLQ de uma vez."""
        qid_pending = await queue.enqueue("a indexar", "articles", {})
        qid_processing = await queue.enqueue("em processamento", "articles", {})
        await queue.mark_processing(qid_processing)

        archived = await queue.archive_pending("Cohere rate limit (429)")

        assert archived == 2
        # Nada mais pendente — não há re-storm no próximo start.
        assert await queue.get_pending(limit=10) == []
        failed = await queue.get_failed(limit=10)
        q_ids = {r.queue_id for r in failed}
        assert {qid_pending, qid_processing} <= q_ids
        assert all(r.dlq_reason == "Cohere rate limit (429)" for r in failed)

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
