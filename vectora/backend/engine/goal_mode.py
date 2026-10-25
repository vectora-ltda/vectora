"""``run_goal`` — camada de Goal-mode (Ralph loop) sobre o motor nativo.

Encadeia turnos sucessivos de ``conversation_loop.run_conversation`` até um
objetivo em linguagem natural ser considerado cumprido, em vez de parar no
primeiro ``stopped_reason="stop"`` (que só significa "o modelo parou de
chamar tools", não "o objetivo foi atingido"). Dois critérios independentes
decidem o término, combinados via AND:

- **Gates de qualidade** (``_run_quality_gates``): comandos externos
  (ex. suíte de testes) que precisam sair com código 0. Sem gates
  configurados, esse critério é considerado satisfeito por vaziez.
- **Judge** (``_judge_goal``): uma chamada de LLM síncrona perguntando se o
  objetivo foi cumprido, no mesmo padrão de ``backend/tools/aitl.py::
  ask_parent_agent`` (JSON defensivo, nunca texto livre interpretado).

Quando nenhum dos dois aprova, o objetivo original + o motivo da rejeição +
a saída resumida dos gates que falharam viram uma nova mensagem de usuário
(``_continuation_message``), reinjetada na mesma thread — o próximo turno
já parte dali, sem nenhum estado além do que ``SessionStore`` persiste.

HITL não é decidido aqui: se um turno pausa em ``stopped_reason=
"interrupted"``, ``run_goal`` devolve o outcome pro caller sem rodar gates
nem judge — o goal loop nunca decide por cima de uma aprovação humana
pendente.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.engine.conversation_loop import resume_conversation, run_conversation
from backend.vtypes.message import ContentBlock, MessageRole, VMessage, text_message

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.engine.conversation_loop import LoopConfig
    from backend.engine.hitl import ApprovalGate
    from backend.llm.base import ChatClient
    from backend.llm.systemone.harness import SystemOneHarness
    from backend.persistence.native.session_store import SessionStore
    from backend.tools.context import ToolContext
    from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

#: Timeout por comando de gate — mesma ordem de grandeza de uma suíte de
#: testes real, sem deixar um comando travado prender o loop indefinidamente.
_GATE_TIMEOUT_SECONDS = 300

#: Mesmo teto de `_finish_run` (`backend/scheduling/background_tasks.py`) —
#: saída de gate truncada antes de virar contexto pro próximo turno.
_GATE_OUTPUT_MAX_CHARS = 4000

#: Falhas seguidas de parse/transporte ao consultar o judge (provider fora
#: do ar, resposta sem JSON válido) disparam auto-pause — nunca ficam
#: tentando pra sempre em silêncio.
_CONSECUTIVE_JUDGE_FAILURE_THRESHOLD = 3

#: O MESMO conjunto de gates falhando repetidamente (não apenas "algum
#: gate falhou") também dispara auto-pause — sinal de que o agente está
#: preso, não progredindo entre turnos.
_SAME_GATE_FAILURE_THRESHOLD = 3

_JUDGE_SYSTEM_PROMPT = (
    "Você avalia se um objetivo delegado a um agente autônomo foi cumprido, "
    "com base no resumo do turno mais recente dele. Responda SOMENTE com um "
    'JSON no formato {"cumprido": true|false, "motivo": "..."}, sem texto '
    "fora do JSON. Seja rigoroso: só marque cumprido=true quando o "
    "resultado realmente satisfaz o objetivo por completo, não apenas "
    "parcialmente ou 'quase'."
)


@dataclass(slots=True)
class GateResult:
    """Resultado da execução de um comando de gate de qualidade."""

    command: list[str]
    passed: bool
    exit_code: int
    output: str


@dataclass(slots=True)
class GoalOutcome:
    """Resultado de ``run_goal``/``resume_goal``.

    ``status``: ``"done"`` (objetivo cumprido) | ``"error"`` (turn budget
    esgotado, gate preso, ou falhas consecutivas do judge — nunca loop
    infinito silencioso) | ``"interrupted"`` (pausado em HITL, pendência
    propagada pro caller sem decisão do goal loop).
    """

    status: str
    reason: str
    turns_used: int
    final_message: VMessage | None = None


def _parse_judge_json(texto: str) -> dict[str, Any]:
    """Extrai o JSON `{"cumprido", "motivo"}` da resposta do judge —
    tolera o modelo envolver o JSON em texto/markdown ao redor."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fim == -1 or fim <= inicio:
            msg = f"resposta do judge não contém JSON válido: {texto[:200]!r}"
            raise ValueError(msg) from None
        return json.loads(texto[inicio : fim + 1])


