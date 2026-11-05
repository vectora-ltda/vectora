"""Testes unitários para Subagent DAG e permission intersection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import BaseModel

from backend.engine.subagents import (
    LivenessConfig,
    SubagentSpec,
    request_hard_interrupt,
    run_subagent,
    subagent_capability_token,
)
from backend.persistence.native.session_store import SessionStore
from backend.services.policy_compiler import PolicySnapshot
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolExtras, ToolSpec
from backend.vtypes.ids import CapabilityToken, CorrelationId
from backend.vtypes.message import VMessage, VMessageChunk


class _SimpleChatClient:
    def __init__(self, responses: list[str]) -> None:
        """Inicializa o estado da instância."""
        self._responses = list(responses)

    async def astream(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[VMessageChunk]:
        """Executa a operação astream."""
        resp = self._responses.pop(0) if self._responses else "ok"
        yield VMessageChunk(delta_text=resp)

    async def agenerate(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> VMessage:
        """Executa a operação agenerate."""
        raise NotImplementedError("não usado")


class _DummyArgs(BaseModel):
    text: str = ""


async def _dummy_handler(args: dict[str, object], ctx: ToolContext) -> str:
    """Executa a operação  dummy handler."""
    return str(args.get("text", "ok"))


def _make_tool_spec(
    name: str, category: str = "generic", *, destructive: bool = False
) -> ToolSpec:
    """Executa a operação  make tool spec."""
    return ToolSpec(
        name=name,
        description=f"tool {name}",
        args_model=_DummyArgs,
        handler=_dummy_handler,
        extras=ToolExtras(category=category, destructive=destructive),
        needs_ctx=True,
    )


@pytest.fixture
async def session_store(tmp_path: Path) -> AsyncIterator[SessionStore]:
    """Executa a operação session store."""
    pool = AsyncConnectionPool(
        str(tmp_path / "subagent_dag.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    await store.create_session("thread-parent", user_id="user1")
    try:
        yield store
    finally:
        await pool.close()


@pytest.fixture
def ctx() -> ToolContext:
    """Executa a operação ctx."""
    return ToolContext(
        user_id="user1",
        thread_id="thread-parent",
        run_id="run-parent-99",
        correlation_id="corr-intent-42",
        permission_mode="ask",
        tool_call_id="call-parent-1",
    )


@pytest.mark.asyncio
async def test_subagent_run_creates_child_agent_run(
    session_store: SessionStore, ctx: ToolContext
) -> None:
    """Verifica o comportamento de subagent run creates child agent run."""
    spec = SubagentSpec(
        name="worker",
        description="worker subagent",
        system_prompt="you are worker",
        tools=[_make_tool_spec("safe_echo", category="safe")],
        correlation_id=CorrelationId("corr-child-101"),
    )

    client = _SimpleChatClient(["Resultado do subagente"])

    result = await run_subagent(
        spec,
        "execute algo",
        session_store=session_store,
        chat_client=client,
        ctx=ctx,
        parent_thread_id="thread-parent",
        should_require_approval=None,
    )

    assert result == "Resultado do subagente"

    # Verifica se a execução filha foi registrada no session_store com parent_run_id e correlation_id
    async with session_store._pool.acquire() as conn:
        cur = await conn.execute(
            "SELECT run_id, parent_run_id, correlation_id FROM agent_runs WHERE parent_run_id = ?",
            ("run-parent-99",),
        )
        rows = list(await cur.fetchall())
        assert len(rows) == 1
        assert rows[0][1] == "run-parent-99"
        assert rows[0][2] == "corr-child-101"
    async with session_store._pool.acquire() as conn:
        cur = await conn.execute(
            "SELECT run_id FROM agent_runs WHERE parent_run_id = ?",
            ("run-parent-99",),
        )
        child_row = await cur.fetchone()
        assert child_row is not None
        child = await session_store.get_agent_run(child_row[0])
    assert child is not None
    assert child.status in {"complete", "passed"}


@pytest.mark.asyncio
async def test_subagent_permission_intersection_blocks_disabled_tool(
    session_store: SessionStore, ctx: ToolContext
) -> None:
    """Verifica o comportamento de subagent permission intersection blocks disabled tool."""
    spec = SubagentSpec(
        name="deleter",
        description="tries to delete",
        system_prompt="delete files",
        tools=[_make_tool_spec("custom_delete", category="disk")],
    )

    parent_policy = PolicySnapshot(
        user_id="user1",
        disabled_tools=frozenset({"custom_delete"}),
    )

    client = _SimpleChatClient(["ok"])

    res = await run_subagent(
        spec,
        "delete /tmp",
        session_store=session_store,
        chat_client=client,
        ctx=ctx,
        parent_thread_id="thread-parent",
        should_require_approval=None,
        parent_policy_snapshot=parent_policy,
    )

    assert (
        "Error: subagente 'deleter' pede tool(s) fora do escopo RBAC ou bloqueadas"
        in res
    )
    assert "custom_delete" in res


@pytest.mark.asyncio
async def test_subagent_reuses_parent_policy_snapshot(
    session_store: SessionStore,
    ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tarefa filha não pode recompilar um snapshot diferente do pai."""
    seen: list[PolicySnapshot | None] = []

    async def fake_run_conversation(*_args: object, **kwargs: object) -> object:
        seen.append(cast("PolicySnapshot | None", kwargs.get("policy_snapshot")))
        return SimpleNamespace(
            final_message=SimpleNamespace(text=lambda: "ok"),
            stopped_reason="stop",
        )

    monkeypatch.setattr(
        "backend.engine.subagents.run_conversation", fake_run_conversation
    )
    parent_policy = PolicySnapshot(
        user_id="user1",
        workspace_id="",
        disabled_tools=frozenset({"parent_disabled"}),
    )

    await run_subagent(
        SubagentSpec(
            name="worker",
            description="worker",
            system_prompt="work",
            tools=[_make_tool_spec("safe_echo", category="safe")],
        ),
        "execute",
        session_store=session_store,
        chat_client=_SimpleChatClient([]),
        ctx=ctx,
        parent_thread_id="thread-parent",
        should_require_approval=None,
        parent_policy_snapshot=parent_policy,
    )

    assert len(seen) == 1
    child_policy = seen[0]
    assert child_policy is not None
    assert child_policy.user_id == parent_policy.user_id
    assert child_policy.workspace_id == parent_policy.workspace_id
    assert child_policy.version == parent_policy.version


