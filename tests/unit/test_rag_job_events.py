"""Testes de `_maybe_emit_job_event` — emissão de progresso do RAG via SSE."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from backend.api.handlers import workspaces


class _FakeQueue:
    def __init__(self, stats: dict[str, int]) -> None:
        self._stats = stats

    async def get_job_stats(self, _job_id: str) -> dict[str, int]:
        return self._stats


@pytest.fixture(autouse=True)
def _isolado() -> Generator[None]:
    workspaces._RAG_JOBS.clear()
    workspaces._RAG_LAST_EMITTED_STATUS.clear()
    yield
    workspaces._RAG_JOBS.clear()
    workspaces._RAG_LAST_EMITTED_STATUS.clear()


def _patch_queue(monkeypatch: pytest.MonkeyPatch, stats: dict[str, int]) -> None:
    async def _get_queue(_dsn: str | None) -> _FakeQueue:
        return _FakeQueue(stats)

    monkeypatch.setattr("backend.embedding.queue.get_embedding_queue", _get_queue)


def _capture_emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict]]:
    calls: list[tuple[str, str, dict]] = []

    def _fake_emit(provider: str, event_type: str, data: dict[str, Any]) -> None:
        calls.append((provider, event_type, data))

    monkeypatch.setattr("backend.api.handlers.webhooks._emit_sse_event", _fake_emit)
    return calls


@pytest.mark.asyncio
async def test_job_desconhecido_nao_emite(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_emitted(monkeypatch)
    await workspaces._maybe_emit_job_event("job-inexistente")
    assert calls == []


@pytest.mark.asyncio
async def test_emite_na_primeira_chamada(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces._RAG_JOBS["j1"] = {
        "path": "/tmp",
        "total_chunks": 10,
        "workspace_id": "ws-1",
        "enqueue_done": False,
    }
    _patch_queue(monkeypatch, {"success": 2, "failed": 0, "dlq": 0, "total": 10})
    calls = _capture_emitted(monkeypatch)

    await workspaces._maybe_emit_job_event("j1")

    assert len(calls) == 1
    provider, event_type, data = calls[0]
    assert provider == "rag"
    assert event_type == "rag_job.indexing"
    assert data["job_id"] == "j1"
    assert data["workspace_id"] == "ws-1"
    assert data["processed"] == 2
    assert data["total"] == 10


@pytest.mark.asyncio
async def test_nao_reemite_mesma_faixa_de_progresso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces._RAG_JOBS["j2"] = {
        "path": "/tmp",
        "total_chunks": 100,
        "workspace_id": "ws-1",
        "enqueue_done": False,
    }
    _patch_queue(monkeypatch, {"success": 1, "failed": 0, "dlq": 0, "total": 100})
    calls = _capture_emitted(monkeypatch)

    await workspaces._maybe_emit_job_event("j2")
    # Mesma faixa (~5%) — não deve reemitir.
    await workspaces._maybe_emit_job_event("j2")

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_reemite_ao_saltar_faixa_de_progresso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces._RAG_JOBS["j3"] = {
        "path": "/tmp",
        "total_chunks": 100,
        "workspace_id": "ws-1",
        "enqueue_done": False,
    }
    calls = _capture_emitted(monkeypatch)

    _patch_queue(monkeypatch, {"success": 1, "failed": 0, "dlq": 0, "total": 100})
    await workspaces._maybe_emit_job_event("j3")

    _patch_queue(monkeypatch, {"success": 30, "failed": 0, "dlq": 0, "total": 100})
    await workspaces._maybe_emit_job_event("j3")

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_reemite_ao_mudar_status_mesmo_sem_mudar_faixa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces._RAG_JOBS["j4"] = {
        "path": "/tmp",
        "total_chunks": 5,
        "workspace_id": "ws-1",
        "enqueue_done": False,
    }
    calls = _capture_emitted(monkeypatch)

    _patch_queue(monkeypatch, {"success": 5, "failed": 0, "dlq": 0, "total": 5})
    await workspaces._maybe_emit_job_event("j4")
    assert calls[-1][1] == "rag_job.indexing"

    workspaces._RAG_JOBS["j4"]["enqueue_done"] = True
    await workspaces._maybe_emit_job_event("j4")

    assert len(calls) == 2
    assert calls[-1][1] == "rag_job.done"


@pytest.mark.asyncio
async def test_erro_ao_buscar_stats_nao_propaga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces._RAG_JOBS["j5"] = {
        "path": "/tmp",
        "total_chunks": 10,
        "workspace_id": "ws-1",
        "enqueue_done": False,
    }

    async def _boom(_dsn: str | None) -> Any:
        raise RuntimeError("fila indisponível")

    monkeypatch.setattr("backend.embedding.queue.get_embedding_queue", _boom)
    calls = _capture_emitted(monkeypatch)

    await workspaces._maybe_emit_job_event("j5")

    assert calls == []
