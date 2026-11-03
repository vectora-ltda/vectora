"""Tests for backend/engine/goal_mode.py.

Goal-mode (Ralph loop) encadeia turnos de `run_conversation` até o objetivo
ser cumprido — critério AND independente entre gates de qualidade
(`_run_quality_gates`, comandos externos) e o judge (`_judge_goal`, LLM
síncrono). Cada caminho feliz tem o par de erro/borda no mesmo teste
(CLAUDE.md §18). SQLite temporário real (SessionStore) + chat client
roteirizado real, nunca mock isolado do pipeline.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import cast

import pytest

from backend.engine import goal_mode
from backend.engine.conversation_loop import LoopConfig
from backend.llm.systemone.harness import SystemOneHarness
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import ContentBlock, MessageRole, VMessage, VMessageChunk


@pytest.fixture
async def session_store(tmp_path):
    pool = AsyncConnectionPool(
        str(tmp_path / "goal-sessions.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


def _text_turn(texto: str) -> list[VMessageChunk]:
    return [VMessageChunk(delta_text=texto)]


def _judge_response(cumprido: bool, motivo: str) -> VMessage:
    payload = json.dumps({"cumprido": cumprido, "motivo": motivo})
    return VMessage(
        role=MessageRole.ASSISTANT, content=[ContentBlock(kind="text", text=payload)]
    )


class _GoalChatClient:
    """Chat client fake real (astream + agenerate) — `astream` alimenta
    `run_conversation` (um turno por chamada), `agenerate` alimenta o judge
    do goal loop. Itens de `judgments` podem ser `Exception` para simular
    falha de transporte/parse."""

    def __init__(
        self,
        turnos: list[list[VMessageChunk]],
        judgments: list[tuple[bool, str] | Exception],
    ) -> None:
        self._turnos = turnos
        self._judgments = judgments
        self.turn_calls = 0
        self.judge_calls = 0

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        turno = self._turnos[self.turn_calls]
        self.turn_calls += 1
        for chunk in turno:
            yield chunk

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        item = self._judgments[self.judge_calls]
        self.judge_calls += 1
        if isinstance(item, Exception):
            raise item
        cumprido, motivo = item
        return _judge_response(cumprido, motivo)


def _ctx() -> ToolContext:
    return ToolContext(user_id="u1", thread_id="goal-thread")


async def _new_thread(session_store: SessionStore, thread_id: str) -> None:
    await session_store.create_session(thread_id, user_id="u1", mode="background")
    await session_store.append_message(
        thread_id,
        VMessage(
            role=MessageRole.USER,
            content=[ContentBlock(kind="text", text="objetivo: implemente X")],
        ),
    )


async def test_objetivo_cumprido_em_n_turnos_par_com_turn_budget_esgotado(
    session_store,
):
    """Happy: judge aprova no 2º turno, `status="done"`. Par de erro no MESMO
    teste: judge nunca aprova + turn budget pequeno -> `status="error"`, e o
    loop realmente para (prova de ausência de loop infinito via
    `asyncio.wait_for(..., timeout=5)`)."""
    thread_id = "goal-happy"
    await _new_thread(session_store, thread_id)
    chat_client = _GoalChatClient(
        turnos=[_text_turn("trabalhando..."), _text_turn("terminei X")],
        judgments=[(False, "ainda falta parte 2"), (True, "objetivo cumprido de fato")],
    )

    outcome = await asyncio.wait_for(
        goal_mode.run_goal(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=ToolRegistry(),
            ctx=_ctx(),
            thread_id=thread_id,
            goal="implemente X",
            loop_config=LoopConfig(max_iterations=10),
            max_goal_turns=10,
            should_require_approval=None,
        ),
        timeout=5,
    )

    assert outcome.status == "done"
    assert outcome.turns_used == 2
    assert chat_client.turn_calls == 2
    assert chat_client.judge_calls == 2
    assert outcome.reason == "objetivo cumprido de fato"

    # Erro/borda: judge nunca aprova, turn budget pequeno -> "error", sem
    # loop infinito (timeout do wait_for provaria isso se travasse).
    thread_id_erro = "goal-turn-budget"
    await _new_thread(session_store, thread_id_erro)
    chat_client_erro = _GoalChatClient(
        turnos=[_text_turn(f"tentativa {i}") for i in range(3)],
        judgments=[(False, "nunca fica bom") for _ in range(3)],
    )
    outcome_erro = await asyncio.wait_for(
        goal_mode.run_goal(
            session_store=session_store,
            chat_client=chat_client_erro,
            tool_registry=ToolRegistry(),
            ctx=_ctx(),
            thread_id=thread_id_erro,
            goal="implemente X",
            loop_config=LoopConfig(max_iterations=10),
            max_goal_turns=3,
            should_require_approval=None,
        ),
        timeout=5,
    )
    assert outcome_erro.status == "error"
    assert "turn budget esgotado" in outcome_erro.reason
    assert outcome_erro.turns_used == 3
    assert chat_client_erro.turn_calls == 3


async def test_falhas_consecutivas_do_judge_disparam_auto_pause(session_store):
    """Falha de transporte/parse do judge repetida 3x seguidas dispara
    auto-pause com motivo distinto de turn budget — mesmo com turn budget
    grande o suficiente pra nunca ser a causa real."""
    thread_id = "goal-judge-falha"
    await _new_thread(session_store, thread_id)
    chat_client = _GoalChatClient(
        turnos=[_text_turn(f"tentativa {i}") for i in range(10)],
        judgments=[
            RuntimeError("provider fora do ar"),
            RuntimeError("provider fora do ar"),
            RuntimeError("provider fora do ar"),
        ],
    )

    outcome = await asyncio.wait_for(
        goal_mode.run_goal(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=ToolRegistry(),
            ctx=_ctx(),
            thread_id=thread_id,
            goal="implemente X",
            loop_config=LoopConfig(max_iterations=10),
            max_goal_turns=10,
            should_require_approval=None,
        ),
        timeout=5,
    )

    assert outcome.status == "error"
    assert "judge falhou" in outcome.reason
    assert "turn budget esgotado" not in outcome.reason
    assert outcome.turns_used == 3
    assert chat_client.judge_calls == 3


@pytest.mark.asyncio
async def test_plan_mode_respeita_flag_de_decision_model(session_store, monkeypatch):
    thread_id = "goal-decision-model-flag"
    await _new_thread(session_store, thread_id)
    chat_client = _GoalChatClient(
        turnos=[_text_turn("feito")],
        judgments=[(True, "objetivo cumprido")],
    )
    model_calls: list[object] = []

    async def evaluate(harness, **kwargs):
        model_calls.append(harness)
        return "completed", True, "aprovado pelo decision model"

    monkeypatch.setattr(goal_mode, "evaluate_goal_with_systemone", evaluate)
    ctx = _ctx()
    ctx._extra["decision_model_preferences"] = {
        "enabled": True,
        "provider": "clef-local",
        "surfaces": {"plan_mode": False},
    }
    supplied_harness = cast("SystemOneHarness", object())

    result = await goal_mode.run_goal(
        session_store=session_store,
        chat_client=chat_client,
        tool_registry=ToolRegistry(),
        ctx=ctx,
        thread_id=thread_id,
        goal="implemente X",
        loop_config=LoopConfig(max_iterations=10),
        max_goal_turns=1,
        should_require_approval=None,
        systemone_harness=supplied_harness,
    )

    assert result.status == "done"
    assert model_calls == []
    assert chat_client.judge_calls == 1


async def test_mesmo_gate_falhando_repetidamente_dispara_auto_pause_antes_do_turn_budget(
    session_store,
):
    """Gate de qualidade sozinho bloqueia mesmo com o judge sempre aprovando
    — prova que o AND é real (judge aprovar não basta) e que o mesmo gate
    falhando repetidas vezes dispara pausa distinta de turn budget."""
    thread_id = "goal-gate-preso"
    await _new_thread(session_store, thread_id)
    chat_client = _GoalChatClient(
        turnos=[_text_turn(f"tentativa {i}") for i in range(10)],
        judgments=[(True, "parece pronto") for _ in range(10)],
    )
    gate_que_falha_sempre = [sys.executable, "-c", "import sys; sys.exit(1)"]

    outcome = await asyncio.wait_for(
        goal_mode.run_goal(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=ToolRegistry(),
            ctx=_ctx(),
            thread_id=thread_id,
            goal="implemente X",
            loop_config=LoopConfig(max_iterations=10),
            quality_gates=[gate_que_falha_sempre],
            max_goal_turns=20,
            should_require_approval=None,
        ),
        timeout=20,
    )

    assert outcome.status == "error"
    assert "gate de qualidade" in outcome.reason
    assert "turn budget esgotado" not in outcome.reason
    # Trava bem antes do teto de 20 turnos configurado.
    assert outcome.turns_used < 20


async def test_gate_e_judge_sao_criterios_independentes_nunca_marca_done_sozinho(
    session_store, tmp_path
):
    """AND real, dois subcasos no mesmo teste: (1) gate passa mas judge
    rejeita -> não fecha; (2) judge aprova mas gate falha -> não fecha. Só
    fecha quando os dois aprovam no mesmo turno."""
    gate_ok = [sys.executable, "-c", "import sys; sys.exit(0)"]

    # Subcaso 1: gate sempre passa, judge só aprova no 2º turno.
    thread_id_1 = "goal-gate-ok-judge-rejeita-depois-aprova"
    await _new_thread(session_store, thread_id_1)
    chat_client_1 = _GoalChatClient(
        turnos=[_text_turn("turno 1"), _text_turn("turno 2")],
        judgments=[
            (False, "gate passou mas ainda não está certo"),
            (True, "agora sim"),
        ],
    )
    outcome_1 = await asyncio.wait_for(
        goal_mode.run_goal(
            session_store=session_store,
            chat_client=chat_client_1,
            tool_registry=ToolRegistry(),
            ctx=_ctx(),
            thread_id=thread_id_1,
            goal="implemente X",
            loop_config=LoopConfig(max_iterations=10),
            quality_gates=[gate_ok],
            max_goal_turns=10,
            should_require_approval=None,
        ),
        timeout=10,
    )
    assert outcome_1.turns_used == 2
    assert outcome_1.status == "done"

    # Subcaso 2: judge sempre aprova, gate real só passa a partir da 2ª
    # execução (contador persistido em arquivo — cada chamada é um
    # subprocess novo, sem estado Python compartilhado) — gate falhando 1x
    # não é "repetido" o bastante pra disparar o auto-pause do teste
    # anterior; aqui provamos que o critério continua AND mesmo sem travar.
    contador_path = tmp_path / "gate_calls.txt"
    gate_intermitente_script = (
        "import pathlib, sys\n"
        f"p = pathlib.Path({str(contador_path)!r})\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "sys.exit(0 if n >= 2 else 1)\n"
    )
    thread_id_2 = "goal-judge-ok-gate-falha-depois-passa"
    await _new_thread(session_store, thread_id_2)
    chat_client_2 = _GoalChatClient(
        turnos=[_text_turn("turno 1"), _text_turn("turno 2")],
        judgments=[(True, "já está pronto na minha avaliação") for _ in range(2)],
    )

    outcome_2 = await asyncio.wait_for(
        goal_mode.run_goal(
            session_store=session_store,
            chat_client=chat_client_2,
            tool_registry=ToolRegistry(),
            ctx=_ctx(),
            thread_id=thread_id_2,
            goal="implemente X",
            loop_config=LoopConfig(max_iterations=10),
            quality_gates=[[sys.executable, "-c", gate_intermitente_script]],
            max_goal_turns=10,
            should_require_approval=None,
        ),
        timeout=10,
    )

    assert outcome_2.status == "done"
    assert outcome_2.turns_used == 2
    assert contador_path.read_text() == "2"


async def test_run_quality_gates_executa_comandos_reais_sequencial_e_trunca_saida(
    session_store,
):
    """`_run_quality_gates` roda comando real via subprocess assíncrono
    (nunca `subprocess.run`), sequencial. Erro/borda: comando com exit != 0
    vira `GateResult(passed=False)` com a saída capturada, sem levantar."""
    ok = [sys.executable, "-c", "print('tudo certo')"]
    falha = [sys.executable, "-c", "import sys; print('deu ruim'); sys.exit(3)"]

    resultados = await goal_mode._run_quality_gates([ok, falha])

    assert len(resultados) == 2
    assert resultados[0].passed is True
    assert resultados[0].exit_code == 0
    assert "tudo certo" in resultados[0].output
    assert resultados[1].passed is False
    assert resultados[1].exit_code == 3
    assert "deu ruim" in resultados[1].output


async def test_resume_goal_sem_pendencia_real_devolve_erro_idempotente(session_store):
    """Erro/borda de `resume_goal`: sem aprovação pendente (duplo-clique/
    retry), devolve `status="error"` de forma defensiva, sem propagar."""
    thread_id = "goal-resume-sem-pendencia"
    await _new_thread(session_store, thread_id)
    chat_client = _GoalChatClient(turnos=[], judgments=[])

    outcome = await goal_mode.resume_goal(
        session_store=session_store,
        chat_client=chat_client,
        tool_registry=ToolRegistry(),
        ctx=_ctx(),
        thread_id=thread_id,
        goal="implemente X",
        loop_config=LoopConfig(max_iterations=10),
        decision="approve",
        should_require_approval=None,
    )

    assert outcome.status == "error"
    assert "pendente" in outcome.reason


class TestShouldRequireApprovalObrigatorio:
    """`should_require_approval` não tem default em `run_goal`/`resume_goal`
    de propósito — desligar HITL precisa ser uma escolha explícita no call
    site (`should_require_approval=None`), nunca um esquecimento silencioso
    que roda um goal-mode inteiro sem nenhuma pausa de aprovação."""

    async def test_run_goal_sem_should_require_approval_estoura_typeerror(
        self, session_store
    ):
        with pytest.raises(TypeError, match="should_require_approval"):
            await goal_mode.run_goal(  # type: ignore[call-arg]  # ty: ignore[missing-argument]
                session_store=session_store,
                chat_client=_GoalChatClient(turnos=[], judgments=[]),
                tool_registry=ToolRegistry(),
                ctx=_ctx(),
                thread_id="goal-sem-hitl-kwarg",
                goal="implemente X",
                loop_config=LoopConfig(max_iterations=10),
            )

    async def test_resume_goal_sem_should_require_approval_estoura_typeerror(
        self, session_store
    ):
        with pytest.raises(TypeError, match="should_require_approval"):
            await goal_mode.resume_goal(  # type: ignore[call-arg]  # ty: ignore[missing-argument]
                session_store=session_store,
                chat_client=_GoalChatClient(turnos=[], judgments=[]),
                tool_registry=ToolRegistry(),
                ctx=_ctx(),
                thread_id="goal-resume-sem-hitl-kwarg",
                goal="implemente X",
                loop_config=LoopConfig(max_iterations=10),
                decision="approve",
            )
