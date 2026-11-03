"""Contrato de branches do armazenamento PostgreSQL."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from backend.persistence.native.agent_run import AgentRun
from backend.persistence.native.postgres_session_store import (
    PostgresSessionStore,
    _message_to_row,
    _row_to_message,
    _RunEvidenceLockRegistry,
)
from backend.vtypes.message import MessageRole, VMessage

if TYPE_CHECKING:
    import asyncpg


class _AsyncContext:
    def __init__(self, value):
        """Inicializa o estado da instância."""
        self.value = value

    async def __aenter__(self):
        """Executa a operação aenter."""
        return self.value

    async def __aexit__(self, *_args):
        """Executa a operação aexit."""
        return False


class _Connection:
    def __init__(self, fetchval_results: list[object] | None = None):
        """Inicializa o estado da instância."""
        self.fetchval_results = list(fetchval_results or [])
        self.fetchval = AsyncMock(side_effect=self.fetchval_results)
        self.fetch = AsyncMock()
        self.fetchrow = AsyncMock()
        self.execute = AsyncMock()

    def transaction(self):
        """Executa a operação transaction."""
        return _AsyncContext(None)


class _Pool:
    def __init__(self, connection: _Connection):
        """Inicializa o estado da instância."""
        self.connection = connection

    def acquire(self):
        """Executa a operação acquire."""
        return _AsyncContext(self.connection)


def _store(connection: _Connection) -> PostgresSessionStore:
    """Executa a operação store."""
    pool = cast("asyncpg.Pool", _Pool(connection))
    store = PostgresSessionStore(pool)
    store.setup = AsyncMock()  # type: ignore[method-assign]
    return store


@pytest.mark.parametrize(
    ("stored", "expected"), [(True, True), (False, False), (None, None)]
)
def test_postgres_message_round_trip_preserves_error_flag(
    stored: bool | None, expected: bool
) -> None:
    """A coluna PostgreSQL não pode rebaixar falhas persistidas para sucesso."""
    message = _row_to_message(
        {
            "role": MessageRole.ASSISTANT,
            "content_json": '[{"kind":"text","text":"falhou"}]',
            "tool_calls_json": None,
            "tool_call_id": None,
            "name": None,
            "is_error": stored,
        }
    )

    assert isinstance(message, VMessage)
    assert message.is_error is expected


@pytest.mark.parametrize("is_error", [None, False, True])
def test_postgres_message_write_preserves_error_tristate(is_error: bool | None) -> None:
    """The write boundary must not collapse an unknown flag into success."""
    message = VMessage(role=MessageRole.ASSISTANT, content=[], is_error=is_error)

    row = _message_to_row(message)

    assert row[-1] is is_error


@pytest.mark.asyncio
async def test_postgres_evidence_lock_keeps_entry_until_waiters_finish() -> None:
    """Um waiter não pode observar um lock removido e criar uma segunda fila."""
    registry = _RunEvidenceLockRegistry()
    entered = asyncio.Event()
    release = asyncio.Event()
    waiter_entered = asyncio.Event()

    async def first() -> None:
        async with registry.acquire("run-1"):
            entered.set()
            await release.wait()

    async def second() -> None:
        await entered.wait()
        async with registry.acquire("run-1"):
            waiter_entered.set()

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert len(registry) == 1
    assert not waiter_entered.is_set()

    release.set()
    await asyncio.gather(first_task, second_task)
    assert waiter_entered.is_set()
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_postgres_terminal_outcome_uses_one_transaction() -> None:
    """A terminal failure must update the run before committing its message."""
    connection = _Connection(fetchval_results=[None, 41, 0])
    store = _store(connection)
    message = VMessage(
        role=MessageRole.ASSISTANT,
        content=[],
        finish_reason="failed",
        is_error=True,
    )

    await store.append_terminal_outcome(
        "thread-1",
        message,
        parent_message_id=None,
        turn_id="run-1:terminal",
        run_id="run-1",
        status="failed",
        detail="provider indisponível",
    )

    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert any(
        "UPDATE vectora_native_agent_runs" in statement for statement in statements
    )
    assert any(
        "INSERT INTO vectora_native_thread_events" in statement
        for statement in statements
    )


@pytest.mark.asyncio
async def test_postgres_set_branch_head_serializa_thread_e_valida_ponta() -> None:
    """Verifica o comportamento de postgres set branch head serializa thread e valida ponta."""
    conn = _Connection([1, 1])
    store = _store(conn)

    await store.set_branch_head("t1", 9)

    first_query = conn.fetchval.await_args_list[0].args[0]
    assert "FOR UPDATE" in first_query
    assert conn.fetchval.await_args_list[1].args[-2:] == ("t1", 9)
    assert conn.execute.await_count == 3


@pytest.mark.asyncio
async def test_postgres_list_branch_heads_limita_e_inclui_contagem() -> None:
    """Verifica o comportamento de postgres list branch heads limita e inclui contagem."""
    conn = _Connection()
    conn.fetch.return_value = [
        {
            "id": 9,
            "created_at": "2026-01-01T00:00:00Z",
            "is_branch_head": True,
        }
    ]
    store = _store(conn)
    store.get_history_with_ids = AsyncMock(return_value=[(1, object()), (9, object())])  # type: ignore[method-assign]

    result = await store.list_branch_heads("t1", limit=999)

    assert result[0]["head_message_id"] == 9
    assert result[0]["message_count"] == 2
    fetch_call = conn.fetch.await_args
    assert fetch_call is not None
    assert fetch_call.args[-1] == 500


@pytest.mark.asyncio
async def test_postgres_compare_branches_separa_divergencias() -> None:
    """Verifica o comportamento de postgres compare branches separa divergencias."""
    conn = _Connection([1])
    store = _store(conn)
    store.get_history_with_ids = AsyncMock(
        side_effect=[
            [(1, object()), (2, object())],
            [(1, object()), (3, object())],
        ]
    )  # type: ignore[method-assign]
    store.get_branch_head_id = AsyncMock(return_value=3)  # type: ignore[method-assign]

    result = await store.compare_branches("t1", 2)

    assert result == {
        "active_head_message_id": 3,
        "selected_head_message_id": 2,
        "common_message_ids": [1],
        "active_divergent_message_ids": [3],
        "selected_divergent_message_ids": [2],
    }


@pytest.mark.asyncio
async def test_postgres_agent_run_upsert_uses_for_update_and_preserves_untrusted_content() -> (
    None
):
    """Verifica o comportamento de postgres agent run upsert uses for update and preserves untrusted content."""
    conn = _Connection()
    conn.fetchrow.return_value = {
        "evidence_json": json.dumps([{"kind": "provenance", "untrusted_content": True}])
    }
    store = _store(conn)

    run = AgentRun(
        run_id="run-pg-1",
        thread_id="t1",
        status="waiting_approval",
        untrusted_content=False,
    )
    await store.upsert_agent_run(run)

    fetchrow_call = conn.fetchrow.await_args
    assert fetchrow_call is not None
    assert "FOR UPDATE" in fetchrow_call.args[0]

    execute_call = conn.execute.await_args
    assert execute_call is not None
    evidence_saved = next(
        json.loads(value)
        for value in execute_call.args[1:]
        if isinstance(value, str) and value.startswith("[{")
    )
    assert any(
        isinstance(e, dict) and e.get("untrusted_content") is True
        for e in evidence_saved
    )


@pytest.mark.asyncio
async def test_postgres_get_and_list_agent_runs_derives_untrusted_content() -> None:
    """Verifica o comportamento de postgres get and list agent runs derives untrusted content."""
    conn = _Connection()
    sample_row = {
        "run_id": "run-pg-2",
        "thread_id": "t1",
        "turn_id": None,
        "user_id": "u1",
        "workspace_id": None,
        "model": "gpt-4",
        "provider": "openai",
        "status": "passed",
        "plan_step": None,
        "tools_available_json": "[]",
        "tools_used_json": "[]",
        "approvals_json": "[]",
        "files_touched_json": "[]",
        "commands_run_json": "[]",
        "tests_run_json": "[]",
        "artifacts_json": "[]",
        "metrics_json": "{}",
        "evidence_json": json.dumps(
            [{"kind": "provenance", "untrusted_content": True}]
        ),
        "detail": None,
        "error_code": None,
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "terminal_at": None,
    }
    conn.fetchrow.return_value = sample_row
    conn.fetch.return_value = [sample_row]
    store = _store(conn)

    saved = await store.get_agent_run("run-pg-2")
    assert saved is not None
    assert saved.untrusted_content is True

    listed = await store.list_agent_runs("t1")
    assert len(listed) == 1
    assert listed[0].untrusted_content is True
