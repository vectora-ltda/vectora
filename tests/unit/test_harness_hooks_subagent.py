"""Hooks do harness valem também para as tools que um subagent executa."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import create_model

from backend.engine import hooks
from backend.engine.subagents import SubagentSpec, run_subagent
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, ToolSpec
from backend.vtypes.message import ToolCallChunk, VMessageChunk
from backend.workspace.workspace_config import (
    hook_rules_digest,
    load_workspace_config,
)

PY = json.dumps(sys.executable)


class _ScriptedChatClient:
    def __init__(self, turnos: list[list[VMessageChunk]]) -> None:
        self._turnos = turnos
        self.chamadas = 0

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        turno = self._turnos[self.chamadas]
        self.chamadas += 1
        for chunk in turno:
            yield chunk

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        msg = "não usado (astream-only)"
        raise NotImplementedError(msg)


def _chamada_da_tool() -> list[VMessageChunk]:
    return [
        VMessageChunk(
            tool_call_chunks=[
                ToolCallChunk(index=0, id="c1", name="sonda", args_fragment="{}")
            ]
        )
    ]


def _resposta_final() -> list[VMessageChunk]:
    return [VMessageChunk(delta_text="pronto")]


@pytest.fixture
async def session_store(tmp_path: Path):
    pool = AsyncConnectionPool(str(tmp_path / "sub.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    await store.create_session("thread-pai", user_id="alice")
    try:
        yield store
    finally:
        await pool.close()


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    hook = tmp_path / "hook.py"
    hook.write_text(script, encoding="utf-8")
    (tmp_path / "vectora.toml").write_text(
        "[[hooks.rules]]\n"
        'id = "guarda"\n'
        'event = "pre_tool"\n'
        f"command = [{PY}, {json.dumps(str(hook))}]\n",
        encoding="utf-8",
    )
    config = load_workspace_config(tmp_path)
    assert config is not None
    state = SimpleNamespace(
        cwd=str(tmp_path),
        hooks_approved=True,
        hooks_rules_digest=hook_rules_digest(config.hooks.rules),
        id="w1",
    )
    monkeypatch.setattr(hooks, "_workspace_for", lambda _ctx: state)


def _spec_com_sonda(executadas: list[bool]) -> SubagentSpec:
    async def handler() -> str:
        executadas.append(True)
        return "executou"

    sonda = ToolSpec(
        name="sonda",
        description="sonda",
        args_model=create_model("SondaArgs"),
        handler=handler,
        extras=ToolExtras(),
        needs_ctx=False,
    )
    return SubagentSpec(
        name="coder",
        description="agente de teste",
        system_prompt="você é um agente de teste",
        tools=[sonda],
    )


async def _delega(spec: SubagentSpec, store: SessionStore) -> str:
    client = _ScriptedChatClient([_chamada_da_tool(), _resposta_final()])
    ctx = ToolContext(
        user_id="alice",
        thread_id="thread-pai",
        permission_mode="ask",
        tool_call_id="call-1",
    )
    return await run_subagent(
        spec,
        "use a sonda",
        session_store=store,
        chat_client=client,
        ctx=ctx,
        parent_thread_id="thread-pai",
        should_require_approval=lambda *_a: False,
    )


async def test_hook_bloqueante_impede_a_tool_do_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_store: SessionStore
) -> None:
    _workspace(
        tmp_path,
        monkeypatch,
        'import json,sys; sys.stdin.read(); print(json.dumps({"decision":"deny","reason":"fora da política"}))',
    )
    executadas: list[bool] = []
    monkeypatch.setattr(
        "backend.engine.subagents._sub_thread_id", lambda *_a: "thread-pai-coder"
    )

    resultado = await _delega(_spec_com_sonda(executadas), session_store)

    assert executadas == []
    assert resultado == "pronto"
    historico = await session_store.get_history("thread-pai-coder")
    textos = " ".join(m.text() for m in historico)
    assert "hook_denied: guarda: fora da política" in textos


async def test_hook_que_permite_deixa_a_tool_do_subagent_rodar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_store: SessionStore
) -> None:
    _workspace(tmp_path, monkeypatch, "import sys; sys.stdin.read()")
    executadas: list[bool] = []

    await _delega(_spec_com_sonda(executadas), session_store)

    assert executadas == [True]
