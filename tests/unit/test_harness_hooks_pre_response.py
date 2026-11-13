"""Hook ``pre_response``: o loop pede revisão quando a resposta é negada."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.engine import hooks
from backend.engine.conversation_loop import LoopConfig, run_conversation
from backend.engine.stream_events import EngineEvent, ErrorSignal
from backend.persistence.native.agent_run import AgentRun
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import VMessageChunk
from backend.workspace.workspace_config import (
    hook_rules_digest,
    load_workspace_config,
)

PY = json.dumps(sys.executable)

DENY_SEM_REVISADO = (
    "import json, sys\n"
    "data = json.load(sys.stdin)\n"
    "ok = 'REVISADO' in data['response']\n"
    "print(json.dumps({'decision': 'allow' if ok else 'deny', "
    "'reason': 'falta o selo REVISADO'}))\n"
)


class _Client:
    def __init__(self, turnos: list[str]) -> None:
        self._turnos = turnos
        self.chamadas = 0

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        texto = self._turnos[min(self.chamadas, len(self._turnos) - 1)]
        self.chamadas += 1
        yield VMessageChunk(delta_text=texto)

    async def agenerate(self, *args: Any, **kwargs: Any):
        raise NotImplementedError


@pytest.fixture
async def session_store(tmp_path: Path):
    pool = AsyncConnectionPool(str(tmp_path / "loop.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    await store.create_session("thread-1", user_id="alice")
    try:
        yield store
    finally:
        await pool.close()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "gate.py"
    script.write_text(DENY_SEM_REVISADO, encoding="utf-8")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "vectora.toml").write_text(
        "[[hooks.rules]]\n"
        'id = "selo"\n'
        'event = "pre_response"\n'
        f"command = [{PY}, {json.dumps(str(script))}]\n",
        encoding="utf-8",
    )
    config = load_workspace_config(root)
    assert config is not None
    state = SimpleNamespace(
        cwd=str(root),
        id="w1",
        hooks_approved=True,
        hooks_rules_digest=hook_rules_digest(config.hooks.rules),
    )
    monkeypatch.setattr(hooks, "_workspace_for", lambda _ctx: state)
    return root


async def _run(session_store: SessionStore, client: _Client, run_id: str):
    await session_store.upsert_agent_run(
        AgentRun(run_id=run_id, thread_id="thread-1", status="running")
    )
    events: list[EngineEvent] = []

    async def on_event(event: EngineEvent) -> None:
        events.append(event)

    result = await run_conversation(
        session_store=session_store,
        chat_client=client,
        tool_registry=ToolRegistry(),
        ctx=ToolContext(user_id="alice", thread_id="thread-1"),
        thread_id="thread-1",
        config=LoopConfig(run_id=run_id),
        on_event=on_event,
    )
    return result, events


async def test_resposta_negada_volta_ao_modelo_e_a_revisada_passa(
    session_store: SessionStore, workspace: Path
) -> None:
    client = _Client(["rascunho", "REVISADO: resposta final"])
    result, events = await _run(session_store, client, "run-revisa")

    assert client.chamadas == 2
    assert result.stopped_reason == "stop"
    assert result.final_message is not None
    assert "REVISADO" in result.final_message.text()
    assert not [event for event in events if isinstance(event, ErrorSignal)]
    history = await session_store.get_history("thread-1")
    feedback = [m for m in history if "[hook pre_response selo]" in m.text()]
    assert len(feedback) == 1
    assert "falta o selo REVISADO" in feedback[0].text()
    stored = await session_store.get_agent_run("run-revisa")
    assert stored is not None
    outcomes = [
        item["outcome"] for item in stored.evidence if item.get("type") == "hook_run"
    ]
    assert outcomes == ["denied", "allowed"]


async def test_negacao_persistente_para_no_limite_e_avisa(
    session_store: SessionStore, workspace: Path
) -> None:
    client = _Client(["sempre sem o selo"])
    result, events = await _run(session_store, client, "run-limite")

    assert client.chamadas == 1 + hooks.MAX_RESPONSE_REVISIONS
    assert result.stopped_reason == "stop"
    codes = [e.code for e in events if isinstance(e, ErrorSignal)]
    assert codes == ["RESPONSE_HOOK_DENIED"]


async def test_sem_regra_pre_response_o_fluxo_nao_muda(
    session_store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hooks, "_workspace_for", lambda _ctx: None)
    client = _Client(["resposta qualquer"])
    result, _ = await _run(session_store, client, "run-livre")
    assert client.chamadas == 1
    assert result.stopped_reason == "stop"
