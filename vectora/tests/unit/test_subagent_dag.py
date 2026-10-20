"""Testes unitários para Subagent DAG e permission intersection."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.engine.subagents import SubagentSpec, run_subagent
from backend.persistence.native.session_store import SessionStore
from backend.services.policy_compiler import PolicySnapshot
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, ToolSpec
from backend.vtypes.ids import CorrelationId
from backend.vtypes.message import VMessageChunk


class _SimpleChatClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        resp = self._responses.pop(0) if self._responses else "ok"
        yield VMessageChunk(delta_text=resp)

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        raise NotImplementedError("não usado")


class _DummyArgs(BaseModel):
    text: str = ""


async def _dummy_handler(args: dict, ctx: ToolContext) -> str:
    return args.get("text", "ok")


def _make_tool_spec(name: str, category: str = "generic") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"tool {name}",
        args_model=_DummyArgs,
        handler=_dummy_handler,
        extras=ToolExtras(category=category),
        needs_ctx=True,
    )


@pytest.fixture
async def session_store(tmp_path):
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
    return ToolContext(
        user_id="user1",
        thread_id="thread-parent",
        run_id="run-parent-99",
        correlation_id="corr-intent-42",
        permission_mode="ask",
        tool_call_id="call-parent-1",
    )


@pytest.mark.asyncio
async def test_subagent_run_creates_child_agent_run(session_store, ctx):
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
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "run-parent-99"
        assert rows[0][2] == "corr-child-101"


@pytest.mark.asyncio
async def test_subagent_permission_intersection_blocks_disabled_tool(
    session_store, ctx
):
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
async def test_subagent_untrusted_content_blocks_destructive_tool(session_store, ctx):
    spec = SubagentSpec(
        name="terminal_worker",
        description="runs terminal",
        system_prompt="run bash",
        tools=[_make_tool_spec("bash", category="terminal")],
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
    assert "bash" in res
