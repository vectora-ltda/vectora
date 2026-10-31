"""Tool nativa de delegação a subagente — roda inteiramente no motor
nativo (``run_subagent``, ``backend/engine/subagents.py``), sem grafo
compilado.

``run_subagent`` precisa de infraestrutura do turno (``session_store``,
``chat_client``, o catálogo de ``SubagentSpec`` por nome, etc.) que não
cabe nos campos tipados de ``ToolContext`` (são objetos de execução, não
dados de sessão/usuário) — chegam via ``ctx._extra["subagent_deps"]``,
uma instância de ``SubagentDeps`` que quem monta o loop nativo injeta
antes de despachar o turno. Sem essa chave, a tool devolve erro tipado em
vez de ``KeyError``/``AttributeError`` crus (CLAUDE.md regra 11).
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from backend.engine.subagents import SubagentSpec, run_subagent
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.ids import CorrelationId

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.engine.conversation_loop import LoopConfig
    from backend.engine.guardrails import TurnBudget
    from backend.engine.stream_events import EventSink
    from backend.engine.subagents import LivenessConfig
    from backend.llm.base import ChatClient
    from backend.persistence.native.session_store import SessionStore
    from backend.services.policy_compiler import PolicySnapshot
    from backend.vtypes.message import VMessage

logger = logging.getLogger(__name__)

_DIRECT_ONLY_PHRASES = frozenset(
    {
        "oi",
        "olá",
        "ola",
        "hello",
        "hi",
        "bom dia",
        "boa tarde",
        "boa noite",
        "quem é você",
        "quem e voce",
        "se apresente",
        "obrigado",
        "obrigada",
        "ok",
        "sim",
    }
)


def _delegation_is_actionable(prompt: str) -> bool:
    """Evita gastar uma execução de SOUL em conversa ou confirmação vazia.

    A decisão final continua pertencendo ao orchestrator. Esta salvaguarda só
    bloqueia chamadas obviamente triviais, inclusive quando o modelo escolhe
    ``delegate_to_subagent`` por engano.
    """
    normalized = re.sub(r"[^\w\s-]", " ", prompt.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized or normalized in _DIRECT_ONLY_PHRASES:
        return False
    return not any(
        normalized.removeprefix(f"{phrase} ") in _DIRECT_ONLY_PHRASES
        for phrase in _DIRECT_ONLY_PHRASES
    )


class SubagentDeps:
    """Dependências de infraestrutura que ``delegate_to_subagent`` precisa
    pra chamar ``run_subagent`` — uma instância por turno, injetada via
    ``ToolContext._extra["subagent_deps"]`` por quem monta a chamada a
    ``run_conversation`` (não pelo LLM, nunca aparece no schema da tool)."""

    __slots__ = (
        "catalog",
        "chat_client",
        "config",
        "liveness",
        "on_event",
        "policy_snapshot",
        "session_store",
        "should_require_approval",
        "turn_budget",
    )

    def __init__(
        self,
        *,
        catalog: dict[str, SubagentSpec],
        session_store: SessionStore,
        chat_client: ChatClient,
        config: LoopConfig | None = None,
        on_event: EventSink | None = None,
        should_require_approval: Callable[
            [str, ToolContext, dict[str, Any], list[VMessage]], bool
        ]
        | None,
        turn_budget: TurnBudget | None = None,
        liveness: LivenessConfig | None = None,
        policy_snapshot: PolicySnapshot | None = None,
    ) -> None:
        """Inicializa o estado da instância."""
        self.catalog = catalog
        self.session_store = session_store
        self.chat_client = chat_client
        self.config = config
        self.on_event = on_event
        self.should_require_approval = should_require_approval
        self.turn_budget = turn_budget
        self.liveness = liveness
        self.policy_snapshot = policy_snapshot


@vtool(
    extras=ToolExtras(
        destructive=False,
        category="agent",
        icon="users",
    )
)
async def delegate_to_subagent(
    subagent_type: str,
    prompt: str,
    ctx: ToolContext,
    correlation_id: str | None = None,
) -> str:
    """Delega uma tarefa a uma SOUL do catálogo, rodando isolada no motor
    nativo (`run_subagent`).

    Args:
        subagent_type: nome da SOUL no catálogo desta sessão.
        prompt: instrução completa que a SOUL vai executar.
        correlation_id: identificador opcional da intenção de delegação —
            uma segunda chamada com o mesmo valor reaproveita a delegação
            já em andamento em vez de duplicar (protege contra retry/race).

    Returns:
        Texto final do subagente, ou uma mensagem `Error: ...` tipada
        (SOUL inexistente, fora de escopo RBAC, timeout de inatividade,
        falha inesperada) — nunca propaga exceção crua pro loop.
    """
    deps: SubagentDeps | None = ctx._extra.get("subagent_deps")
    if deps is None:
        return (
            "Error: delegate_to_subagent chamada sem dependências de "
            "subagente configuradas (ctx._extra['subagent_deps'])."
        )

    spec = deps.catalog.get(subagent_type)
    if spec is None:
        return (
            f"Error: subagent_type inválido: {subagent_type!r}. "
            f"Válidos: {sorted(deps.catalog)}."
        )
    if not _delegation_is_actionable(prompt):
        logger.info(
            "delegate_to_subagent: delegação ignorada por ser não acionável",
            extra={"subagent_type": subagent_type},
        )
        return (
            "Delegação não necessária para esta solicitação simples. "
            "Responda diretamente ao usuário; não crie outro subagente."
        )
    if correlation_id:
        spec = replace(spec, correlation_id=CorrelationId(correlation_id))

    try:
        return await run_subagent(
            spec,
            prompt,
            session_store=deps.session_store,
            chat_client=deps.chat_client,
            ctx=ctx,
            parent_thread_id=ctx.thread_id,
            config=deps.config,
            on_event=deps.on_event,
            should_require_approval=deps.should_require_approval,
            turn_budget=deps.turn_budget,
            liveness=deps.liveness,
            parent_policy_snapshot=deps.policy_snapshot,
        )
    except Exception as exc:
        logger.exception(
            "delegate_to_subagent: erro inesperado",
            extra={"subagent_type": subagent_type},
        )
        return f"Error: delegação a '{subagent_type}' falhou: {exc}"
