"""Regressões do snapshot único usado pelo histórico nativo."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services import agent_factory
from backend.vtypes.message import ContentBlock, MessageRole


class _SnapshotMessage:
    def __init__(self, text: str, *, is_error: bool | None) -> None:
        """Create a minimal message double for history normalization tests."""
        self.role = MessageRole.ASSISTANT
        self.content = [ContentBlock(kind="text", text=text)]
        self.is_error = is_error

    def text(self) -> str:
        """Expose the message text through the provider-shaped interface."""
        return "".join(block.text or "" for block in self.content)


def _message(text: str, *, is_error: bool | None) -> _SnapshotMessage:
    """Build a message object accepted by the factory under test."""
    return _SnapshotMessage(text, is_error=is_error)


@pytest.mark.asyncio
async def test_history_with_files_reuses_snapshot_for_messages_and_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flags permanecem associadas ao message_id da mesma leitura do store."""

    snapshot = [
        (10, _message("falhou", is_error=True)),
        (11, _message("passou", is_error=False)),
    ]
    store = type(
        "Store",
        (),
        {
            "get_edited_files_for_thread": AsyncMock(return_value={}),
            "get_history_with_ids": AsyncMock(return_value=snapshot),
        },
    )()
    monkeypatch.setattr(
        agent_factory, "get_session_store", AsyncMock(return_value=store)
    )
    legacy_read = AsyncMock(side_effect=AssertionError("snapshot duplicado"))
    monkeypatch.setattr(agent_factory, "aget_thread_messages", legacy_read)

    entries = await agent_factory.aget_thread_messages_with_files("thread-1")

    assert [(entry.message_id, entry.is_error) for entry in entries] == [
        (10, True),
        (11, False),
    ]
    store.get_history_with_ids.assert_awaited_once_with("thread-1")
    legacy_read.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_with_files_preserva_none_e_permite_retentativa_apos_falha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha transitória não transforma flag desconhecida em ``False``."""

    snapshot = [(10, _message("incerto", is_error=None))]
    get_snapshot = AsyncMock(side_effect=[OSError("store indisponível"), snapshot])
    store = type(
        "Store",
        (),
        {
            "get_edited_files_for_thread": AsyncMock(return_value={}),
            "get_history_with_ids": get_snapshot,
        },
    )()
    monkeypatch.setattr(
        agent_factory, "get_session_store", AsyncMock(return_value=store)
    )

    with pytest.raises(RuntimeError, match="histórico persistido"):
        await agent_factory.aget_thread_messages_with_files("thread-1")

    entries = await agent_factory.aget_thread_messages_with_files("thread-1")

    assert len(entries) == 1
    assert entries[0].message_id == 10
    assert entries[0].is_error is None
    assert get_snapshot.await_count == 2


@pytest.mark.asyncio
async def test_history_with_files_preserva_snapshot_vazio_sem_leitura_adicional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Um snapshot vazio não dispara uma segunda leitura concorrente."""

    store = type(
        "Store",
        (),
        {
            "get_edited_files_for_thread": AsyncMock(return_value={}),
            "get_history_with_ids": AsyncMock(return_value=[]),
        },
    )()
    monkeypatch.setattr(
        agent_factory, "get_session_store", AsyncMock(return_value=store)
    )
    legacy_read = AsyncMock(side_effect=AssertionError("leitura legada duplicada"))
    monkeypatch.setattr(agent_factory, "aget_thread_messages", legacy_read)

    assert await agent_factory.aget_thread_messages_with_files("thread-empty") == []
    store.get_history_with_ids.assert_awaited_once_with("thread-empty")
    legacy_read.assert_not_awaited()
