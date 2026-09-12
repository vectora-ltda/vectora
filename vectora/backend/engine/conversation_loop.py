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
from backend.engine.tool_batch import _is_tool_error, execute_tool_batch
from backend.services.context_compaction import compact_messages
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
    context_max_tokens: int | None = None
    context_compaction_enabled: bool = True
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
    usage: dict[str, int] | None = None
    usage_models: tuple[str, ...] = ()
    usage_records: tuple[tuple[str | None, dict[str, int]], ...] = ()
    tool_names: tuple[str, ...] = ()


async def _noop_event(_event: object) -> None:
    return None


_ARGS_PREVIEW_MAX_CHARS = 80
_ARGS_PREVIEW_SEMANTIC_KEYS = ("path", "file_path", "query", "command", "url", "name")


def _sanitize_tool_call(tc: ToolCall, ctx: ToolContext) -> ToolCall:
    """Remove payloads sensíveis das chamadas persistidas no histórico."""
    if tc.name in {"generate_image", "text_to_speech", "generate_video"}:
        from backend.services.media_quota import (
            media_estimate_record,
            new_idempotency_key,
        )

        provider, _, model = ctx.model.partition(":")
        estimate = media_estimate_record(tc.name, provider=provider, model=model)
        try:
            idempotency_key = new_idempotency_key(tc.id or uuid4().hex, tc.name)
        except ValueError:
            idempotency_key = None
        args = {
            "operation": estimate.operation,
            "provider": estimate.provider,
            "model": estimate.model,
            "estimate_version": estimate.version,
            "billable_unit": estimate.billable_unit,
            "currency": estimate.currency,
            "estimated_units": estimate.units,
            "idempotency_key": idempotency_key,
        }
        return replace(tc, args=args)
    if tc.name != "write_terminal" or "input_data" not in tc.args:
        return tc
    args = dict(tc.args)
    raw = str(args.pop("input_data", ""))
    args["input_preview"] = "<redacted>"
    args["input_length"] = len(raw.encode("utf-8"))
    return replace(tc, args=args)


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


