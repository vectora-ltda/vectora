"""``run_subagent``/``spawn_subagent_background`` — delegação de
subagentes nativa. Cada subagente é uma instância nova do motor
(`run_conversation`) numa sub-thread isolada com `parent_thread_id`
gravado — testado com o mesmo `_ScriptedChatClient` de
`test_engine_conversation_loop.py`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import pytest

from backend.engine.guardrails import LoopCapConfig, TurnBudget
from backend.engine.stream_events import SubagentOutput
from backend.engine.subagents import (
    _ACTIVE_CONVERSATION_TASKS,
    LivenessConfig,
    SubagentSpec,
    request_hard_interrupt,
    run_subagent,
    spawn_subagent_background,
    subagent_capability_token,
)
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolExtras, vtool
from backend.vtypes.ids import CapabilityToken, CorrelationId
from backend.vtypes.message import ToolCallChunk, VMessageChunk


async def _aguarda_task_ativa(correlation_id: str, prazo_s: float = 2.0):
    """Espera `_ACTIVE_CONVERSATION_TASKS[correlation_id]` existir e ainda
    estar rodando — evita corrida entre o teste disparar `run_subagent` em
    background e a task de conversa ainda não ter sido registrada."""
    deadline = time.monotonic() + prazo_s
    while time.monotonic() < deadline:
        task = _ACTIVE_CONVERSATION_TASKS.get(correlation_id)
        if task is not None and not task.done():
            return task
        await asyncio.sleep(0.01)
    msg = f"task de '{correlation_id}' nunca ficou ativa"
    raise AssertionError(msg)


class _HangingChatClient:
    """Nunca progride — simula um subagente preso em loop, sem depender de
    tempo real longo (o teste cancela via `asyncio.sleep` cancelável)."""

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        await asyncio.sleep(3600)
        yield  # pragma: no cover - nunca alcançado, mantém a função geradora

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


def _tool_call_chunk(*, index: int, id: str, name: str, args: str) -> VMessageChunk:  # noqa: A002
    return VMessageChunk(
        tool_call_chunks=[
            ToolCallChunk(index=index, id=id, name=name, args_fragment=args)
        ]
    )


@pytest.fixture
async def session_store(tmp_path):
    pool = AsyncConnectionPool(str(tmp_path / "subagents.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    await store.create_session("thread-pai", user_id="alice")
    try:
        yield store
    finally:
        await pool.close()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(
        user_id="alice",
        thread_id="thread-pai",
        permission_mode="ask",
        tool_call_id="call-1",
    )


def _spec(nome: str = "coder", tools=None) -> SubagentSpec:
    return SubagentSpec(
        name=nome,
        description="agente de teste",
        system_prompt="você é um agente de teste",
        tools=tools or [],
    )


class TestRunSubagentSincrono:
    async def test_devolve_o_texto_final_do_subagente(self, session_store, ctx):
        client = _ScriptedChatClient([[_texto_chunk("resultado do subagente")]])

        resultado = await run_subagent(
            _spec(),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        assert resultado == "resultado do subagente"

    async def test_sub_thread_isolada_com_parent_thread_id_gravado(
        self, session_store, ctx
    ):
        client = _ScriptedChatClient([[_texto_chunk("ok")]])

        await run_subagent(
            _spec(nome="search"),
            "pesquise algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        async with session_store._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT thread_id, parent_thread_id, mode FROM sessions "
                "WHERE parent_thread_id = ?",
                ("thread-pai",),
            )
            row = await cur.fetchone()

        assert row is not None
        thread_id, parent_thread_id, mode = row
        assert parent_thread_id == "thread-pai"
        assert mode == "subagent"
        assert thread_id.startswith("thread-pai:search:")
        assert thread_id != "thread-pai"

    async def test_historico_do_subagente_nao_herda_o_do_pai(self, session_store, ctx):
        # Popula o histórico do pai antes de delegar.
        from backend.vtypes.message import MessageRole, text_message

        await session_store.append_message(
            "thread-pai", text_message(MessageRole.USER, "mensagem do pai")
        )
        client = _ScriptedChatClient([[_texto_chunk("resposta isolada")]])

        await run_subagent(
            _spec(),
            "tarefa nova",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        historico_pai = await session_store.get_history("thread-pai")
        assert [m.text() for m in historico_pai] == ["mensagem do pai"]

    async def test_emite_subagent_output_running_e_complete(self, session_store, ctx):
        client = _ScriptedChatClient([[_texto_chunk("feito")]])
        eventos: list[SubagentOutput] = []

        async def on_event(event):
            if isinstance(event, SubagentOutput):
                eventos.append(event)

        await run_subagent(
            _spec(nome="coder"),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            on_event=on_event,
            should_require_approval=None,
        )

        assert [e.status for e in eventos] == ["running", "running", "complete"]
        assert eventos[1].is_delta is True
        assert eventos[1].content == "feito"
        assert eventos[-1].content == "feito"
        assert [e.tool_call_id for e in eventos] == ["call-1", "call-1", "call-1"]

    async def test_hitl_dentro_do_subagente_pausa_sem_emitir_complete(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(destructive=True))
        async def escrever_no_subagente(ctx: ToolContext) -> str:
            """escreve algo."""
            return "nunca roda"

        spec = TOOL_REGISTRY.get("escrever_no_subagente")
        assert spec is not None

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="call_1",
                        name="escrever_no_subagente",
                        args="{}",
                    )
                ]
            ]
        )
        eventos: list[SubagentOutput] = []

        async def on_event(event):
            if isinstance(event, SubagentOutput):
                eventos.append(event)

        resultado = await run_subagent(
            _spec(tools=[spec]),
            "escreva algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            on_event=on_event,
            should_require_approval=lambda *_a: True,
        )

        assert resultado == ""
        assert [e.status for e in eventos] == ["running"]


class TestSpawnSubagentBackground:
    async def test_roda_em_background_e_pode_ser_esperada_depois(
        self, session_store, ctx
    ):
        client = _ScriptedChatClient([[_texto_chunk("resultado em background")]])

        task = spawn_subagent_background(
            _spec(),
            "faça em background",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        assert isinstance(task, asyncio.Task)
        resultado = await task
        assert resultado == "resultado em background"


class TestTurnBudgetDoTurnoPai:
    async def test_spawn_recusado_quando_teto_do_turno_pai_ja_estourou(
        self, session_store, ctx
    ):
        """`turn_budget` é o mesmo objeto do turno do agente pai — se já
        estourou (por qualquer dimensão), o spawn é recusado sem gastar
        nenhum recurso: nenhuma sessão criada, nenhuma chamada ao chat
        client."""
        budget = TurnBudget(config=LoopCapConfig(max_subagent_spawns_per_turn=0))
        client = _ScriptedChatClient([[_texto_chunk("nunca deveria rodar")]])

        resultado = await run_subagent(
            _spec(),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            turn_budget=budget,
            should_require_approval=None,
        )

        assert "não foi disparado" in resultado
        assert client.chamadas == 0
        assert budget.subagent_spawns == 0

    async def test_spawn_dentro_do_teto_incrementa_o_budget_do_pai(
        self, session_store, ctx
    ):
        budget = TurnBudget(config=LoopCapConfig(max_subagent_spawns_per_turn=2))
        client = _ScriptedChatClient([[_texto_chunk("ok")]])

        resultado = await run_subagent(
            _spec(),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            turn_budget=budget,
            should_require_approval=None,
        )

        assert resultado == "ok"
        assert budget.subagent_spawns == 1
        assert budget.exceeded is None


class TestLivenessAtiva:
    async def test_sem_liveness_config_roda_ate_completar_normalmente(
        self, session_store, ctx
    ):
        client = _ScriptedChatClient([[_texto_chunk("completou sem watchdog")]])

        resultado = await run_subagent(
            _spec(),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        assert resultado == "completou sem watchdog"

    async def test_subagente_progredindo_nao_dispara_cancelamento(
        self, session_store, ctx
    ):
        """Happy path: watchdog ativo, mas o subagente termina rápido —
        nunca chega a competir com o watchdog."""
        client = _ScriptedChatClient([[_texto_chunk("terminei rápido")]])

        resultado = await run_subagent(
            _spec(),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            liveness=LivenessConfig(heartbeat_interval_s=5.0, max_stalled_heartbeats=3),
            should_require_approval=None,
        )

        assert resultado == "terminei rápido"

    async def test_subagente_sem_progresso_e_cancelado_pelo_watchdog(
        self, session_store, ctx
    ):
        """Erro/borda: subagente travado (nunca emite evento) é cancelado
        depois de `heartbeat_interval_s * max_stalled_heartbeats`, resultado
        vira status='cancelled' — nunca trava o processo pai pra sempre."""
        client = _HangingChatClient()
        eventos: list[SubagentOutput] = []

        async def on_event(event):
            if isinstance(event, SubagentOutput):
                eventos.append(event)

        resultado = await run_subagent(
            _spec(),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            liveness=LivenessConfig(
                heartbeat_interval_s=0.02, max_stalled_heartbeats=2
            ),
            on_event=on_event,
            should_require_approval=None,
        )

        assert "cancelado por inatividade" in resultado
        assert eventos[-1].status == "cancelled"


class TestEscopoRBACDoSubagente:
    async def test_soul_com_toolset_dentro_do_escopo_delega_normalmente(
        self, session_store, ctx, monkeypatch
    ):
        from backend.rbac import tool_policy

        @vtool(extras=ToolExtras())
        async def tool_permitida(ctx: ToolContext) -> str:
            """tool permitida."""
            return "ok"

        spec = TOOL_REGISTRY.get("tool_permitida")
        assert spec is not None
        monkeypatch.setattr(tool_policy, "effective_disabled", lambda _uid: set())

        client = _ScriptedChatClient([[_texto_chunk("delegado")]])

        resultado = await run_subagent(
            _spec(tools=[spec]),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        assert resultado == "delegado"

    async def test_soul_pedindo_tool_fora_do_escopo_e_rejeitada_sem_chamar_llm(
        self, session_store, ctx, monkeypatch
    ):
        """Erro/borda: tool desabilitada (kill-switch global ou ABAC do
        usuário) nunca chega a rodar dentro do subagente — erro tipado
        antes de qualquer sessão/chamada ao chat client."""
        from backend.rbac import tool_policy

        @vtool(extras=ToolExtras())
        async def tool_fora_do_escopo(ctx: ToolContext) -> str:
            """tool fora do escopo."""
            return "nunca deveria rodar"

        spec = TOOL_REGISTRY.get("tool_fora_do_escopo")
        assert spec is not None
        monkeypatch.setattr(
            tool_policy, "effective_disabled", lambda _uid: {"tool_fora_do_escopo"}
        )

        client = _ScriptedChatClient([[_texto_chunk("nunca deveria rodar")]])

        resultado = await run_subagent(
            _spec(tools=[spec]),
            "faça algo",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        assert "fora do escopo RBAC" in resultado
        assert "tool_fora_do_escopo" in resultado
        assert client.chamadas == 0


class TestDedupPorCorrelationId:
    async def test_correlation_id_duplicado_reaproveita_a_delegacao_em_andamento(
        self, session_store, ctx
    ):
        """Duas delegações concorrentes com o mesmo `correlation_id` não
        criam duas sessões/chamadas ao chat client — a segunda reaproveita
        o resultado da primeira, já em andamento. Erro/borda coberto por
        `TestHardInterruptRealNaoPassivo` (correlation_id sem delegação
        ativa nunca cancela nada)."""
        client = _ScriptedChatClient([[_texto_chunk("resultado único")]])
        spec = replace(_spec(), correlation_id=CorrelationId("corr-dedup-1"))

        resultados = await asyncio.gather(
            run_subagent(
                spec,
                "faça algo",
                session_store=session_store,
                chat_client=client,
                ctx=ctx,
                parent_thread_id="thread-pai",
                should_require_approval=None,
            ),
            run_subagent(
                spec,
                "faça algo de novo",
                session_store=session_store,
                chat_client=client,
                ctx=ctx,
                parent_thread_id="thread-pai",
                should_require_approval=None,
            ),
        )

        assert resultados == ["resultado único", "resultado único"]
        assert client.chamadas == 1

    async def test_correlation_id_diferente_roda_delegacoes_independentes(
        self, session_store, ctx
    ):
        """Erro/borda simétrico: ids diferentes nunca deduplicam — cada
        delegação roda e chama o chat client separadamente."""
        client = _ScriptedChatClient(
            [[_texto_chunk("resultado 1")], [_texto_chunk("resultado 2")]]
        )
        spec_a = replace(_spec(), correlation_id=CorrelationId("corr-a"))
        spec_b = replace(_spec(), correlation_id=CorrelationId("corr-b"))

        resultado_a = await run_subagent(
            spec_a,
            "tarefa a",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )
        resultado_b = await run_subagent(
            spec_b,
            "tarefa b",
            session_store=session_store,
            chat_client=client,
            ctx=ctx,
            parent_thread_id="thread-pai",
            should_require_approval=None,
        )

        assert resultado_a == "resultado 1"
        assert resultado_b == "resultado 2"
        assert client.chamadas == 2


class TestHardInterruptRealNaoPassivo:
    async def test_token_valido_cancela_a_task_de_verdade_invalido_e_rejeitado(
        self, session_store, ctx
    ):
        """Erro/borda: token HMAC inválido nunca cancela nada, mesmo com o
        `correlation_id` certo e o subagente realmente em execução — só o
        token correto (`subagent_capability_token`) autoriza. Prova de que
        o cancelamento é ATIVO (chama `Task.cancel()` de verdade), não só
        uma flag: `task_interna.cancelled()` fica `True` depois do
        interrupt, distinto do watchdog de liveness (timeout passivo,
        testado em `TestLivenessAtiva`)."""
        client = _HangingChatClient()
        spec = replace(_spec(), correlation_id=CorrelationId("corr-hard-1"))

        task = asyncio.create_task(
            run_subagent(
                spec,
                "trabalhe para sempre",
                session_store=session_store,
                chat_client=client,
                ctx=ctx,
                parent_thread_id="thread-pai",
                should_require_approval=None,
            )
        )

        task_interna = await _aguarda_task_ativa("corr-hard-1")

        assert (
            request_hard_interrupt(
                CorrelationId("corr-hard-1"), CapabilityToken("token-errado")
            )
            is False
        )
        assert not task_interna.cancelled()
        assert not task_interna.done()

        token_valido = subagent_capability_token(CorrelationId("corr-hard-1"))
        assert (
            request_hard_interrupt(CorrelationId("corr-hard-1"), token_valido) is True
        )

        resultado = await task

        assert task_interna.cancelled()
        assert "pedido explícito" in resultado

    async def test_sem_task_em_execucao_devolve_false_mesmo_com_token_valido(
        self, session_store, ctx
    ):
        """Erro/borda: `correlation_id` que nunca teve (ou já terminou) uma
        delegação em execução nunca é achado pra cancelar — mesmo com o
        token correto."""
        token_valido = subagent_capability_token(CorrelationId("corr-inexistente"))
        assert (
            request_hard_interrupt(CorrelationId("corr-inexistente"), token_valido)
            is False
        )


class TestShouldRequireApprovalObrigatorio:
    """`should_require_approval` não tem default em `run_subagent` de
    propósito — desligar HITL pra tudo que um subagente faz precisa ser
    uma escolha explícita no call site (`should_require_approval=None`),
    nunca um esquecimento silencioso."""

    async def test_run_subagent_sem_should_require_approval_estoura_typeerror(
        self, session_store, ctx
    ):
        client = _ScriptedChatClient([[_texto_chunk("nunca deveria rodar")]])
        with pytest.raises(TypeError, match="should_require_approval"):
            await run_subagent(  # type: ignore[call-arg]  # ty: ignore[missing-argument]
                _spec(),
                "faça algo",
                session_store=session_store,
                chat_client=client,
                ctx=ctx,
                parent_thread_id="thread-pai",
            )
