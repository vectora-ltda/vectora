"""Testes para backend/services/gateway/review_job.py — execução da
revisão de PR self-hosted (gh-bot), disparada por um `review_job` recebido
pelo túnel do gateway (ver backend/services/gateway/__init__.py::_dispatch).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from backend.engine import conversation_loop
from backend.engine.hitl import ApprovalGate
from backend.llm import fallback_chat_client
from backend.persistence.native.session_store import SessionStore
from backend.services import agent_factory
from backend.services.agent_factory import NativeAgent
from backend.services.gateway import review_job
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import MessageRole, VMessageChunk, text_message


@pytest.fixture
async def native_session_store(tmp_path: Path) -> AsyncIterator[SessionStore]:
    pool = AsyncConnectionPool(
        str(tmp_path / "review-job-sessions.db"), min_size=1, max_size=2
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
    """Mesmo fake de test_cli_run_task.py — cada `astream` consome o
    próximo turno pré-roteirizado."""

    def __init__(self, turnos: list[list[VMessageChunk]] | None = None) -> None:
        self._turnos = turnos or []
        self.chamadas = 0
        self.model_ids: list[str] = []

    async def astream(
        self,
        messages: list[Any],
        *,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[VMessageChunk]:
        self.chamadas += 1
        turno = self._turnos[self.chamadas - 1]
        for chunk in turno:
            yield chunk

    async def agenerate(
        self,
        messages: list[Any],
        *,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        msg = "não usado (astream-only)"
        raise NotImplementedError(msg)


def _patch_native_engine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_store: SessionStore,
    chat_client: Any = None,
) -> _ScriptedChatClient:
    native_agent = NativeAgent(
        tool_registry=ToolRegistry(), subagent_catalog={}, system_prompt="system"
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

    monkeypatch.setattr(agent_factory, "get_native_agent", _fake_get_native_agent)
    monkeypatch.setattr(agent_factory, "get_session_store", _fake_get_session_store)
    monkeypatch.setattr(agent_factory, "get_approval_gate", _fake_get_approval_gate)
    monkeypatch.setattr(agent_factory, "get_store", _fake_get_store)

    def _fake_chat_client_ctor(primary_model_id: str = "") -> _ScriptedChatClient:
        client.model_ids.append(primary_model_id)
        return client

    monkeypatch.setattr(
        fallback_chat_client, "FallbackChatClient", _fake_chat_client_ctor
    )
    return client


class TestBuildTask:
    def test_monta_o_prompt_com_diff_e_metadata(self) -> None:
        task = review_job._build_task(
            "diff --git a/a.py\n+print(1)", {"pr_number": "42", "repo": "x/y"}
        )
        assert "diff --git a/a.py" in task
        assert "pr_number: 42" in task
        assert "repo: x/y" in task

    def test_erro_borda_metadata_vazia_nao_quebra(self) -> None:
        task = review_job._build_task("diff x", {})
        assert "diff x" in task


class TestRunReviewJob:
    async def test_erro_borda_sem_vectora_model_levanta_erro_tipado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VECTORA_MODEL", raising=False)
        with pytest.raises(review_job.ReviewJobModelNotConfiguredError):
            await review_job.run_review_job("diff x", {})

    async def test_roda_a_revisao_e_devolve_o_texto_final(
        self, native_session_store: SessionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VECTORA_MODEL", "google_genai:gemini-flash")
        client = _ScriptedChatClient([[_texto_chunk("LGTM, só um nit.")]])
        _patch_native_engine(
            monkeypatch, session_store=native_session_store, chat_client=client
        )

        texto = await review_job.run_review_job(
            "diff --git a/a.py\n+print(1)", {"pr_number": "7"}
        )

        assert texto == "LGTM, só um nit."
        assert client.chamadas == 1
        assert client.model_ids == ["google_genai:gemini-flash"]

    async def test_erro_borda_sem_mensagem_final_devolve_string_vazia(
        self, native_session_store: SessionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VECTORA_MODEL", "google_genai:gemini-flash")
        from backend.engine.conversation_loop import LoopResult

        async def _fake_run_conversation(**kwargs: Any) -> LoopResult:
            return LoopResult(stopped_reason="stop", final_message=None)

        _patch_native_engine(monkeypatch, session_store=native_session_store)
        monkeypatch.setattr(
            conversation_loop, "run_conversation", _fake_run_conversation
        )

        texto = await review_job.run_review_job("diff x", {})

        assert texto == ""

    async def test_erro_de_execucao_se_propaga_pro_chamador(
        self, native_session_store: SessionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`run_review_job` não engole erro nenhum — quem decide como
        reportar (postar `error` em vez de `review_text`) é o chamador
        (`GatewayClient._handle_review_job`), não esta função."""
        monkeypatch.setenv("VECTORA_MODEL", "google_genai:gemini-flash")

        async def _fake_run_conversation(**kwargs: Any) -> None:
            msg = "provider indisponível"
            raise ConnectionError(msg)

        _patch_native_engine(monkeypatch, session_store=native_session_store)
        monkeypatch.setattr(
            conversation_loop, "run_conversation", _fake_run_conversation
        )

        with pytest.raises(ConnectionError, match="provider indisponível"):
            await review_job.run_review_job("diff x", {})

    async def test_roda_com_tool_registry_vazio_mesmo_quando_o_agente_tem_ferramentas(
        self, native_session_store: SessionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Achado de segurança: `diff` vem de um PR de terceiros (não
        confiável) — rodar com o registry completo do agente (fs/git/web/mcp)
        e permission_mode="auto" daria a uma instrução maliciosa embutida no
        diff acesso irrestrito, sem humano no loop pra aprovar. Mesmo que
        `native_agent.tool_registry` venha com ferramentas de verdade,
        `run_conversation` deve receber um registry vazio."""
        monkeypatch.setenv("VECTORA_MODEL", "google_genai:gemini-flash")

        from pydantic import BaseModel

        from backend.tools.registry import ToolExtras, ToolSpec

        class _SemArgs(BaseModel):
            pass

        async def _nunca_deveria_ser_chamado() -> str:
            return "não deveria nunca ser chamado"

        registro_com_ferramentas = ToolRegistry()
        registro_com_ferramentas.register(
            ToolSpec(
                name="fs_write",
                description="escreve arquivo",
                args_model=_SemArgs,
                handler=_nunca_deveria_ser_chamado,
                extras=ToolExtras(),
                needs_ctx=False,
            )
        )
        native_agent = NativeAgent(
            tool_registry=registro_com_ferramentas,
            subagent_catalog={},
            system_prompt="system",
        )

        async def _fake_get_native_agent(
            user_id: str | None = None,
            chat_mode: bool = False,
            workspace_id: str | None = None,
        ) -> NativeAgent:
            return native_agent

        _patch_native_engine(monkeypatch, session_store=native_session_store)
        monkeypatch.setattr(agent_factory, "get_native_agent", _fake_get_native_agent)

        registros_recebidos: list[ToolRegistry] = []

        async def _fake_run_conversation(**kwargs: Any) -> Any:
            from backend.engine.conversation_loop import LoopResult

            registros_recebidos.append(kwargs["tool_registry"])
            return LoopResult(
                stopped_reason="stop",
                final_message=text_message(MessageRole.ASSISTANT, "ok"),
            )

        monkeypatch.setattr(
            conversation_loop, "run_conversation", _fake_run_conversation
        )

        await review_job.run_review_job("diff x", {})

        assert len(registros_recebidos) == 1
        assert registros_recebidos[0].all() == []
