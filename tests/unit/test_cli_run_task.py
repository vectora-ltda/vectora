"""Testes para backend/cli/run_task.py.

``vectora run`` é o modo one-shot (CI, Vectora Bot for GHA): sobe o motor
nativo, roda `run_conversation` uma vez com a tarefa dada e sai — mesmo
caminho de `backend/services/connect/runner.py::run_agent_for_thread`
(mesmo `permission_mode="auto"`), mas sem lookup de thread por plataforma:
cada execução usa um thread_id novo, descartável.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from backend.cli import run_task
from backend.engine import conversation_loop
from backend.engine.hitl import ApprovalGate
from backend.llm import fallback_chat_client
from backend.persistence.native.session_store import SessionStore
from backend.services import agent_factory
from backend.services.agent_factory import NativeAgent
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import VMessageChunk


@pytest.fixture
async def native_session_store(tmp_path):
    pool = AsyncConnectionPool(
        str(tmp_path / "cli-run-sessions.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


def _texto_chunk(texto: str) -> VMessageChunk:
    return VMessageChunk(delta_text=texto)


class _ScriptedChatClient:
    """Mesmo fake de test_connect_runner.py — cada `astream` consome o
    próximo turno pré-roteirizado."""

    def __init__(
        self,
        turnos: list[list[VMessageChunk]] | None = None,
        *,
        exc: Exception | None = None,
    ) -> None:
        self._turnos = turnos or []
        self.chamadas = 0
        self.model_ids: list[str] = []
        self.aclose_calls: list[bool] = []
        self._exc = exc

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        self.chamadas += 1
        if self._exc is not None:
            raise self._exc
        turno = self._turnos[self.chamadas - 1]
        for chunk in turno:
            yield chunk

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        msg = "não usado (astream-only)"
        raise NotImplementedError(msg)


def _patch_native_engine(
    monkeypatch,
    *,
    session_store: SessionStore,
    tool_registry: ToolRegistry | None = None,
    chat_client: Any = None,
) -> _ScriptedChatClient:
    native_agent = NativeAgent(
        tool_registry=tool_registry or ToolRegistry(),
        subagent_catalog={},
        system_prompt="system prompt de teste",
    )
    client = chat_client or _ScriptedChatClient([[_texto_chunk("ok")]])

    async def _fake_get_native_agent(
        user_id: str | None = None,
        chat_mode: bool = False,
        workspace_id: str | None = None,
    ) -> NativeAgent:
        return native_agent

    async def _fake_get_session_store() -> SessionStore:
        return session_store

    approval_gate = ApprovalGate(session_store)

    async def _fake_get_approval_gate() -> ApprovalGate:
        return approval_gate

    async def _fake_get_store() -> None:
        return None

    async def _fake_aclose() -> None:
        client.aclose_calls.append(True)

    monkeypatch.setattr(agent_factory, "get_native_agent", _fake_get_native_agent)
    monkeypatch.setattr(agent_factory, "get_session_store", _fake_get_session_store)
    monkeypatch.setattr(agent_factory, "get_approval_gate", _fake_get_approval_gate)
    monkeypatch.setattr(agent_factory, "get_store", _fake_get_store)
    monkeypatch.setattr(agent_factory, "aclose", _fake_aclose)

    def _fake_chat_client_ctor(primary_model_id: str = "") -> _ScriptedChatClient:
        client.model_ids.append(primary_model_id)
        return client

    monkeypatch.setattr(
        fallback_chat_client, "FallbackChatClient", _fake_chat_client_ctor
    )
    return client


class TestRunTask:
    async def test_roda_o_turno_e_devolve_o_texto_final(
        self, native_session_store, monkeypatch
    ):
        client = _ScriptedChatClient([[_texto_chunk("Revisão: tudo certo.")]])
        _patch_native_engine(
            monkeypatch, session_store=native_session_store, chat_client=client
        )

        exit_code = await run_task._run_task(
            "revise este PR", model_id="google_genai:gemini-flash"
        )

        assert exit_code == 0
        assert client.chamadas == 1
        assert client.model_ids == ["google_genai:gemini-flash"]

    async def test_pendencia_nativa_e_devolvida_como_exit_1(
        self, native_session_store, monkeypatch
    ):
        """`stopped_reason != "stop"` (ex.: max_iterations) vira exit code
        1 — sinaliza pro chamador (a Action) que o turno não terminou
        limpo, sem precisar inspecionar o texto da resposta."""
        from backend.engine.conversation_loop import LoopResult

        async def _fake_run_conversation(**kwargs):
            return LoopResult(stopped_reason="max_iterations", final_message=None)

        _patch_native_engine(monkeypatch, session_store=native_session_store)
        monkeypatch.setattr(
            conversation_loop, "run_conversation", _fake_run_conversation
        )

        exit_code = await run_task._run_task(
            "tarefa longa demais", model_id="google_genai:gemini-flash"
        )

        assert exit_code == 1

    async def test_sem_pendencia_nativa_devolve_exit_0_com_texto_vazio(
        self, native_session_store, monkeypatch, capsys
    ):
        """Erro/borda: `final_message=None` (loop parou sem produzir texto)
        não quebra — imprime string vazia, não `None`."""
        from backend.engine.conversation_loop import LoopResult

        async def _fake_run_conversation(**kwargs):
            return LoopResult(stopped_reason="stop", final_message=None)

        _patch_native_engine(monkeypatch, session_store=native_session_store)
        monkeypatch.setattr(
            conversation_loop, "run_conversation", _fake_run_conversation
        )

        exit_code = await run_task._run_task(
            "tarefa", model_id="google_genai:gemini-flash"
        )

        assert exit_code == 0
        assert capsys.readouterr().out == "\n"

    async def test_falha_no_chat_client_fecha_agent_factory_mesmo_assim(
        self, native_session_store, monkeypatch
    ):
        """`agent_factory.aclose()` roda mesmo quando `run_conversation`
        lança — `finally`, não best-effort só no caminho feliz."""
        client = _ScriptedChatClient(exc=RuntimeError("provider fora do ar"))
        _patch_native_engine(
            monkeypatch, session_store=native_session_store, chat_client=client
        )

        with pytest.raises(RuntimeError, match="provider fora do ar"):
            await run_task._run_task("tarefa", model_id="google_genai:gemini-flash")

        assert client.aclose_calls == [True]


class TestReadTask:
    def test_task_do_argumento_tem_prioridade_sobre_stdin(self):
        args = argparse.Namespace(task="tarefa via --task")
        assert run_task._read_task(args) == "tarefa via --task"


class TestRunRunTask:
    def test_sem_modelo_sai_com_erro_claro_sem_chamar_o_motor(
        self, monkeypatch, capsys
    ):
        """Erro/borda: sem --model nem VECTORA_MODEL, sai com exit 1 e
        mensagem clara ANTES de sequer tentar montar o motor — não deixa
        o erro estourar de dentro de fallback_chat_client.py."""
        monkeypatch.delenv("VECTORA_MODEL", raising=False)
        args = argparse.Namespace(task="diga oi", model=None)

        with pytest.raises(SystemExit) as exc_info:
            run_task.run_run_task(args)

        assert exc_info.value.code == 1
        assert "modelo" in capsys.readouterr().err.lower()

    def test_sem_tarefa_sai_com_erro_claro(self, monkeypatch, capsys):
        monkeypatch.setattr(run_task.sys.stdin, "isatty", lambda: True)
        args = argparse.Namespace(task=None, model="google_genai:gemini-flash")

        with pytest.raises(SystemExit) as exc_info:
            run_task.run_run_task(args)

        assert exc_info.value.code == 1
