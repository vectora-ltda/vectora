"""Testes de isolamento das rotas REST de sessões PTY."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from backend.api.handlers.terminal import CloseBody, close_terminal, list_terminals
from backend.services.pty_registry import pty_registry
from backend.services.pty_session import PtySession


def _request(user_id: str) -> Request:
    return cast(
        "Request",
        SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=user_id))),
    )


def _session(
    terminal_id: str,
    *,
    user_id: str,
    thread_id: str,
    workspace_id: str,
) -> PtySession:
    return cast(
        "PtySession",
        SimpleNamespace(
            terminal_id=terminal_id,
            user_id=user_id,
            thread_id=thread_id,
            workspace_id=workspace_id,
            is_alive=lambda: True,
            close=lambda: None,
        ),
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    pty_registry._sessions.clear()
    pty_registry._closed.clear()
    yield
    pty_registry._sessions.clear()
    pty_registry._closed.clear()


@pytest.mark.asyncio
async def test_list_route_filters_user_workspace_and_thread() -> None:
    pty_registry.add(
        _session(
            "owned",
            user_id="alice",
            thread_id="thread-1",
            workspace_id="workspace-1",
        )
    )
    pty_registry.add(
        _session(
            "other-user",
            user_id="bob",
            thread_id="thread-1",
            workspace_id="workspace-1",
        )
    )
    pty_registry.add(
        _session(
            "other-thread",
            user_id="alice",
            thread_id="thread-2",
            workspace_id="workspace-1",
        )
    )
    pty_registry.add(
        _session(
            "other-workspace",
            user_id="alice",
            thread_id="thread-1",
            workspace_id="workspace-2",
        )
    )

    result = await list_terminals(
        _request("alice"), thread_id="thread-1", workspace_id="workspace-1"
    )

    assert [item["terminal_id"] for item in result["terminals"]] == ["owned"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "thread_id", "workspace_id"),
    [
        ("bob", "thread-1", "workspace-1"),
        ("alice", "thread-2", "workspace-1"),
        ("alice", "thread-1", "workspace-2"),
    ],
)
async def test_close_route_rejects_context_mismatch(
    user_id: str, thread_id: str, workspace_id: str
) -> None:
    pty_registry.add(
        _session(
            "owned",
            user_id="alice",
            thread_id="thread-1",
            workspace_id="workspace-1",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await close_terminal(
            CloseBody(
                terminal_id="owned",
                thread_id=thread_id,
                workspace_id=workspace_id,
            ),
            _request(user_id),
        )

    assert exc_info.value.status_code == 404
    assert pty_registry.get("owned") is not None


@pytest.mark.asyncio
async def test_close_route_closes_only_matching_context() -> None:
    pty_registry.add(
        _session(
            "owned",
            user_id="alice",
            thread_id="thread-1",
            workspace_id="workspace-1",
        )
    )

    result = await close_terminal(
        CloseBody(
            terminal_id="owned",
            thread_id="thread-1",
            workspace_id="workspace-1",
        ),
        _request("alice"),
    )

    assert result == {"status": "closed", "terminal_id": "owned"}
    assert pty_registry.get("owned") is None
