"""Regressões de persistência e concorrência lógica do MemoryStore."""

from __future__ import annotations

import pytest

from backend.services.memory_store import MemoryConflictError, MemoryStore


def _store_record(store: MemoryStore, *, content: str = "fato") -> None:
    store.store(
        "shared-key",
        "project",
        "workspace-a",
        content,
        provenance="run-1",
    )


def test_persisted_same_key_is_idempotent_across_store_instances(tmp_path) -> None:
    """Reexecução em outro processo lógico não duplica nem falha."""
    path = tmp_path / "memory.db"
    first = MemoryStore(path)
    second = MemoryStore(path)

    _store_record(first)
    _store_record(second)

    assert second.metrics()["idempotent_writes"] == 1
    assert [item.id for item in second.retrieve(scope_id="workspace-a").records] == [
        "shared-key"
    ]


def test_persisted_same_key_with_different_payload_is_conflict(tmp_path) -> None:
    """Uma gravação concorrente divergente não sobrescreve a proveniência."""
    path = tmp_path / "memory.db"
    first = MemoryStore(path)
    second = MemoryStore(path)

    _store_record(first)
    with pytest.raises(MemoryConflictError):
        _store_record(second, content="conteúdo divergente")

    assert second.metrics()["conflicts"] == 1
    result = first.retrieve(scope_id="workspace-a")
    assert result.records[0].content == "fato"


def test_expire_then_purge_invalidated_respects_retention_and_batch(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.store(
        "old",
        "project",
        "workspace-a",
        "old memory",
        valid_until="2020-01-01T00:00:00+00:00",
    )
    store.store("current", "project", "workspace-a", "current memory")

    assert store.expire(before="2021-01-01T00:00:00+00:00") == 1
    assert (
        store.purge_invalidated(before="2021-01-01T00:00:00+00:00", batch_size=1) == 1
    )
    assert [item.id for item in store.retrieve(scope_id="workspace-a").records] == [
        "current"
    ]


def test_purge_invalidated_rejeita_batch_invalido(tmp_path) -> None:
    with pytest.raises(ValueError, match="batch_size deve ser positivo"):
        MemoryStore(tmp_path / "memory.db").purge_invalidated(batch_size=0)


def test_memory_authorization_isolates_scopes_and_global_records() -> None:
    store = MemoryStore()
    store.store("a", "project", "workspace-a", "segredo a")
    store.store("b", "project", "workspace-b", "segredo b")
    store.store("g", "project", "global", "global")

    result = store.retrieve(
        scope_id="workspace-a", authorized_scope_ids={"workspace-a"}
    )
    assert {item.id for item in result.records} == {"a"}

    with pytest.raises(PermissionError, match="não autorizado"):
        store.retrieve(scope_id="workspace-b", authorized_scope_ids={"workspace-a"})

    assert {item.id for item in result.records} == {"a"}
    assert {
        item.id
        for item in store.retrieve(
            scope_id="workspace-a", authorized_scope_ids={"workspace-a", "global"}
        ).records
    } == {"a", "g"}


def test_memory_authorization_rejects_unauthorized_write() -> None:
    with pytest.raises(PermissionError, match="não autorizado"):
        MemoryStore().store(
            "forbidden",
            "project",
            "workspace-b",
            "segredo",
            authorized_scope_ids={"workspace-a"},
        )