@pytest.mark.asyncio
async def test_subagent_rejects_parent_policy_from_other_identity(
    session_store: SessionStore, ctx: ToolContext
) -> None:
    """Um snapshot de outro usuário não pode atravessar a fronteira de escopo."""
    with pytest.raises(ValueError, match="não pertence"):
        await run_subagent(
            SubagentSpec(
                name="worker",
                description="worker",
                system_prompt="work",
                tools=[_make_tool_spec("safe_echo", category="safe")],
            ),
            "execute",
            session_store=session_store,
            chat_client=_SimpleChatClient([]),
            ctx=ctx,
            parent_thread_id="thread-parent",
            should_require_approval=None,
            parent_policy_snapshot=PolicySnapshot(
                user_id="other-user", workspace_id=None
            ),
        )


@pytest.mark.asyncio
async def test_subagent_untrusted_content_blocks_destructive_tool(
    session_store: SessionStore, ctx: ToolContext
) -> None:
    """Verifica o comportamento de subagent untrusted content blocks destructive tool."""
    tool = _make_tool_spec("fake_terminal", category="terminal", destructive=True)
    TOOL_REGISTRY.register(tool)
    try:
        spec = SubagentSpec(
            name="terminal_worker",
            description="runs terminal",
            system_prompt="run fake terminal",
            tools=[tool],
        )

        parent_policy = PolicySnapshot(
            user_id="user1",
            untrusted_content=True,
        )

        client = _SimpleChatClient(["ok"])

        res = await run_subagent(
            spec,
            "run bash ls",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-parent",
            should_require_approval=None,
            parent_policy_snapshot=parent_policy,
        )

        assert (
            "Error: subagente 'terminal_worker' pede tool(s) fora do escopo RBAC ou bloqueadas"
            in res
        )
        assert "fake_terminal" in res
    finally:
        TOOL_REGISTRY._tools.pop("fake_terminal", None)


