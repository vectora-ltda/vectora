"""``run_conversation`` — loop de conversa nativo, estilo Hermes Agent:
um loop ``while`` imperativo que a cada volta relê o histórico da
persistência (nunca mantém estado só em memória — reload/resume funcionam
por reconstrução, mesmo invariante de ``SessionStore``), chama o chat
client em streaming, acumula os fragmentos, executa as tool calls
resultantes, e repete.

``max_iterations`` é o teto de voltas do loop; estourar emite
``stopped_reason="max_iterations"`` — mesmo código que o frontend já trata
hoje via `ErrorSignal(code="RECURSION_LIMIT")`.

HITL entra por injeção: ``should_require_approval`` (``backend/engine/
hitl.py``) é uma função pura opcional — se fornecida e disparar pra
qualquer tool call do lote, o loop pausa ali (``stopped_reason=
"interrupted"``) como controle normal, sem executar nenhuma tool do lote.
Sem a função, o loop nunca pausa. Quando um ``ApprovalGate`` é passado, a
aprovação pendente é persistida SINCRONAMENTE (``SessionStore.
put_pending_approval``) antes do loop retornar — sobrevive a restart do
backend, porque o estado nunca fica só na call stack deste `await`.

Eventos emitidos via ``on_event`` (``backend/engine/stream_events.py``)
são os mesmos tipos que ``sse_adapter.py`` serializa pro contrato SSE que
o frontend já consome — o loop não sabe nada sobre SSE, só produz o
vocabulário nativo.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from backend.engine.guardrails import LoopCapConfig, TurnBudget
from backend.engine.stream_events import (
    ErrorSignal,
    HitlRequested,
    MessageBreak,
    MessageChunk,
    TodoItem,
    TodosUpdated,
    ToolActivity,
    ToolCallStarted,
    ToolResult,
    WorkbenchInvalidate,
)
from backend.engine.tool_batch import execute_tool_batch
from backend.vtypes.message import ContentBlock, MessageRole, ToolCall, VMessage

_REPEATED_CALL_THRESHOLD = 3
"""Quantas chamadas idênticas seguidas (mesma tool, mesmos args) disparam o
aviso de possível loop preso — inspirado na detecção de repetição do
hermes-agent (`agent/tool_guardrails.py`), sem a classificação
idempotente/mutante dele: aqui é só sinal, o LLM/HITL decide o resto."""

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.engine.hitl import ApprovalGate
    from backend.engine.stream_events import EventSink
    from backend.llm.base import ChatClient
    from backend.persistence.native.session_store import SessionStore
    from backend.tools.context import ToolContext
    from backend.tools.registry import ToolRegistry


@dataclass(slots=True)
class LoopConfig:
    """Configuração de uma execução do loop — um objeto por turno."""

    max_iterations: int = 50
    temperature: float | None = None
    max_tokens: int | None = None
    loop_caps: LoopCapConfig = field(default_factory=LoopCapConfig)
    """Tetos de volume por turno (`backend/engine/guardrails.py`) —
    distintos de `max_iterations` (teto de voltas do loop): aqui é volume
    de tool calls/subagentes/AITL, não repetição nem número de idas e
    vindas ao chat client."""


@dataclass(slots=True)
class LoopResult:
    """Resultado de uma chamada a `run_conversation`."""

    stopped_reason: str
    """`"stop"` | `"max_iterations"` | `"interrupted"` | `"loop_cap_exceeded"`."""
    final_message: VMessage | None = None


async def _noop_event(_event: object) -> None:
    return None


_ARGS_PREVIEW_MAX_CHARS = 80
_ARGS_PREVIEW_SEMANTIC_KEYS = ("path", "file_path", "query", "command", "url", "name")


def _args_preview(args: dict[str, Any]) -> str:
    """Preview curto (≤80 chars) dos args de uma tool call, pra exibir na
    linha de status do agente (AgentStatusLine) enquanto a tool roda —
    prioriza campos semânticos comuns, com fallback pros 2 primeiros
    campos do dict."""
    for chave in _ARGS_PREVIEW_SEMANTIC_KEYS:
        valor = args.get(chave)
        if valor and isinstance(valor, str):
            preview = valor
            break
    else:
        preview = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])
    return preview[:_ARGS_PREVIEW_MAX_CHARS]


_WRITE_TODOS_TOOL_NAME = "write_todos"


def _parse_todos_result(texto: str) -> list[TodoItem] | None:
    """Traduz o JSON devolvido por ``write_todos`` (``backend/tools/
    planning.py``) em ``TodoItem`` — ``None`` se o resultado não tiver o
    shape esperado (defensivo: nunca propaga, só deixa de emitir)."""
    try:
        bruto = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(bruto, list):
        return None
    itens: list[TodoItem] = []
    for item in bruto:
        if not isinstance(item, dict):
            return None
        status = item.get("status")
        if status not in ("pending", "in_progress", "completed"):
            return None
        itens.append(TodoItem(content=str(item.get("content", "")), status=status))
    return itens


def _call_signature(tool_call: ToolCall) -> tuple[str, str]:
    """Assinatura estável de uma tool call — nome + args normalizados
    (chaves ordenadas), usada só pra detectar repetição, nunca pra
    execução."""
    return (tool_call.name, json.dumps(tool_call.args, sort_keys=True))


def _resolve_tool_calls(acumulado: dict[int, dict[str, Any]]) -> list[ToolCall]:
    """Monta `ToolCall`s completas a partir dos fragmentos acumulados por
    `index` — `arguments` inválido não derruba o turno, vira `_parse_error`
    (mesmo padrão já usado nos 5 chat clients nativos)."""
    chamadas: list[ToolCall] = []
    for indice in sorted(acumulado):
        item = acumulado[indice]
        args_texto = item["args_fragment"] or "{}"
        try:
            args = json.loads(args_texto)
        except json.JSONDecodeError:
            args = {"_parse_error": args_texto}
        if not isinstance(args, dict):
            args = {"_parse_error": args_texto}
        chamadas.append(
            ToolCall(id=item["id"] or "", name=item["name"] or "", args=args)
        )
    return chamadas


async def run_conversation(
    *,
    session_store: SessionStore,
    chat_client: ChatClient,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    thread_id: str,
    config: LoopConfig,
    on_event: EventSink | None = None,
    should_require_approval: Callable[
        [str, ToolContext, dict[str, Any], list[VMessage]], bool
    ]
    | None = None,
    approval_gate: ApprovalGate | None = None,
) -> LoopResult:
    emit = on_event or _noop_event
    tools = tool_registry.all()
    parent_id = await session_store.get_branch_head_id(thread_id)
    assinaturas_anteriores: frozenset[tuple[str, str]] | None = None
    repeticoes_seguidas = 0
    turn_budget = TurnBudget(config=config.loop_caps)

    for _iteracao in range(config.max_iterations):
        historico = await session_store.get_history(thread_id)

        partes_texto: list[str] = []
        tool_call_chunks_por_indice: dict[int, dict[str, Any]] = {}

        async for chunk in chat_client.astream(
            historico,
            tools=tools,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        ):
            if chunk.delta_text:
                partes_texto.append(chunk.delta_text)
                await emit(MessageChunk(content=chunk.delta_text))

            for tc_chunk in chunk.tool_call_chunks:
                acumulado = tool_call_chunks_por_indice.setdefault(
                    tc_chunk.index, {"id": None, "name": None, "args_fragment": ""}
                )
                if tc_chunk.id:
                    acumulado["id"] = tc_chunk.id
                if tc_chunk.name:
                    acumulado["name"] = tc_chunk.name
                acumulado["args_fragment"] += tc_chunk.args_fragment

            # `usage` não tem evento SSE dedicado — rastreio de custo lê
            # `VMessageChunk.usage` diretamente do stream do chat client,
            # não via `on_event`.

        texto_final = "".join(partes_texto)
        tool_calls = _resolve_tool_calls(tool_call_chunks_por_indice)

        assistant_msg = VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text=texto_final)]
            if texto_final
            else [],
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
        )
        parent_id = await session_store.append_message(
            thread_id, assistant_msg, parent_message_id=parent_id
        )
        await emit(MessageBreak())

        if not tool_calls:
            return LoopResult(stopped_reason="stop", final_message=assistant_msg)

        assinaturas_atual = frozenset(_call_signature(tc) for tc in tool_calls)
        if assinaturas_atual == assinaturas_anteriores:
            repeticoes_seguidas += 1
        else:
            repeticoes_seguidas = 1
        assinaturas_anteriores = assinaturas_atual
        if repeticoes_seguidas == _REPEATED_CALL_THRESHOLD:
            nomes = ", ".join(sorted({tc.name for tc in tool_calls}))
            await emit(
                ErrorSignal(
                    code="TOOL_CALL_REPEATED",
                    message=(
                        f"Mesma tool call ({nomes}) repetida "
                        f"{_REPEATED_CALL_THRESHOLD}x seguidas com argumentos "
                        "idênticos — possível loop preso."
                    ),
                )
            )

        if should_require_approval is not None:
            pendente = next(
                (
                    tc
                    for tc in tool_calls
                    if should_require_approval(tc.name, ctx, tc.args, historico)
                ),
                None,
            )
            if pendente is not None:
                interrupt_id = str(uuid4())
                args_json = json.dumps(pendente.args, ensure_ascii=False)
                if approval_gate is not None:
                    await approval_gate.request_approval(
                        thread_id,
                        interrupt_id=interrupt_id,
                        tool_name=pendente.name,
                        tool_call_id=pendente.id,
                        args=pendente.args,
                    )
                await emit(
                    HitlRequested(
                        tool_name=pendente.name,
                        args_json=args_json,
                        interrupt_id=interrupt_id,
                    )
                )
                return LoopResult(
                    stopped_reason="interrupted", final_message=assistant_msg
                )

        tool_started_at: dict[str, float] = {}
        tool_args_previews: dict[str, str] = {}
        for tc in tool_calls:
            spec = tool_registry.get(tc.name)
            extras = spec.extras if spec is not None else None
            tool_started_at[tc.id] = time.monotonic()
            preview = _args_preview(tc.args)
            tool_args_previews[tc.id] = preview
            await emit(
                ToolCallStarted(
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    args_json=json.dumps(tc.args, ensure_ascii=False),
                    render_hint=extras.render_hint if extras else "json",
                    category=extras.category if extras else "general",
                    destructive=extras.destructive if extras else False,
                    icon=extras.icon if extras else "tool",
                )
            )
            await emit(
                ToolActivity(
                    tool_name=tc.name, tool_call_id=tc.id, args_preview=preview
                )
            )

        resultados = await execute_tool_batch(
            tool_calls, tool_registry=tool_registry, ctx=ctx, turn_budget=turn_budget
        )
        for resultado in resultados:
            parent_id = await session_store.append_message(
                thread_id, resultado, parent_message_id=parent_id
            )
            call_id = resultado.tool_call_id or ""
            await emit(
                ToolResult(
                    tool_call_id=call_id,
                    content_json=resultado.text(),
                    is_error=resultado.is_error,
                )
            )
            started_at = tool_started_at.pop(call_id, None)
            elapsed_ms = (
                int((time.monotonic() - started_at) * 1000)
                if started_at is not None
                else 0
            )
            await emit(
                ToolActivity(
                    tool_name=resultado.name or "",
                    tool_call_id=call_id,
                    args_preview=tool_args_previews.pop(call_id, ""),
                    elapsed_ms=elapsed_ms,
                )
            )
            spec = tool_registry.get(resultado.name or "")
            if spec is not None and spec.extras.invalidates:
                await emit(
                    WorkbenchInvalidate(
                        tabs=spec.extras.invalidates, tool_name=resultado.name or ""
                    )
                )
            if resultado.name == _WRITE_TODOS_TOOL_NAME and not resultado.is_error:
                todos = _parse_todos_result(resultado.text())
                if todos is not None:
                    await emit(TodosUpdated(todos=todos))

        if turn_budget.exceeded is not None:
            await emit(
                ErrorSignal(
                    code="LOOP_CAP_EXCEEDED",
                    message=(
                        f"Teto de guardrail do turno excedido "
                        f"({turn_budget.exceeded}) — turno encerrado."
                    ),
                )
            )
            return LoopResult(
                stopped_reason="loop_cap_exceeded", final_message=assistant_msg
            )

    await emit(
        ErrorSignal(
            code="RECURSION_LIMIT",
            message=f"Limite de {config.max_iterations} iterações atingido.",
        )
    )
    return LoopResult(stopped_reason="max_iterations")


async def _execute_single_call(
    tool_call: ToolCall, *, tool_registry: ToolRegistry, ctx: ToolContext
) -> VMessage:
    """Mesma lógica de execução de ``tool_batch._run_one``, sem
    ``TurnBudget`` (o teto de volume é do turno que gerou o lote original,
    já contabilizado ou não antes da pausa — reaplicá-lo no resume duplicaria
    ou perderia contagem)."""
    spec = tool_registry.get(tool_call.name)
    if spec is None:
        texto = f"Error: tool '{tool_call.name}' não encontrada no registry"
        is_error = True
    else:
        # Cada chamada recebe seu próprio contexto correlacionado. Isso evita
        # que eventos de duas tools executadas em lote compartilhem o mesmo
        # ID e permite que delegações internas atualizem o card correto.
        texto = await spec.ainvoke(
            tool_call.args, replace(ctx, tool_call_id=tool_call.id)
        )
        is_error = texto.startswith("Error:")
    return VMessage(
        role=MessageRole.TOOL,
        content=[ContentBlock(kind="text", text=texto)],
        tool_call_id=tool_call.id,
        name=tool_call.name,
        is_error=is_error,
    )


async def resume_conversation(
    *,
    session_store: SessionStore,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    thread_id: str,
    decision: str,
    edited_args: dict[str, Any] | None = None,
    approval_gate: ApprovalGate | None = None,
    on_event: EventSink | None = None,
) -> bool:
    """Executa o lote de tool calls que ``run_conversation`` pausou —
    ``decision`` é ``"approve"`` | ``"reject"`` | ``"edit"``, aplicada só à
    tool call sinalizada em ``SessionStore.get_pending_approval`` (as
    demais do MESMO lote, se houver, nunca foram a causa da pausa e
    executam normalmente, como executariam se nenhuma delas exigisse
    aprovação).

    Não continua o loop de conversa sozinho — depois de persistir o(s)
    resultado(s) de tool, o caller chama ``run_conversation`` normalmente
    pra deixar o modelo reagir: a próxima iteração relê o histórico (agora
    com o resultado da tool já presente) e segue dali, sem nenhum código de
    "retomada" especial no loop principal.

    Devolve ``False`` (nenhum efeito) se não havia aprovação pendente para
    ``thread_id`` — resume idempotente diante de um duplo-clique/retry do
    cliente. ``True`` quando o lote foi executado e a pendência resolvida.
    """
    emit = on_event or _noop_event
    pending = await session_store.get_pending_approval(thread_id)
    if pending is None:
        return False

    flagged_id = pending["tool_call_id"]
    historico = await session_store.get_history(thread_id)
    ultimo_assistant = next(
        (
            m
            for m in reversed(historico)
            if m.role == MessageRole.ASSISTANT and m.tool_calls
        ),
        None,
    )
    tool_calls = ultimo_assistant.tool_calls if ultimo_assistant is not None else []

    parent_id = await session_store.get_branch_head_id(thread_id)
    for tc in tool_calls:
        if tc.id != flagged_id:
            resultado = await _execute_single_call(
                tc, tool_registry=tool_registry, ctx=ctx
            )
        elif decision == "reject":
            resultado = VMessage(
                role=MessageRole.TOOL,
                content=[ContentBlock(kind="text", text="Usuário rejeitou esta ação.")],
                tool_call_id=tc.id,
                name=tc.name,
                is_error=True,
            )
        else:
            args = tc.args
            if decision == "edit" and edited_args is not None:
                args = edited_args
            resultado = await _execute_single_call(
                replace(tc, args=args), tool_registry=tool_registry, ctx=ctx
            )

        parent_id = await session_store.append_message(
            thread_id, resultado, parent_message_id=parent_id
        )
        await emit(
            ToolResult(
                tool_call_id=resultado.tool_call_id or "",
                content_json=resultado.text(),
                is_error=resultado.is_error,
            )
        )
        spec = tool_registry.get(resultado.name or "")
        if spec is not None and spec.extras.invalidates:
            await emit(
                WorkbenchInvalidate(
                    tabs=spec.extras.invalidates, tool_name=resultado.name or ""
                )
            )

    if approval_gate is not None:
        await approval_gate.resolve(thread_id)
    else:
        await session_store.clear_pending_approval(thread_id)
    return True
