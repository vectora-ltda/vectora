"""Delegação de subagentes nativa. Cada subagente é uma instância nova do
motor nativo (``run_conversation``) rodando com seu próprio ``SessionStore``
thread, tools restritas ao ``SubagentSpec``, e prompt de sistema próprio —
``asyncio.Task``, nunca thread OS.

``SubagentSpec.tools`` já são ``ToolSpec`` do registry nativo. ``backend/
agents/souls.py`` (catálogo real em produção, ``SOUL_CATALOG``) consome as
mesmas tools nativas por baixo, envolvidas em adapter compatível — a fonte
de verdade em produção continua sendo ``SOUL_CATALOG`` até o dispatch
cortar pro motor novo; este módulo entrega o MECANISMO de delegação
nativo, testável com qualquer ``ToolRegistry``, coexistindo sem depender
de um catálogo próprio ainda ligado ao dispatch.

Sub-thread_id = ``f"{parent_thread_id}:{spec.name}:{uuid4()}"`` com
``parent_thread_id`` gravado em ``SessionStore.create_session`` — dá
rastreabilidade completa (qualquer subagente sabe de qual conversa/task
veio).

HITL dentro do subagente: ``should_require_approval`` (``backend/engine/
hitl.py``) é passado direto pro `run_conversation` do subagente — chamado
IDENTICAMENTE ao do agente principal, porque é código importado, não
estado injetado por instância de middleware. Isso garante que a política
de aprovação do pai sempre se propaga ao subagente, sem truque nenhum.

Liveness ativa (``LivenessConfig``): heartbeat baseado em progresso real
(qualquer evento emitido pelo `run_conversation` do subagente — token,
tool_call, etc.), não só existência do processo. Sem atividade por
``heartbeat_interval_s * max_stalled_heartbeats`` segundos, o watchdog
cancela a `asyncio.Task` do subagente — nunca deixa um loop preso rodando
pra sempre. Pausa legítima em HITL não conta como "travado": o
`run_conversation` retorna (task termina) ao pausar, então o watchdog
nunca chega a competir com uma delegação esperando aprovação de verdade.

Escopo RBAC do subagente (``_tools_outside_user_scope``): as tools
do ``SubagentSpec`` nunca podem exceder o que ``tool_policy.
effective_disabled(ctx.user_id)`` permite pro usuário/sessão que está
delegando — mesmo filtro que ``agent_factory._subagent_specs()`` já aplica
no catálogo em produção (kill-switch global + ABAC por usuário), replicado
aqui pro motor nativo não abrir uma segunda porta sem esse filtro.

Dedup por ``correlation_id`` (``SubagentSpec.correlation_id``): delegações
concorrentes com o mesmo ``correlation_id`` REAPROVEITAM a execução já em
andamento em vez de duplicar — a primeira chamada a alcançar
``run_subagent`` registra um ``asyncio.Future`` em
``_IN_FLIGHT_BY_CORRELATION`` antes de qualquer ``await`` (sem ponto de
troca de contexto entre o check e o registro, então não há corrida real
mesmo com duas chamadas concorrentes via ``asyncio.gather``); qualquer
chamada seguinte com o mesmo id só espera esse ``Future`` em vez de gastar
sessão/turn budget/chamada ao chat client de novo. Reaproveitar (em vez de
rejeitar) foi a escolha porque o caso de uso real é retry/race do próprio
LLM (mesma intenção reemitida), não dois pedidos legitimamente distintos
que colidiram por acaso no id.

Interrupção real (``request_hard_interrupt``): cada ``conversation_task``
de um subagente delegado com ``correlation_id`` fica registrada em
``_ACTIVE_CONVERSATION_TASKS`` enquanto roda. ``request_hard_interrupt``
exige um capability token HMAC (``subagent_capability_token`` — mesma
chave de assinatura de sessão que ``backend/tools/background.py::
_capability_token`` já usa) e, se válido, chama ``Task.cancel()`` de
verdade na task em execução — não uma flag checada em algum loop
periódico. Distinto do watchdog de liveness (``_watch_liveness``, ainda
só timeout de inatividade): aqui a cancelação é imediata e sob pedido
explícito de quem tem o token, a qualquer momento da execução.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from backend.engine.conversation_loop import LoopConfig, run_conversation
from backend.engine.stream_events import MessageChunk, SubagentOutput
from backend.rbac import tool_policy
from backend.tools.registry import ToolRegistry
from backend.vtypes.ids import CapabilityToken, CorrelationId
from backend.vtypes.message import MessageRole, text_message

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.engine.guardrails import TurnBudget
    from backend.engine.stream_events import EventSink
    from backend.llm.base import ChatClient
    from backend.persistence.native.session_store import SessionStore
    from backend.tools.context import ToolContext
    from backend.tools.registry import ToolSpec
    from backend.vtypes.message import VMessage


@dataclass(slots=True)
class SubagentSpec:
    """Spec nativa de subagente — mesmo papel de `Soul`
    (`backend/agents/souls.py`), tools já resolvidas como `ToolSpec`."""

    name: str
    description: str
    system_prompt: str
    tools: list[ToolSpec]
    correlation_id: CorrelationId | None = field(default=None)
    """Identificador opcional da intenção de delegação. Duas chamadas com o
    mesmo valor reaproveitam a mesma execução (`_IN_FLIGHT_BY_CORRELATION`)
    em vez de rodar o subagente duas vezes, e habilitam
    `request_hard_interrupt(correlation_id, token)` a cancelar essa
    execução especificamente."""


@dataclass(slots=True)
class LivenessConfig:
    """Teto de inatividade antes do watchdog cancelar o subagente.

    O watchdog só existe quando `run_subagent` recebe `liveness=` — sem
    isso (default), o subagente roda até completar sem nenhum teto de
    tempo, comportamento inalterado."""

    heartbeat_interval_s: float = 30.0
    max_stalled_heartbeats: int = 3


_IN_FLIGHT_BY_CORRELATION: dict[CorrelationId, asyncio.Future[str]] = {}
"""Delegações em andamento por `correlation_id` — chave só existe entre o
início e o fim de `run_subagent`; usada pra reaproveitar (nunca duplicar)
uma delegação concorrente com o mesmo id."""

_ACTIVE_CONVERSATION_TASKS: dict[CorrelationId, asyncio.Task[Any]] = {}
"""`conversation_task` de cada subagente em execução com `correlation_id`
definido — só existe enquanto a task roda; é o alvo real de
`request_hard_interrupt`."""


def subagent_capability_token(correlation_id: CorrelationId) -> CapabilityToken:
    """HMAC(secret, correlation_id) — mesma chave de assinatura de sessão
    que ``backend/tools/background.py::_capability_token`` já usa (auto-
    gerada por instalação via ``backend.rbac.auth._get_secret``, sempre
    disponível mesmo sem VECTORA_TOKEN/Pro configurado). Recomputável a
    qualquer momento a partir só do ``correlation_id`` — não precisa ser
    persistido."""
    from backend.rbac.auth import _get_secret

    return CapabilityToken(
        hmac.new(
            _get_secret().encode(), correlation_id.encode(), hashlib.sha256
        ).hexdigest()
    )


def request_hard_interrupt(
    correlation_id: CorrelationId, capability_token: CapabilityToken
) -> bool:
    """Cancela DE VERDADE (``asyncio.Task.cancel()``) a execução do
    subagente associado a ``correlation_id``, se ``capability_token`` bater
    com ``subagent_capability_token(correlation_id)``.

    Distinto do watchdog de liveness (``_watch_liveness``): aquele só
    dispara por timeout de inatividade; este cancela imediatamente sob
    pedido explícito de quem tem o token, a qualquer momento da execução.

    Devolve ``False`` tanto para token inválido quanto para nenhum
    subagente em execução com esse id (já terminou ou nunca existiu) — o
    mesmo shape de retorno pros dois casos não vaza qual dos dois
    aconteceu pra quem não tem autorização."""
    if not hmac.compare_digest(
        subagent_capability_token(correlation_id), capability_token or ""
    ):
        return False
    task = _ACTIVE_CONVERSATION_TASKS.get(correlation_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def _sub_thread_id(parent_thread_id: str, subagent_name: str) -> str:
    return f"{parent_thread_id}:{subagent_name}:{uuid4()}"


def _tools_outside_user_scope(tools: list[ToolSpec], user_id: str | None) -> list[str]:
    """Nomes de tool do `SubagentSpec` que o usuário não teria permissão de
    usar diretamente (kill-switch global ou ABAC por usuário) — mesmo
    filtro que `agent_factory._subagent_specs()` já aplica em produção."""
    disabled = tool_policy.effective_disabled(user_id)
    return [t.name for t in tools if t.name in disabled]


async def _watch_liveness(
    task: asyncio.Task[Any],
    liveness: LivenessConfig,
    last_activity: list[float],
) -> None:
    """Corre em paralelo a `task` — dispara (retorna) quando não há
    atividade registrada em `last_activity[0]` por tempo suficiente. Só
    checa enquanto `task` ainda está rodando; termina sozinho se `task`
    concluir primeiro (nada a cancelar)."""
    limite = liveness.heartbeat_interval_s * liveness.max_stalled_heartbeats
    while not task.done():
        await asyncio.sleep(liveness.heartbeat_interval_s)
        if task.done():
            return
        if time.monotonic() - last_activity[0] >= limite:
            return


async def run_subagent(
    spec: SubagentSpec,
    prompt: str,
    *,
    session_store: SessionStore,
    chat_client: ChatClient,
    ctx: ToolContext,
    parent_thread_id: str,
    config: LoopConfig | None = None,
    on_event: EventSink | None = None,
    should_require_approval: Callable[
        [str, ToolContext, dict[str, Any], list[VMessage]], bool
    ]
    | None,
    turn_budget: TurnBudget | None = None,
    liveness: LivenessConfig | None = None,
) -> str:
    """Roda `spec` até completar (ou pausar em HITL) e devolve o texto
    final. Sem `spec.correlation_id`, delega direto pra
    `_run_subagent_once`. Com `spec.correlation_id`, deduplica: a primeira
    chamada regista um `Future` em `_IN_FLIGHT_BY_CORRELATION` e roda
    normalmente; qualquer chamada concorrente com o mesmo id só espera
    esse `Future` em vez de rodar `_run_subagent_once` de novo — nenhum
    ponto de `await` entre o check e o registro, então duas chamadas via
    `asyncio.gather` nunca duplicam."""
    if not spec.correlation_id:
        return await _run_subagent_once(
            spec,
            prompt,
            session_store=session_store,
            chat_client=chat_client,
            ctx=ctx,
            parent_thread_id=parent_thread_id,
            config=config,
            on_event=on_event,
            should_require_approval=should_require_approval,
            turn_budget=turn_budget,
            liveness=liveness,
        )

    correlation_id = spec.correlation_id
    existente = _IN_FLIGHT_BY_CORRELATION.get(correlation_id)
    if existente is not None:
        return await existente

    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    _IN_FLIGHT_BY_CORRELATION[correlation_id] = future
    try:
        resultado = await _run_subagent_once(
            spec,
            prompt,
            session_store=session_store,
            chat_client=chat_client,
            ctx=ctx,
            parent_thread_id=parent_thread_id,
            config=config,
            on_event=on_event,
            should_require_approval=should_require_approval,
            turn_budget=turn_budget,
            liveness=liveness,
        )
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
        raise
    else:
        if not future.done():
            future.set_result(resultado)
        return resultado
    finally:
        _IN_FLIGHT_BY_CORRELATION.pop(correlation_id, None)


async def _run_subagent_once(
    spec: SubagentSpec,
    prompt: str,
    *,
    session_store: SessionStore,
    chat_client: ChatClient,
    ctx: ToolContext,
    parent_thread_id: str,
    config: LoopConfig | None = None,
    on_event: EventSink | None = None,
    should_require_approval: Callable[
        [str, ToolContext, dict[str, Any], list[VMessage]], bool
    ]
    | None = None,
    turn_budget: TurnBudget | None = None,
    liveness: LivenessConfig | None = None,
) -> str:
    """Roda `spec` uma única vez até completar (ou pausar em HITL) e
    devolve o texto final — instância nova e isolada do motor, sessão
    própria com `parent_thread_id` gravado (rastreabilidade), sem herdar o
    histórico do chamador.

    `turn_budget`, quando fornecido, é o mesmo objeto do turno do agente
    pai (`backend/engine/guardrails.py::TurnBudget`) — o spawn é registrado
    contra o teto `max_subagent_spawns_per_turn` do turno pai antes de
    qualquer sessão ser criada; teto excedido recusa o spawn sem gastar
    nenhum recurso.

    `liveness`, quando fornecido, ativa o watchdog de inatividade — sem
    progresso (nenhum evento emitido pelo subagente) por
    `heartbeat_interval_s * max_stalled_heartbeats` segundos, a task é
    cancelada e o resultado vira `status="cancelled"`. Sem `liveness`
    (default), o subagente roda até completar normalmente, sem watchdog.

    Se `spec.correlation_id` estiver definido, a `conversation_task` fica
    registrada em `_ACTIVE_CONVERSATION_TASKS` enquanto roda — alvo de
    `request_hard_interrupt`, que cancela essa task de verdade a qualquer
    momento, distinto do watchdog de liveness (só timeout de inatividade).

    Erro/borda: `spec.tools` pedindo tool fora do escopo RBAC do usuário
    (`ctx.user_id`, via `backend.rbac.tool_policy.effective_disabled`) é
    rejeitado antes de qualquer sessão ser criada — erro tipado, nenhuma
    chamada ao chat client."""
    fora_do_escopo = _tools_outside_user_scope(spec.tools, ctx.user_id)
    if fora_do_escopo:
        return (
            f"Error: subagente '{spec.name}' pede tool(s) fora do escopo "
            f"RBAC do usuário atual: {', '.join(sorted(fora_do_escopo))}."
        )

    if turn_budget is not None:
        estourado = turn_budget.record_subagent_spawn()
        if estourado is not None:
            return (
                f"Error: teto de guardrail do turno excedido ({estourado}) — "
                f"subagente '{spec.name}' não foi disparado."
            )

    thread_id = _sub_thread_id(parent_thread_id, spec.name)
    await session_store.create_session(
        thread_id,
        user_id=ctx.user_id,
        workspace_id=ctx.workspace_id or None,
        parent_thread_id=parent_thread_id,
        mode="subagent",
        permission_mode=ctx.permission_mode,
    )

    system_id = await session_store.append_message(
        thread_id, text_message(MessageRole.SYSTEM, spec.system_prompt)
    )
    await session_store.append_message(
        thread_id,
        text_message(MessageRole.USER, prompt),
        parent_message_id=system_id,
    )

    sub_registry = ToolRegistry()
    for tool_spec in spec.tools:
        sub_registry.register(tool_spec)

    sub_ctx = replace(ctx, thread_id=thread_id)

    if on_event is not None:
        await on_event(
            SubagentOutput(
                subagent_type=spec.name,
                description=spec.description,
                status="running",
                tool_call_id=ctx.tool_call_id,
            )
        )

    last_activity = [time.monotonic()]

    async def _on_event_com_liveness(event: Any) -> None:
        last_activity[0] = time.monotonic()
        if on_event is not None:
            # O output textual do subagente pertence ao card da delegação na
            # thread pai. Eventos internos de tool não devem virar ações da
            # conversa pública, e os tokens não devem ser misturados à
            # resposta do orquestrador.
            if isinstance(event, MessageChunk):
                await on_event(
                    SubagentOutput(
                        subagent_type=spec.name,
                        description=spec.description,
                        status="running",
                        tool_call_id=ctx.tool_call_id,
                        content=event.content,
                        is_delta=True,
                    )
                )

    conversation_task: asyncio.Task[Any] = asyncio.create_task(
        run_conversation(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=sub_registry,
            ctx=sub_ctx,
            thread_id=thread_id,
            config=config or LoopConfig(),
            on_event=_on_event_com_liveness,
            should_require_approval=should_require_approval,
        )
    )

    if spec.correlation_id:
        _ACTIVE_CONVERSATION_TASKS[spec.correlation_id] = conversation_task

    try:
        if liveness is not None:
            watchdog_task = asyncio.create_task(
                _watch_liveness(conversation_task, liveness, last_activity)
            )
            await asyncio.wait(
                {conversation_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if not conversation_task.done():
                conversation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await conversation_task
                texto_cancelado = (
                    f"Subagente '{spec.name}' cancelado por inatividade — sem "
                    f"progresso por "
                    f"{liveness.heartbeat_interval_s * liveness.max_stalled_heartbeats:.0f}s."
                )
                if on_event is not None:
                    await on_event(
                        SubagentOutput(
                            subagent_type=spec.name,
                            status="cancelled",
                            tool_call_id=ctx.tool_call_id,
                            content=texto_cancelado,
                        )
                    )
                return texto_cancelado
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task

        try:
            resultado = await conversation_task
        except asyncio.CancelledError:
            # `request_hard_interrupt` cancelou a `conversation_task` de
            # verdade (`Task.cancel()`) — distinto do ramo de liveness
            # acima, que já consome a `CancelledError` sozinho ao cancelar
            # por inatividade. Chegar aqui significa cancelamento sob
            # pedido explícito, a qualquer momento da execução.
            texto_cancelado = (
                f"Subagente '{spec.name}' cancelado por pedido explícito "
                f"(hard interrupt)."
            )
            if on_event is not None:
                await on_event(
                    SubagentOutput(
                        subagent_type=spec.name,
                        status="cancelled",
                        tool_call_id=ctx.tool_call_id,
                        content=texto_cancelado,
                    )
                )
            return texto_cancelado
    finally:
        if spec.correlation_id:
            _ACTIVE_CONVERSATION_TASKS.pop(spec.correlation_id, None)

    texto = resultado.final_message.text() if resultado.final_message else ""

    if resultado.stopped_reason == "stop":
        status = "complete"
    elif resultado.stopped_reason == "interrupted":
        # Ainda pausado esperando aprovação — nem sucesso nem erro. Não
        # emite evento de conclusão: o subagente segue "running" até
        # alguém retomar (a UI vê a pendência via o mesmo pending_approvals
        # do agente principal).
        status = "running"
    else:
        status = "error"

    if status != "running" and on_event is not None:
        await on_event(
            SubagentOutput(
                subagent_type=spec.name,
                status=status,
                tool_call_id=ctx.tool_call_id,
                content=texto,
            )
        )

    return texto


def spawn_subagent_background(
    spec: SubagentSpec, prompt: str, **kwargs: Any
) -> asyncio.Task[str]:
    """Dispara `run_subagent` em segundo plano via `asyncio.create_task` —
    nunca thread OS. Devolve a `asyncio.Task[str]` pra
    quem chamou decidir se/quando esperar (ex.: `await` direto pra
    delegação síncrona, ou nunca esperar pra fire-and-forget real)."""
    return asyncio.create_task(run_subagent(spec, prompt, **kwargs))