def _default_context_tokens() -> int:
    """Read the configured default lazily so loop tests remain isolated."""
    from backend.settings import settings

    return settings.max_context_tokens


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
    last_usage: dict[str, int] | None = None
    usage_models: set[str] = set()
    usage_records: list[tuple[str | None, dict[str, int]]] = []
    observed_tools: set[str] = set()

    for _iteracao in range(config.max_iterations):
        historico = compact_messages(
            await session_store.get_history(thread_id),
            max_tokens=(
                config.context_max_tokens
                if config.context_max_tokens is not None
                else _default_context_tokens()
            ),
            enabled=config.context_compaction_enabled,
        )

        partes_texto: list[str] = []
        tool_call_chunks_por_indice: dict[int, dict[str, Any]] = {}

        stream_usage: dict[str, int] | None = None
        async for chunk in chat_client.astream(
            historico,
            tools=tools,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        ):
            if chunk.usage:
                stream_usage = dict(chunk.usage)
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

        if stream_usage is not None:
            if last_usage is None:
                last_usage = {}
            for key, value in stream_usage.items():
                last_usage[key] = last_usage.get(key, 0) + value
        model_id = getattr(chat_client, "last_model_id", None)
        if model_id:
            usage_models.add(str(model_id))
        if stream_usage is not None:
            usage_records.append((str(model_id) if model_id else None, stream_usage))

        texto_final = "".join(partes_texto)
        tool_calls = _resolve_tool_calls(tool_call_chunks_por_indice)
        observed_tools.update(tc.name for tc in tool_calls if tc.name)

        tool_calls_for_history = [_sanitize_tool_call(tc, ctx) for tc in tool_calls]
        assistant_msg = VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text=texto_final)]
            if texto_final
            else [],
            tool_calls=tool_calls_for_history,
            finish_reason="tool_calls" if tool_calls else "stop",
        )
        parent_id = await session_store.append_message(
            thread_id, assistant_msg, parent_message_id=parent_id
        )
        await emit(MessageBreak())

        if not tool_calls:
            return LoopResult(
                stopped_reason="stop",
                final_message=assistant_msg,
                usage=last_usage,
                usage_models=tuple(sorted(usage_models)),
                usage_records=tuple(usage_records),
                tool_names=tuple(sorted(observed_tools)),
            )

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
                stable_call_id = (pendente.id or "").strip() or interrupt_id
                approval_args = dict(pendente.args)
                approval_metadata: dict[str, Any] | None = None
                if pendente.name == "write_terminal":
                    raw_input = str(approval_args.pop("input_data", ""))
                    approval_args["input_preview"] = "<redacted>"
                    approval_args["input_length"] = len(raw_input.encode("utf-8"))
                elif pendente.name in {
                    "generate_image",
                    "text_to_speech",
                    "generate_video",
                }:
                    from backend.services.media_quota import (
                        media_estimate_record,
                        media_quota,
                        new_idempotency_key,
                    )

                    provider, _, model = ctx.model.partition(":")
                    estimate = media_estimate_record(
                        pendente.name, provider=provider, model=model
                    )
                    quota_summary = await media_quota.summary(ctx.user_id)
                    remaining = int(quota_summary.get("remaining", 0))
                    approval_metadata = {
                        "operation": estimate.operation,
                        "provider": estimate.provider,
                        "model": estimate.model,
                        "estimate_version": estimate.version,
                        "billable_unit": estimate.billable_unit,
                        "currency": estimate.currency,
                        "estimated_units": estimate.units,
                        "idempotency_key": new_idempotency_key(
                            stable_call_id, pendente.name
                        ),
                        # A reserva acontece somente após a aprovação. Este valor
                        # representa o saldo projetado e permite à UI mostrar o
                        # impacto sem expor argumentos sensíveis.
                        "balance_after_reservation": max(0, remaining - estimate.units),
                    }
                    approval_args = dict(approval_metadata)
                args_json = json.dumps(approval_args, ensure_ascii=False)
                raw_options = pendente.args.get("options", [])
                options = (
                    [
                        {
                            "label": str(item.get("label") or item["value"]),
                            "value": str(item["value"]),
                        }
                        for item in raw_options
                        if isinstance(item, dict) and item.get("value")
                    ]
                    if isinstance(raw_options, list)
                    else []
                )
                try:
                    priority = max(
                        0, min(100, int(pendente.args.get("approval_priority", 0)))
                    )
                except (TypeError, ValueError):
                    priority = 0
                expires_at = pendente.args.get("approval_expires_at")
                if approval_gate is not None:
                    await approval_gate.request_approval(
                        thread_id,
                        interrupt_id=interrupt_id,
                        tool_name=pendente.name,
                        tool_call_id=stable_call_id,
                        args=pendente.args,
                        options=options,
                        priority=priority,
                        expires_at=str(expires_at) if expires_at else None,
                        approval_metadata=approval_metadata,
                    )
                await emit(
                    HitlRequested(
                        tool_name=pendente.name,
                        args_json=args_json,
                        interrupt_id=interrupt_id,
                        options=options,
                        priority=priority,
                        expires_at=str(expires_at) if expires_at else None,
                    )
                )
                return LoopResult(
                    stopped_reason="interrupted",
                    final_message=assistant_msg,
                    usage=last_usage,
                    usage_models=tuple(sorted(usage_models)),
                    usage_records=tuple(usage_records),
                    tool_names=tuple(sorted(observed_tools)),
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
            tool_calls,
            tool_registry=tool_registry,
            ctx=ctx,
            turn_budget=turn_budget,
            on_event=on_event,
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
                stopped_reason="loop_cap_exceeded",
                final_message=assistant_msg,
                usage=last_usage,
                usage_models=tuple(sorted(usage_models)),
                usage_records=tuple(usage_records),
                tool_names=tuple(sorted(observed_tools)),
            )

    await emit(
        ErrorSignal(
            code="RECURSION_LIMIT",
            message=f"Limite de {config.max_iterations} iterações atingido.",
        )
    )
    return LoopResult(
        stopped_reason="max_iterations",
        usage=last_usage,
        usage_models=tuple(sorted(usage_models)),
        usage_records=tuple(usage_records),
        tool_names=tuple(sorted(observed_tools)),
    )


