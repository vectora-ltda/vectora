"""Tests para namespace de memória com workspace."""

from __future__ import annotations

import pytest

from backend.tools.context import ToolContext
from backend.tools.memory import _user_id_from_ctx

# ---------------------------------------------------------------------------
# _user_id_from_ctx
# ---------------------------------------------------------------------------


def test_workspace_namespace_when_workspace_id_present():
    """workspace_id presente → namespace workspace_<id>."""
    ctx = ToolContext(workspace_id="abc12345", thread_id="001")
    assert _user_id_from_ctx(ctx) == "workspace_abc12345"


def test_session_namespace_fallback_no_workspace_id():
    """Sem workspace_id → fallback para session_<thread_id>."""
    ctx = ToolContext(thread_id="001")
    assert _user_id_from_ctx(ctx) == "session_001"


def test_default_session_when_ctx_bare():
    """ctx sem nenhum identificador → "local"."""
    assert _user_id_from_ctx(ToolContext()) == "local"


def test_workspace_takes_priority_over_thread_id():
    """workspace_id tem prioridade sobre thread_id."""
    ctx = ToolContext(workspace_id="ws001", thread_id="t999")
    result = _user_id_from_ctx(ctx)
    assert result == "workspace_ws001"
    assert "t999" not in result


async def test_expiring_scoped_memory_preserves_legacy_in_other_workspace() -> None:
    """Expiry only cleans the expired record, preserving shared legacy context."""
    import json

    from backend.testing.mocks import NativeInMemoryStore
    from backend.tools.memory import _memory_namespace, get_memory

    store = NativeInMemoryStore()
    first = ToolContext(user_id="alice", workspace_id="first", store=store)
    second = ToolContext(user_id="alice", workspace_id="second", store=store)
    legacy = ("user", "alice", "memories")
    await store.aput(legacy, "key", {"content": "shared"})
    await store.aput(
        _memory_namespace(first),
        "key",
        {
            "content": "expired",
            "valid_until": "2000-01-01T00:00:00Z",
        },
    )
    assert json.loads(await get_memory(first, "key"))["content"] == "shared"
    assert await store.aget(_memory_namespace(first), "key") is None
    assert await store.aget(legacy, "key") is not None
    assert json.loads(await get_memory(second, "key"))["content"] == "shared"


async def test_explicit_delete_removes_legacy_but_preserves_other_workspace() -> None:
    """Deletion removes shared fallback without touching another scoped record."""
    import json

    from backend.testing.mocks import NativeInMemoryStore
    from backend.tools.memory import _memory_namespace, delete_memory, get_memory

    store = NativeInMemoryStore()
    first = ToolContext(user_id="alice", workspace_id="first", store=store)
    second = ToolContext(user_id="alice", workspace_id="second", store=store)
    legacy = ("user", "alice", "memories")
    await store.aput(legacy, "key", {"content": "shared"})
    await store.aput(legacy, "only-fallback", {"content": "shared fallback"})
    await store.aput(_memory_namespace(first), "key", {"content": "first"})
    await store.aput(_memory_namespace(second), "key", {"content": "second"})
    assert json.loads(await delete_memory(first, "key"))["status"] == "deleted"
    assert (
        json.loads(await delete_memory(first, "only-fallback"))["status"] == "deleted"
    )
    assert await store.aget(legacy, "key") is None
    assert await store.aget(_memory_namespace(first), "key") is None
    assert json.loads(await get_memory(second, "key"))["content"] == "second"
    assert (
        json.loads(await get_memory(second, "only-fallback"))["status"] == "not_found"
    )


async def test_listing_skips_expired_scoped_copy_before_deduplicating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired scoped copy cannot hide the valid legacy copy of the same key."""
    import json

    from backend.services import agent_factory
    from backend.testing.mocks import NativeInMemoryStore
    from backend.tools.memory import _memory_namespace, get_memory, list_fact_contents

    store = NativeInMemoryStore()
    ctx = ToolContext(user_id="alice", workspace_id="first", store=store)
    legacy = ("user", "alice", "memories")
    expired = {"content": "expired", "valid_until": "2000-01-01T00:00:00Z"}
    await store.aput(legacy, "key", {"content": "shared"})
    await store.aput(_memory_namespace(ctx), "key", expired)

    listed = json.loads(await get_memory(ctx))["memories"]
    assert [(m["key"], m["content"]) for m in listed] == [("key", "shared")]

    async def _store() -> NativeInMemoryStore:
        return store

    monkeypatch.setattr(agent_factory, "get_store", _store)
    assert await list_fact_contents("alice") == ["shared"]

    await store.aput(legacy, "key", expired)
    assert json.loads(await get_memory(ctx))["memories"] == []
    assert await list_fact_contents("alice") == []


async def test_deduplicacao_do_remember_enxerga_fato_salvo_no_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Um fato gravado no namespace do workspace entra na deduplicação do
    Remember quando o gatilho recebe o mesmo workspace; sem o escopo a
    proposta repetida seria aceita de novo."""
    from backend.services import agent_factory
    from backend.testing.mocks import NativeInMemoryStore
    from backend.tools.memory import _memory_namespace, list_fact_contents

    store = NativeInMemoryStore()

    async def _store() -> NativeInMemoryStore:
        return store

    monkeypatch.setattr(agent_factory, "get_store", _store)
    ctx = ToolContext(user_id="alice", workspace_id="ws1", store=store)
    await store.aput(_memory_namespace(ctx), "fato", {"content": "usa pytest"})

    assert await list_fact_contents("alice", workspace_id="ws1") == ["usa pytest"]
    assert await list_fact_contents("alice") == []
