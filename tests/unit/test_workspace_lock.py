"""Testes para o mutex de workspace (`src/services/workspace.py` — A.2).

Cobre aquisição/liberação, isolamento por chave `(workspace_id, thread_id)`,
timeout (`WorkspaceLockTimeoutError`) e a checagem não-bloqueante `is_workspace_locked`.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.workspace.workspace import (
    WorkspaceLockTimeoutError,
    acquire_workspace_lock,
)


@pytest.mark.asyncio
async def test_serializes_concurrent_access_to_same_key():
    wsid, tid = "ws-serialize", "thread-1"
    order: list[str] = []

    async def task(name: str, hold: float) -> None:
        async with acquire_workspace_lock(wsid, tid):
            order.append(f"{name}-start")
            await asyncio.sleep(hold)
            order.append(f"{name}-end")

    await asyncio.gather(task("a", 0.05), task("b", 0.0))

    # Sem o mutex, "b-start" apareceria antes de "a-end" (execução intercalada).
    assert order == ["a-start", "a-end", "b-start", "b-end"]


@pytest.mark.asyncio
async def test_independent_keys_do_not_block_each_other():
    order: list[str] = []

    async def task(wsid: str, name: str, hold: float) -> None:
        async with acquire_workspace_lock(wsid, "thread-1"):
            order.append(f"{name}-start")
            await asyncio.sleep(hold)
            order.append(f"{name}-end")

    await asyncio.gather(task("ws-x", "x", 0.05), task("ws-y", "y", 0.0))

    # Chaves diferentes não competem — "y" termina antes de "x" liberar.
    assert order[0] == "x-start"
    assert order.index("y-start") < order.index("x-end")


@pytest.mark.asyncio
async def test_raises_timeout_when_lock_held_too_long():
    wsid, tid = "ws-timeout", "thread-1"

    async with acquire_workspace_lock(wsid, tid):
        with pytest.raises(WorkspaceLockTimeoutError) as exc_info:
            async with acquire_workspace_lock(wsid, tid, timeout=0.05):
                pass

    assert exc_info.value.workspace_id == wsid
    assert exc_info.value.thread_id == tid
    assert exc_info.value.timeout == 0.05