@pytest.mark.asyncio
async def test_subagent_liveness_marks_child_cancelled(
    session_store: SessionStore, ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Watchdog cancela a conversa e persiste o estado observável."""
    started = asyncio.Event()

    async def stalled(*_args, **_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr("backend.engine.subagents.run_conversation", stalled)
    spec = SubagentSpec(
        name="stalled",
        description="stalled",
        system_prompt="wait",
        tools=[],
    )
    task = asyncio.create_task(
        run_subagent(
            spec,
            "wait",
            session_store=session_store,
            chat_client=_SimpleChatClient([]),
            ctx=ctx,
            parent_thread_id="thread-parent",
            should_require_approval=None,
            liveness=LivenessConfig(
                heartbeat_interval_s=0.001, max_stalled_heartbeats=1
            ),
        )
    )
    await started.wait()
    result = await task
    assert "cancelado por inatividade" in result
    async with session_store._pool.acquire() as conn:
        cur = await conn.execute(
            "SELECT run_id, parent_run_id, correlation_id, status "
            "FROM agent_runs WHERE parent_run_id = ?",
            (ctx.run_id,),
        )
        rows = list(await cur.fetchall())
        assert len(rows) == 1
        assert rows[0][1] == ctx.run_id
        assert rows[0][3] == "cancelled"


@pytest.mark.asyncio
async def test_subagent_hard_interrupt_marks_child_cancelled(
    session_store: SessionStore,
    ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interrupção autorizada cancela a task e persiste cancelled."""
    started = asyncio.Event()

    async def stalled(*_args, **_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr("backend.engine.subagents.run_conversation", stalled)
    correlation = CorrelationId("corr-hard-interrupt")
    spec = SubagentSpec(
        name="interruptible",
        description="interruptible",
        system_prompt="wait",
        tools=[],
        correlation_id=correlation,
    )
    task = asyncio.create_task(
        run_subagent(
            spec,
            "wait",
            session_store=session_store,
            chat_client=_SimpleChatClient([]),
            ctx=ctx,
            parent_thread_id="thread-parent",
            should_require_approval=None,
        )
    )
    await started.wait()
    assert not request_hard_interrupt(correlation, CapabilityToken("invalid-token"))
    assert request_hard_interrupt(correlation, subagent_capability_token(correlation))
    result = await task
    assert "hard interrupt" in result
    async with session_store._pool.acquire() as conn:
        cur = await conn.execute(
            "SELECT run_id FROM agent_runs "
            "WHERE parent_run_id = ? AND correlation_id = ? "
            "AND status = 'cancelled'",
            (ctx.run_id, str(correlation)),
        )
        rows = list(await cur.fetchall())
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_subagent_exception_marks_child_failed_and_propagates(
    session_store: SessionStore,
    ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha inesperada do loop fica persistida e não é engolida."""

    async def failing(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("backend.engine.subagents.run_conversation", failing)
    spec = SubagentSpec(
        name="failing",
        description="failing",
        system_prompt="fail",
        tools=[],
    )
    with pytest.raises(RuntimeError, match="boom"):
        await run_subagent(
            spec,
            "fail",
            session_store=session_store,
            chat_client=_SimpleChatClient([]),
            ctx=ctx,
            parent_thread_id="thread-parent",
            should_require_approval=None,
        )
    async with session_store._pool.acquire() as conn:
        cur = await conn.execute(
            "SELECT run_id, parent_run_id, correlation_id, status "
            "FROM agent_runs WHERE parent_run_id = ?",
            (ctx.run_id,),
        )
        rows = list(await cur.fetchall())
        assert len(rows) == 1
        assert rows[0][1] == ctx.run_id
        assert rows[0][3] == "failed"