async def _judge_goal(
    chat_client: ChatClient, goal: str, turn_summary: str
) -> tuple[bool, str]:
    """Pergunta ao chat client se `goal` foi cumprido dado `turn_summary`.

    Diferente de ``ask_parent_agent``, NÃO absorve a exceção — levanta em
    falha de transporte/parse. É responsabilidade do caller (``run_goal``)
    decidir como contar isso (falha consecutiva rumo ao auto-pause), não
    desta função esconder o problema convertendo em "não cumprido" silencioso.
    """
    resposta = await chat_client.agenerate(
        [
            VMessage(
                role=MessageRole.SYSTEM,
                content=[ContentBlock(kind="text", text=_JUDGE_SYSTEM_PROMPT)],
            ),
            VMessage(
                role=MessageRole.USER,
                content=[
                    ContentBlock(
                        kind="text",
                        text=f"Objetivo: {goal}\n\nResumo do turno mais recente:\n"
                        f"{turn_summary}",
                    )
                ],
            ),
        ]
    )
    dados = _parse_judge_json(resposta.text().strip())
    return bool(dados["cumprido"]), str(dados.get("motivo", ""))


async def _run_quality_gates(commands: list[list[str]]) -> list[GateResult]:
    """Roda cada comando sequencialmente via ``asyncio.create_subprocess_exec``
    (nunca ``subprocess.run`` síncrono). Comando com timeout ou que falha ao
    ser lançado vira ``GateResult(passed=False)``, nunca propaga — um gate
    mal configurado não pode derrubar o goal loop inteiro."""
    resultados: list[GateResult] = []
    for comando in commands:
        try:
            proc = await asyncio.create_subprocess_exec(
                *comando,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=_GATE_TIMEOUT_SECONDS
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                resultados.append(
                    GateResult(
                        command=comando,
                        passed=False,
                        exit_code=-1,
                        output=f"gate excedeu o timeout de {_GATE_TIMEOUT_SECONDS}s",
                    )
                )
                continue
            output = stdout_bytes.decode("utf-8", errors="replace")[
                :_GATE_OUTPUT_MAX_CHARS
            ]
            resultados.append(
                GateResult(
                    command=comando,
                    passed=proc.returncode == 0,
                    exit_code=proc.returncode or 0,
                    output=output,
                )
            )
        except Exception as exc:
            logger.warning(
                "goal_mode: falha ao executar gate %r", comando, exc_info=True
            )
            resultados.append(
                GateResult(
                    command=comando,
                    passed=False,
                    exit_code=-1,
                    output=f"falha ao executar gate: {exc}",
                )
            )
    return resultados


def _continuation_message(goal: str, motivo_judge: str, gates: list[GateResult]) -> str:
    """Monta a próxima mensagem de usuário quando o turno não fechou o
    objetivo — objetivo original + motivo da rejeição + saída resumida dos
    gates que falharam, pro próximo turno já partir com contexto completo."""
    partes = [f"O objetivo ainda não foi cumprido: {goal}"]
    if motivo_judge:
        partes.append(f"Motivo da avaliação: {motivo_judge}")
    falhas = [g for g in gates if not g.passed]
    if falhas:
        blocos = "\n".join(
            f"- `{' '.join(g.command)}` (exit {g.exit_code}):\n{g.output}"
            for g in falhas
        )
        partes.append(f"Gates de qualidade que falharam:\n{blocos}")
    partes.append("Continue trabalhando no objetivo, corrigindo o que for necessário.")
    return "\n\n".join(partes)


def _gate_failure_signature(
    gates: list[GateResult],
) -> tuple[tuple[str, ...], ...] | None:
    """Assinatura estável do CONJUNTO de gates que falharam neste turno —
    usada só pra detectar o mesmo bloqueio se repetindo turno após turno,
    nunca pra decidir aprovação."""
    falhas = tuple(tuple(g.command) for g in gates if not g.passed)
    return falhas or None


async def evaluate_goal_with_systemone(
    harness: SystemOneHarness,
    *,
    workspace_id: str,
    goal: str,
    turn_summary: str,
    gate_results: list[GateResult],
) -> tuple[str, bool, str]:
    """Avalia cumprimento do objetivo via SystemOneHarness com evidências verificáveis.

    Retorna `(disposition, completed, reason)` onde `disposition` é:
    - `"completed"`: objetivo satisfeito com evidência suficiente E gates aprovados.
    - `"not_completed"`: objetivo ainda não satisfeito ou gate reprovado.
    - `"ambiguous"`: incerteza ou necessidade de revisão.
    - `"unavailable"`: harness desligado, em modo shadow, ou falha de inferência.
    """
    gates_ok = all(g.passed for g in gate_results)
    gate_summary = [
        {
            "command": " ".join(g.command),
            "passed": g.passed,
            "exit_code": g.exit_code,
        }
        for g in gate_results
    ]
    try:
        res = await harness.evaluate_goal_progress(
            workspace_id=workspace_id,
            goal=goal,
            turn_summary=turn_summary,
            gates_passed=gates_ok,
            gate_results_summary=gate_summary,
        )
    except Exception:
        logger.warning("goal_mode: systemone evaluation failed", exc_info=True)
        return "unavailable", False, "systemone_evaluation_unavailable"

    if res.mode in ("off", "shadow") or res.response is None:
        return "unavailable", False, ",".join(res.reason_codes)

    if res.policy_result.outcome != "accepted" or not res.is_usable_recommendation:
        return "ambiguous", False, res.policy_result.reason

    # Nunca marca conclusão quando um quality gate falhar
    if not gates_ok:
        return "not_completed", False, "quality_gate_failed"

    completed_ans = res.response.answers.get("goal_completed")
    evidence_ans = res.response.answers.get("evidence_sufficient")
    prob_completed = getattr(completed_ans, "probability", 0.0)
    prob_evidence = getattr(evidence_ans, "probability", 0.0)

    disposition, completed, reason = (
        "ambiguous",
        False,
        "systemone_uncertain_probability",
    )
    if prob_completed >= 0.80 and prob_evidence >= 0.80:
        disposition, completed, reason = (
            "completed",
            True,
            f"systemone_verified(p_goal={prob_completed:.2f}, p_evidence={prob_evidence:.2f})",
        )
    elif prob_completed <= 0.30 or prob_evidence <= 0.30:
        disposition, completed, reason = (
            "not_completed",
            False,
            f"systemone_incomplete(p_goal={prob_completed:.2f}, p_evidence={prob_evidence:.2f})",
        )
    return disposition, completed, reason


async def _run_goal_loop(
    *,
    session_store: SessionStore,
    chat_client: ChatClient,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    thread_id: str,
    goal: str,
    loop_config: LoopConfig,
    quality_gates: list[list[str]] | None,
    max_goal_turns: int,
    should_require_approval: Callable[..., bool] | None,
    approval_gate: ApprovalGate | None,
    systemone_harness: SystemOneHarness | None = None,
) -> GoalOutcome:
    """Executa a operação run goal loop."""
    consecutive_judge_failures = 0
    gate_failure_streak = 0
    last_gate_signature: tuple[tuple[str, ...], ...] | None = None

    for turno in range(1, max_goal_turns + 1):
        result = await run_conversation(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=tool_registry,
            ctx=ctx,
            thread_id=thread_id,
            config=loop_config,
            should_require_approval=should_require_approval,
            approval_gate=approval_gate,
        )

        if result.stopped_reason == "interrupted":
            return GoalOutcome(
                status="interrupted",
                reason="Aguardando aprovação humana.",
                turns_used=turno,
                final_message=result.final_message,
            )
        if result.stopped_reason != "stop":
            return GoalOutcome(
                status="error",
                reason=f"turno encerrado por '{result.stopped_reason}' antes de "
                "avaliar o objetivo",
                turns_used=turno,
                final_message=result.final_message,
            )

        turn_summary = result.final_message.text() if result.final_message else ""

        gate_results: list[GateResult] = []
        gates_ok = True
        if quality_gates:
            gate_results = await _run_quality_gates(quality_gates)
            gates_ok = all(g.passed for g in gate_results)
            signature = _gate_failure_signature(gate_results)
            if signature is not None and signature == last_gate_signature:
                gate_failure_streak += 1
            else:
                gate_failure_streak = 1 if signature is not None else 0
            last_gate_signature = signature
            if gate_failure_streak >= _SAME_GATE_FAILURE_THRESHOLD:
                return GoalOutcome(
                    status="error",
                    reason="mesmo gate de qualidade falhou "
                    f"{gate_failure_streak}x seguidas — pausa automática",
                    turns_used=turno,
                    final_message=result.final_message,
                )

        s1_disposition = "unavailable"
        s1_completed = False
        s1_reason = ""
        if systemone_harness is not None:
            (
                s1_disposition,
                s1_completed,
                s1_reason,
            ) = await evaluate_goal_with_systemone(
                systemone_harness,
                workspace_id=getattr(ctx, "workspace_id", "default") or "default",
                goal=goal,
                turn_summary=turn_summary,
                gate_results=gate_results,
            )

        if s1_disposition in ("completed", "not_completed"):
            cumprido = s1_completed and gates_ok
            motivo_judge = s1_reason
            consecutive_judge_failures = 0
        else:
            try:
                cumprido, motivo_judge = await _judge_goal(
                    chat_client, goal, turn_summary
                )
                consecutive_judge_failures = 0
            except Exception as exc:
                consecutive_judge_failures += 1
                logger.warning(
                    "goal_mode: judge falhou (turno %s)", turno, exc_info=True
                )
                if consecutive_judge_failures >= _CONSECUTIVE_JUDGE_FAILURE_THRESHOLD:
                    return GoalOutcome(
                        status="error",
                        reason=f"judge falhou {consecutive_judge_failures}x seguidas "
                        f"(transporte/parse) — pausa automática: {exc}",
                        turns_used=turno,
                        final_message=result.final_message,
                    )
                cumprido, motivo_judge = False, f"judge indisponível neste turno: {exc}"

        if cumprido and gates_ok:
            return GoalOutcome(
                status="done",
                reason=motivo_judge or "objetivo cumprido",
                turns_used=turno,
                final_message=result.final_message,
            )

        continuation = _continuation_message(goal, motivo_judge, gate_results)
        parent_id = await session_store.get_branch_head_id(thread_id)
        await session_store.append_message(
            thread_id,
            text_message(MessageRole.USER, continuation),
            parent_message_id=parent_id,
        )

    return GoalOutcome(
        status="error",
        reason=f"turn budget esgotado ({max_goal_turns} turnos) sem cumprir o objetivo",
        turns_used=max_goal_turns,
    )


async def run_goal(
    *,
    session_store: SessionStore,
    chat_client: ChatClient,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    thread_id: str,
    goal: str,
    loop_config: LoopConfig,
    quality_gates: list[list[str]] | None = None,
    max_goal_turns: int = 20,
    should_require_approval: Callable[..., bool] | None,
    approval_gate: ApprovalGate | None = None,
    systemone_harness: SystemOneHarness | None = None,
) -> GoalOutcome:
    """Encadeia turnos de ``run_conversation`` até o objetivo ser cumprido
    (gates + judge, AND independente) ou o turn budget/guardrails de falha
    repetida esgotarem — ver docstring do módulo.

    Defensiva no nível do orquestrador: qualquer exceção não prevista pelos
    guardrails internos (``_run_goal_loop``) vira ``GoalOutcome(status=
    "error")``, nunca propaga pro caller (mesma regra de tools/loops do
    projeto).

    ``should_require_approval`` é obrigatório (sem default) de propósito:
    desligar HITL é uma escolha que precisa ser explícita no call site
    (``should_require_approval=None``), nunca um esquecimento silencioso.
    """
    try:
        return await _run_goal_loop(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=tool_registry,
            ctx=ctx,
            thread_id=thread_id,
            goal=goal,
            loop_config=loop_config,
            quality_gates=quality_gates,
            max_goal_turns=max_goal_turns,
            should_require_approval=should_require_approval,
            approval_gate=approval_gate,
            systemone_harness=systemone_harness,
        )
    except Exception as exc:
        logger.exception("goal_mode: run_goal falhou de forma inesperada")
        return GoalOutcome(
            status="error", reason=f"erro inesperado: {exc}", turns_used=0
        )


async def resume_goal(
    *,
    session_store: SessionStore,
    chat_client: ChatClient,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    thread_id: str,
    goal: str,
    loop_config: LoopConfig,
    decision: str,
    edited_args: dict[str, Any] | None = None,
    quality_gates: list[list[str]] | None = None,
    max_goal_turns: int = 20,
    should_require_approval: Callable[..., bool] | None,
    approval_gate: ApprovalGate | None = None,
    systemone_harness: SystemOneHarness | None = None,
) -> GoalOutcome:
    """Resolve a pendência HITL do turno pausado (``resume_conversation``) e
    reentra no goal loop pelos turnos restantes — diferente de retomar só o
    turno isolado, o objetivo continua sendo perseguido (gates + judge) até
    ``max_goal_turns`` ou um novo HITL.

    Sem pendência real (duplo-clique/retry), devolve ``status="error"``
    imediatamente — mesmo shape defensivo de ``run_goal``, nunca propaga.
    """
    try:
        resumed = await resume_conversation(
            session_store=session_store,
            tool_registry=tool_registry,
            ctx=ctx,
            thread_id=thread_id,
            decision=decision,
            edited_args=edited_args,
            approval_gate=approval_gate,
        )
    except Exception as exc:
        logger.exception("goal_mode: resume_conversation falhou")
        return GoalOutcome(
            status="error", reason=f"falha ao retomar aprovação: {exc}", turns_used=0
        )
    if not resumed:
        return GoalOutcome(
            status="error",
            reason="nenhuma aprovação pendente para retomar",
            turns_used=0,
        )

    return await run_goal(
        session_store=session_store,
        chat_client=chat_client,
        tool_registry=tool_registry,
        ctx=ctx,
        thread_id=thread_id,
        goal=goal,
        loop_config=loop_config,
        quality_gates=quality_gates,
        max_goal_turns=max_goal_turns,
        should_require_approval=should_require_approval,
        approval_gate=approval_gate,
        systemone_harness=systemone_harness,
    )
