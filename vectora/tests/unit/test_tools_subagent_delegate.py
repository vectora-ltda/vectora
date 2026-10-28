"""``delegate_to_subagent`` — tool nativa registrada em ``TOOL_REGISTRY``
que o loop nativo chama pra invocar uma SOUL, consumindo ``run_subagent``
(``backend/engine/subagents.py``) por baixo. Mesmo ``_ScriptedChatClient``/
``_HangingChatClient`` de ``test_engine_subagents.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.engine.subagents import LivenessConfig, SubagentSpec
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY
from backend.tools.subagent_delegate import SubagentDeps
from backend.vtypes.message import ToolCallChunk, VMessageChunk


class _HangingChatClient:
    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        await asyncio.sleep(3600)
        yield  # pragma: no cover - nunca alcançado

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        msg = "não usado (astream-only)"
        raise NotImplementedError(msg)


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


def _texto_chunk(texto: str) -> VMessageChunk:
    return VMessageChunk(delta_text=texto)


@pytest.fixture
async def session_store(tmp_path):
    pool = AsyncConnectionPool(
        str(tmp_path / "subagent_delegate.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    await store.create_session("thread-pai", user_id="alice")
    try:
        yield store
    finally:
        await pool.close()


def _spec(nome: str = "coder") -> SubagentSpec:
    return SubagentSpec(
        name=nome,
        description="agente de teste",
        system_prompt="você é um agente de teste",
        tools=[],
    )


def _ctx(*, extra: dict | None = None) -> ToolContext:
    ctx = ToolContext(user_id="alice", thread_id="thread-pai", permission_mode="ask")
    if extra:
        ctx._extra.update(extra)
    return ctx


def _handler():
    spec = TOOL_REGISTRY.get("delegate_to_subagent")
    assert spec is not None
    return spec.handler


class TestDelegateToSubagent:
    async def test_solicitacao_trivial_nao_inicia_subagente(self, session_store):
        client = _ScriptedChatClient([[_texto_chunk("não deveria ser chamado")]])
        deps = SubagentDeps(
            catalog={"coder": _spec("coder")},
            session_store=session_store,
            chat_client=client,
            should_require_approval=None,
        )

        resultado = await _handler()(
            subagent_type="coder",
            prompt="Olá, quem é você?",
            ctx=_ctx(extra={"subagent_deps": deps}),
        )

        assert resultado.startswith("Delegação não necessária")
        assert client.chamadas == 0

    async def test_registrada_no_tool_registry(self):
        spec = TOOL_REGISTRY.get("delegate_to_subagent")
        assert spec is not None
        assert spec.needs_ctx is True

    async def test_delegacao_bem_sucedida_devolve_texto_do_subagente(
        self, session_store
    ):
        client = _ScriptedChatClient([[_texto_chunk("respondido pela SOUL")]])
        deps = SubagentDeps(
            catalog={"coder": _spec("coder")},
            session_store=session_store,
            chat_client=client,
            should_require_approval=None,
        )
        handler = _handler()

        resultado = await handler(
            subagent_type="coder",
            prompt="implemente algo",
            ctx=_ctx(extra={"subagent_deps": deps}),
        )

        assert resultado == "respondido pela SOUL"

    async def test_soul_inexistente_e_timeout_devolvem_erro_tipado_sem_propagar(
        self, session_store
    ):
        """Erro/borda (2 casos, mesmo teste): `subagent_type` fora do
        catálogo devolve erro tipado sem tocar no chat client; SOUL que
        existe mas nunca progride (timeout via liveness) também devolve
        erro tipado (`run_subagent` cancela via watchdog) — nenhum dos
        dois propaga exceção crua pro loop."""
        handler = _handler()

        deps_sem_soul = SubagentDeps(
            catalog={},
            session_store=session_store,
            chat_client=_ScriptedChatClient([[_texto_chunk("nunca chamado")]]),
            should_require_approval=None,
        )
        resultado_inexistente = await handler(
            subagent_type="soul-que-nao-existe",
            prompt="faça algo",
            ctx=_ctx(extra={"subagent_deps": deps_sem_soul}),
        )
        assert resultado_inexistente.startswith("Error:")
        assert "soul-que-nao-existe" in resultado_inexistente

        client_travado = _HangingChatClient()
        deps_timeout = SubagentDeps(
            catalog={"coder": _spec("coder")},
            session_store=session_store,
            chat_client=client_travado,
            should_require_approval=None,
            liveness=LivenessConfig(
                heartbeat_interval_s=0.02, max_stalled_heartbeats=2
            ),
        )
        resultado_timeout = await handler(
            subagent_type="coder",
            prompt="trabalhe para sempre",
            ctx=_ctx(extra={"subagent_deps": deps_timeout}),
        )
        assert resultado_timeout.startswith("Subagente")
        assert "cancelado por inatividade" in resultado_timeout

    async def test_sem_dependencias_injetadas_devolve_erro_tipado(self):
        handler = _handler()

        resultado = await handler(
            subagent_type="coder",
            prompt="faça algo",
            ctx=_ctx(),
        )

        assert resultado.startswith("Error:")
        assert "subagent_deps" in resultado


class TestShouldRequireApprovalObrigatorio:
    """`should_require_approval` não tem default em `SubagentDeps.__init__`
    de propósito — desligar HITL pra tudo que uma delegação de subagente
    faz precisa ser uma escolha explícita, nunca um esquecimento
    silencioso que injeta dependências sem política de aprovação."""

    def test_subagent_deps_sem_should_require_approval_estoura_typeerror(
        self, session_store
    ):
        with pytest.raises(TypeError, match="should_require_approval"):
            SubagentDeps(  # type: ignore[call-arg]  # ty: ignore[missing-argument]
                catalog={"coder": _spec("coder")},
                session_store=session_store,
                chat_client=_HangingChatClient(),
            )
