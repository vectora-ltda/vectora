"""Tests for src/services/tracer.py

Cobre VectoraTracer sem APIs externas:
- span() context manager: sucesso, erro, set()
- record_sync(): grava span síncrono
- get_session(): filtra por session_id
- get_recent(): retorna N spans
- clear_session(): remove spans de uma session
- clear_all(): remove todos os spans
- Singleton: instância module-level
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from backend.persistence.tracer import VectoraTracer, tracer


@pytest.fixture
async def tr(tmp_path):
    """Tracer isolado com banco temporário."""
    return VectoraTracer(db_path=tmp_path / "test_traces.db")


class TestSpanContextManager:
    @pytest.mark.asyncio
    async def test_span_records_on_success(self, tr):
        async with tr.span("test_node", "call", session_id=1) as s:
            s.set(key="value")

        spans = await tr.get_recent(n=10)
        assert len(spans) == 1
        assert spans[0]["node"] == "test_node"
        assert spans[0]["event"] == "call"
        assert spans[0]["status"] == "ok"
        assert spans[0]["session_id"] == 1

    @pytest.mark.asyncio
    async def test_span_records_duration(self, tr):
        async with tr.span("timed_node", "op") as _:
            await asyncio.sleep(0.01)

        spans = await tr.get_recent(n=1)
        assert spans[0]["duration_ms"] >= 5  # pelo menos 5ms

    @pytest.mark.asyncio
    async def test_span_marks_error_on_exception(self, tr):
        with pytest.raises(ValueError):
            async with tr.span("err_node", "fail") as _:
                raise ValueError("boom")

        spans = await tr.get_recent(n=1)
        assert spans[0]["status"] == "error"
        meta = json.loads(spans[0]["metadata"])
        assert meta.get("error") == "ValueError"

    @pytest.mark.asyncio
    async def test_span_set_stores_metadata(self, tr):
        async with tr.span("orchestrator", "route") as s:
            s.set(routing="search", intent="search", query_len=50)

        spans = await tr.get_recent(n=1)
        meta = json.loads(spans[0]["metadata"])
        assert meta["routing"] == "search"
        assert meta["query_len"] == 50

    @pytest.mark.asyncio
    async def test_span_set_in_out_tokens(self, tr):
        async with tr.span("invoke_llm", "call") as s:
            s.set(in_tokens=100, out_tokens=40)

        spans = await tr.get_recent(n=1)
        assert spans[0]["in_tokens"] == 100
        assert spans[0]["out_tokens"] == 40

    @pytest.mark.asyncio
    async def test_span_set_custom_status(self, tr):
        async with tr.span("quota", "call") as s:
            s.set(status="quota_error")

        spans = await tr.get_recent(n=1)
        assert spans[0]["status"] == "quota_error"

    @pytest.mark.asyncio
    async def test_span_no_session_id(self, tr):
        async with tr.span("node", "op") as _:
            pass

        spans = await tr.get_recent(n=1)
        assert spans[0]["session_id"] is None

    @pytest.mark.asyncio
    async def test_multiple_spans_same_session(self, tr):
        async with tr.span("a", "op", session_id=42):
            pass
        async with tr.span("b", "op", session_id=42):
            pass
        async with tr.span("c", "op", session_id=99):
            pass

        session_42 = await tr.get_session(42)
        assert len(session_42) == 2
        nodes = {s["node"] for s in session_42}
        assert nodes == {"a", "b"}


class TestRecordSync:
    @pytest.mark.asyncio
    async def test_record_sync_creates_span(self, tr):
        tr.record_sync("web_search", "call", 0.5, {"query": "test"})
        spans = await tr.get_recent(n=5)
        web_spans = [s for s in spans if s["node"] == "web_search"]
        assert len(web_spans) >= 1
        assert web_spans[0]["duration_ms"] == pytest.approx(500.0, abs=1)

    @pytest.mark.asyncio
    async def test_record_sync_with_status_error(self, tr):
        tr.record_sync("fetch_url", "call", 0.1, status="error")
        spans = await tr.get_recent(n=5)
        url_spans = [s for s in spans if s["node"] == "fetch_url"]
        assert url_spans[0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_record_sync_with_session_id(self, tr):
        tr.record_sync("web_search", "call", 0.2, session_id=77)
        spans = await tr.get_session(77)
        assert len(spans) >= 1

    def test_record_sync_doesnt_raise_on_bad_path(self, tmp_path):
        """record_sync deve ser silencioso em caso de erro."""
        t = VectoraTracer(db_path="/invalid/path/that/cannot/be/created/traces.db")
        # Não deve lançar exceção
        t.record_sync("node", "event", 0.1)


class TestQueryMethods:
    @pytest.mark.asyncio
    async def test_get_session_returns_only_matching(self, tr):
        async with tr.span("node", "op", session_id=10):
            pass
        async with tr.span("node", "op", session_id=20):
            pass

        s10 = await tr.get_session(10)
        s20 = await tr.get_session(20)
        assert len(s10) == 1
        assert len(s20) == 1

    @pytest.mark.asyncio
    async def test_get_recent_respects_limit(self, tr):
        for _ in range(10):
            async with tr.span("node", "op"):
                pass

        recent = await tr.get_recent(n=3)
        assert len(recent) == 3

    @pytest.mark.asyncio
    async def test_get_session_empty_returns_empty(self, tr):
        spans = await tr.get_session(9999)
        assert spans == []

    @pytest.mark.asyncio
    async def test_get_recent_on_empty_db_returns_empty(self, tr):
        spans = await tr.get_recent(n=10)
        assert spans == []

    @pytest.mark.asyncio
    async def test_get_recent_on_missing_db_returns_empty(self, tmp_path):
        t = VectoraTracer(db_path=tmp_path / "missing.db")
        # arquivo não existe ainda
        spans = await t.get_recent(n=10)
        assert spans == []


class TestClearMethods:
    @pytest.mark.asyncio
    async def test_clear_session_removes_only_target(self, tr):
        async with tr.span("n", "o", session_id=1):
            pass
        async with tr.span("n", "o", session_id=2):
            pass

        removed = await tr.clear_session(1)
        assert removed == 1
        assert await tr.get_session(1) == []
        assert len(await tr.get_session(2)) == 1

    @pytest.mark.asyncio
    async def test_clear_all_removes_everything(self, tr):
        async with tr.span("a", "o", session_id=1):
            pass
        async with tr.span("b", "o", session_id=2):
            pass

        removed = await tr.clear_all()
        assert removed == 2
        assert await tr.get_recent(n=100) == []

    @pytest.mark.asyncio
    async def test_clear_session_nonexistent_returns_zero(self, tr):
        removed = await tr.clear_session(9999)
        assert removed == 0

    @pytest.mark.asyncio
    async def test_clear_all_on_missing_db_returns_zero(self, tmp_path):
        t = VectoraTracer(db_path=tmp_path / "missing.db")
        removed = await t.clear_all()
        assert removed == 0


class TestSingleton:
    def test_module_singleton_exists(self):
        """O tracer singleton de módulo deve ser uma instância de VectoraTracer."""
        assert isinstance(tracer, VectoraTracer)

    def test_two_imports_same_object(self):
        """Importações múltiplas devem retornar o mesmo objeto."""
        from backend.persistence.tracer import tracer as t2

        assert tracer is t2

    @pytest.mark.asyncio
    async def test_singleton_can_record_and_query(self, tmp_path):
        """O singleton funciona com DB temporário."""
        t = VectoraTracer(db_path=tmp_path / "singleton_test.db")
        async with t.span("singleton_node", "test", session_id=1212):
            pass
        spans = await t.get_session(1212)
        assert len(spans) == 1
        assert spans[0]["node"] == "singleton_node"