async def _execute_single_call(
    tool_call: ToolCall,
    *,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    on_event: EventSink | None = None,
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
        from backend.services.media_billing import apply_media_billing_source

        call_context = replace(
            ctx,
            tool_call_id=tool_call.id,
            _extra={**ctx._extra, "event_sink": on_event},
        )
        if spec.extras.category == "media":
            call_context = await apply_media_billing_source(call_context)
        texto = await spec.ainvoke(
            tool_call.args,
            call_context,
        )
        is_error = _is_tool_error(texto)
        from backend.services.tool_usage import record_tool_usage

        await record_tool_usage(
            ctx.user_id or "local", tool_call.name, "error" if is_error else "ok"
        )
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
    decided_by: str | None = None,
    interrupt_id: str | None = None,
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
    requested_interrupt_id = interrupt_id
    if requested_interrupt_id is None:
        snapshot = await session_store.get_pending_approval(thread_id)
        if snapshot is None:
            return False
        requested_interrupt_id = str(snapshot["interrupt_id"])
    claim_pending = getattr(session_store, "claim_pending_approval", None)
    if claim_pending is None:
        pending = await session_store.get_pending_approval(thread_id)
        if pending is None or pending["interrupt_id"] != requested_interrupt_id:
            return False
    else:
        pending = await claim_pending(thread_id, interrupt_id=requested_interrupt_id)
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
    ephemeral_args = (
        approval_gate.ephemeral_args(pending["interrupt_id"])
        if approval_gate is not None
        else None
    )
    media_tool = pending["tool_name"] in {
        "generate_image",
        "text_to_speech",
        "generate_video",
    }
    if ephemeral_args is None and (
        pending["tool_name"] == "write_terminal"
        or (media_tool and decision != "reject")
    ):
        # Conteúdo de mídia não é recuperável com segurança após restart:
        # encerra a pendência sem executar o provider nem persistir o payload.
        await session_store.clear_pending_approval(thread_id)
        return False

    parent_id = await session_store.get_branch_head_id(thread_id)
    for tc in tool_calls:
        if tc.id != flagged_id:
            spec = tool_registry.get(tc.name)
            if spec is not None and spec.extras.category == "media":
                resultado = VMessage(
                    role=MessageRole.TOOL,
                    content=[
                        ContentBlock(
                            kind="text",
                            text=(
                                "Esta operação de mídia exige uma aprovação "
                                "separada antes de ser executada."
                            ),
                        )
                    ],
                    tool_call_id=tc.id,
                    name=tc.name,
                    is_error=True,
                )
            else:
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
            if tc.id == flagged_id and ephemeral_args is not None:
                args = ephemeral_args
            if decision in {"edit", "option"} and edited_args is not None:
                args = edited_args
            resultado = await _execute_single_call(
                replace(tc, args=args),
                tool_registry=tool_registry,
                ctx=ctx,
                on_event=on_event,
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

    record_decision = getattr(session_store, "record_approval_decision", None)
    if record_decision is not None:
        await record_decision(
            thread_id,
            interrupt_id=str(pending["interrupt_id"]),
            tool_name=str(pending["tool_name"]),
            decision=decision,
            selection=(edited_args or {}).get("selection")
            if decision == "option"
            else None,
            decided_by=decided_by,
            options=list(pending.get("options", [])),
            priority=int(pending.get("priority", 0)),
            expires_at=pending.get("expires_at"),
        )
    if media_tool:
        from backend.persistence.telemetry import telemetry

        persisted_args = pending.get("args", {})
        telemetry.record_media_quota(
            "hitl_decision",
            operation=str(persisted_args.get("operation", pending["tool_name"])),
            provider=str(persisted_args.get("provider", "")),
            model=str(persisted_args.get("model", "")),
            units=persisted_args.get("estimated_units"),
            idempotency_key=persisted_args.get("idempotency_key"),
            result=decision,
        )
    if approval_gate is not None:
        await approval_gate.resolve(
            thread_id, interrupt_id=str(pending["interrupt_id"])
        )
    elif claim_pending is None:
        await session_store.clear_pending_approval(thread_id)
    return True
