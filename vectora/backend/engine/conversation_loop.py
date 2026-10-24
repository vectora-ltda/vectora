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

import asyncio
import contextlib
import json
import logging
import math
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, cast
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
from backend.engine.tool_batch import (
    _apply_post_execute,
    _apply_pre_execute,
    _is_tool_error,
    execute_tool_batch,
)
from backend.persistence.native.agent_run import AgentRunMetrics
from backend.persistence.native.ledger import (
    LedgerEventType,
    ThreadLedgerEvent,
    make_agent_turn_started_key,
    make_run_completed_key,
    make_run_failed_key,
    make_tool_finished_key,
    make_tool_started_key,
)
from backend.services.context_planner import compute_transcript_hash
from backend.services.memory_store import MemoryRecord
from backend.vtypes.contracts import ToolOption
from backend.vtypes.message import ContentBlock, MessageRole, ToolCall, VMessage

_REPEATED_CALL_THRESHOLD = 3
"""Quantas chamadas idênticas seguidas (mesma tool, mesmos args) disparam o
aviso de possível loop preso — inspirado na detecção de repetição do
hermes-agent (`agent/tool_guardrails.py`), sem a classificação
idempotente/mutante dele: aqui é só sinal, o LLM/HITL decide o resto."""

_REPEATED_CALL_HARD_LIMIT = 6
"""Limite de segurança antes de encerrar uma chamada de tool presa."""

logger = logging.getLogger(__name__)


def _preserve_latest_tool_result(model_id: str) -> bool:
    normalized = model_id.strip().casefold()
    return normalized.startswith(("ollama:", "llamacpp:", "dmr:"))


async def _load_persistent_context_memories(ctx: ToolContext) -> list[MemoryRecord]:
    if ctx.store is None or not hasattr(ctx.store, "asearch"):
        return []
    if ctx.user_id and ctx.user_id != "local":
        owner = ctx.user_id
    elif ctx.workspace_id:
        owner = f"workspace_{ctx.workspace_id}"
    elif ctx.thread_id:
        owner = f"session_{ctx.thread_id}"
    else:
        owner = "local"
    try:
        from backend.tools.memory import _search_unexpired

        items = await _search_unexpired(
            ctx.store,
            ("user", owner, "memories"),
            query="",
            limit=5,
        )
        from backend.services.context_planner import memory_records_from_backend_items

        return [
            record
            for record in memory_records_from_backend_items(
                items, default_scope_id=ctx.workspace_id or ctx.thread_id or "global"
            )
            if record.active
        ]
    except Exception:
        logger.exception("conversation_loop: falha ao carregar memórias persistentes")
        return []


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.engine.hitl import ApprovalGate
    from backend.engine.stream_events import EventSink
    from backend.llm.base import ChatClient
    from backend.persistence.native.session_store import SessionStore
    from backend.services.policy_compiler import PolicySnapshot
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
    run_id: str | None = None
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


async def _safe_update_agent_run_status(
    session_store: Any,
    run_id: str | None,
    *,
    status: str,
    detail: str | None = None,
    error_code: str | None = None,
    metrics: AgentRunMetrics | None = None,
) -> None:
    """Persiste o estado do run sem deixar falha do storage interromper o turno."""
    if not run_id or not hasattr(session_store, "update_agent_run_status"):
        return
    try:
        await session_store.update_agent_run_status(
            run_id,
            status=status,
            detail=detail,
            error_code=error_code,
            metrics=metrics,
        )
    except Exception:
        logger.exception(
            "conversation_loop: falha ao persistir status do run %s", run_id
        )


async def _safe_record_run_evidence(
    session_store: Any, run_id: str | None, **evidence: Any
) -> None:
    """Persiste evidências de forma best-effort, sem perder o resultado da tool."""
    if not run_id or not hasattr(session_store, "record_run_evidence"):
        return
    try:
        await session_store.record_run_evidence(run_id, **evidence)
    except Exception:
        logger.exception(
            "conversation_loop: falha ao persistir evidência do run %s", run_id
        )


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
        step_id = item.get("step_id")
        quality_gate = item.get("quality_gate")
        itens.append(
            TodoItem(
                content=str(item.get("content", "")),
                status=status,
                step_id=str(step_id) if step_id is not None else None,
                quality_gate=str(quality_gate) if quality_gate is not None else None,
            )
        )
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


async def _workspace_changed_paths(ctx: ToolContext) -> set[str]:
    """Obtém arquivos alterados do workspace, sem confiar nos argumentos da tool."""
    root = getattr(ctx, "workspace_dir", None) or getattr(ctx, "cwd", None)
    if not root:
        return set()
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        if proc.returncode != 0:
            return set()
        paths: set[str] = set()
        for line in (stdout or b"").decode("utf-8", errors="replace").splitlines():
            if len(line) <= 3:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[-1].strip()
            if path:
                paths.add(path)
        return paths
    except Exception:
        logger.debug(
            "conversation_loop: não foi possível obter arquivos alterados",
            exc_info=True,
        )
        return set()


async def run_conversation(  # noqa: PLR0911
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
    policy_snapshot: PolicySnapshot | None = None,
) -> LoopResult:
    # A origem de cobrança vem exclusivamente da credencial persistida do
    # usuário autenticado. O marcador é refeito a cada execução para impedir
    # que um contexto reidratado ou argumento de tool carregue uma isenção
    # não confirmada.
    from backend.services.media_billing import apply_media_billing_source
    from backend.services.policy_compiler import compile_policy_snapshot

    await apply_media_billing_source(ctx)
    if hasattr(session_store, "get_session") and not (
        isinstance(ctx._extra, dict) and ctx._extra.get("untrusted_content")
    ):
        session_meta = await session_store.get_session(thread_id)
        if session_meta and session_meta.get("mode") == "connect":
            if not isinstance(ctx._extra, dict):
                ctx._extra = {}
            ctx._extra["untrusted_content"] = True

    if hasattr(session_store, "list_agent_runs"):
        runs = await session_store.list_agent_runs(thread_id, limit=5)
        if any(
            bool(
                run.get("untrusted_content")
                if isinstance(run, dict)
                else getattr(run, "untrusted_content", False)
            )
            for run in runs
        ):
            if not isinstance(ctx._extra, dict):
                ctx._extra = {}
            ctx._extra["untrusted_content"] = True
    if policy_snapshot is None:
        policy_snapshot = compile_policy_snapshot(ctx)

    if isinstance(ctx._extra, dict):
        sub_deps = ctx._extra.get("subagent_deps")
        if sub_deps is not None:
            sub_deps.policy_snapshot = policy_snapshot
    emit = on_event or _noop_event
    tools = tool_registry.all()
    parent_id = await session_store.get_branch_head_id(thread_id)
    # Uma identidade por invocação mantém retries correlacionados sem
    # reutilizar IDs quando o runner não fornece run_id.
    turn_identity: str = config.run_id or f"turn-{uuid4().hex}"

    if config.run_id:
        try:
            if hasattr(session_store, "append_ledger_event"):
                await session_store.append_ledger_event(
                    thread_id,
                    event_key=make_agent_turn_started_key(config.run_id),
                    event_type="agent_turn_started",
                    run_id=config.run_id,
                    payload={"iteration": 1},
                )
            elif hasattr(session_store, "append_event"):
                await session_store.append_event(
                    thread_id,
                    ThreadLedgerEvent(
                        thread_id=thread_id,
                        event_key=make_agent_turn_started_key(config.run_id),
                        type="agent_turn_started",
                        run_id=config.run_id,
                        payload={"iteration": 1},
                    ),
                )
        except Exception:
            logger.exception(
                "conversation_loop: falha ao registrar agent_turn_started no ledger para thread %s",
                thread_id,
            )
    assinaturas_anteriores: tuple[tuple[str, str], ...] | None = None
    repeticoes_seguidas = 0
    turn_budget = TurnBudget(config=config.loop_caps)
    last_usage: dict[str, int] | None = None
    usage_models: set[str] = set()
    usage_records: list[tuple[str | None, dict[str, int]]] = []
    observed_tools: set[str] = set()
    files_touched: list[str] = []
    executed_step_gates: set[tuple[str, str]] = set()
    manifest_recorded = False
    projection_recorded = False

    async def _persist_context_checkpoint(
        raw_history: list[VMessage],
        *,
        phase: str,
        iteration: int,
        source_hash: str,
        pending_tool_call_ids: tuple[str, ...] = (),
        completed_tool_call_ids: tuple[str, ...] = (),
    ) -> None:
        if not config.run_id or not hasattr(session_store, "save_context_checkpoint"):
            return
        await session_store.save_context_checkpoint(
            thread_id,
            config.run_id,
            {
                "checkpoint_id": f"{config.run_id}:seq:{len(raw_history)}:{phase}",
                "last_sequence": len(raw_history),
                "source_hash": source_hash,
                "schema_version": "context.v1",
                "state": {
                    "iteration": iteration,
                    "phase": phase,
                    "policy_version": getattr(policy_snapshot, "version", ""),
                    "pending_tool_call_ids": list(pending_tool_call_ids),
                    "completed_tool_call_ids": list(completed_tool_call_ids),
                },
                "artifact_refs": [],
            },
        )

    if config.run_id and hasattr(session_store, "get_latest_context_checkpoint"):
        try:
            checkpoint = await session_store.get_latest_context_checkpoint(
                thread_id, run_id=config.run_id
            )
            state = checkpoint.get("state", {}) if checkpoint else {}
            current_history = await session_store.get_history(thread_id)
            checkpoint_sequence = (
                int(checkpoint.get("last_sequence", 0))
                if checkpoint is not None
                else -1
            )
            checkpoint_hash_matches = bool(
                checkpoint
                and checkpoint_sequence >= 0
                and checkpoint_sequence <= len(current_history)
                and checkpoint.get("source_hash")
                == compute_transcript_hash(current_history[:checkpoint_sequence])
            )
            checkpoint_is_current = bool(
                checkpoint
                and checkpoint.get("schema_version") == "context.v1"
                and checkpoint_hash_matches
            )
            if (
                checkpoint_is_current
                and checkpoint is not None
                and checkpoint.get("run_id") == config.run_id
                and state.get("phase") == "tool_running"
                and state.get("pending_tool_call_ids")
            ):
                await _safe_update_agent_run_status(
                    session_store,
                    config.run_id,
                    status="recovery_required",
                    detail="Execução interrompida durante tools; reconciliação necessária.",
                    error_code="CHECKPOINT_TOOL_RECONCILIATION_REQUIRED",
                )
                return LoopResult(stopped_reason="recovery_required")
        except Exception:
            logger.exception(
                "conversation_loop: falha ao carregar checkpoint de retomada"
            )

    for _iteracao in range(config.max_iterations):
        raw_history = await session_store.get_history(thread_id)
        effective_max_tokens = (
            config.context_max_tokens
            if config.context_max_tokens is not None
            else _default_context_tokens()
        )
        from backend.services.context_planner import plan_context

        planned_context = plan_context(
            raw_history,
            max_tokens=effective_max_tokens,
            enabled=config.context_compaction_enabled,
            preserve_latest_tool_result=_preserve_latest_tool_result(str(ctx.model)),
            workspace_id=ctx.workspace_id or None,
            policy_snapshot=policy_snapshot,
            persistent_memory_records=await _load_persistent_context_memories(ctx),
        )
        historico = planned_context.messages
        if config.run_id and hasattr(session_store, "record_run_evidence"):
            try:
                if planned_context.manifest and not manifest_recorded:
                    await session_store.record_run_evidence(
                        config.run_id,
                        evidence={
                            "run_id": config.run_id,
                            "thread_id": thread_id,
                            **planned_context.manifest.to_evidence_entry(),
                        },
                    )
                    manifest_recorded = True
                if planned_context.projection is not None and not projection_recorded:
                    projection_entry = planned_context.projection.to_evidence_entry()
                    await session_store.record_run_evidence(
                        config.run_id,
                        evidence={
                            "run_id": config.run_id,
                            "thread_id": thread_id,
                            **projection_entry,
                        },
                    )
                    if hasattr(session_store, "save_context_projection"):
                        await session_store.save_context_projection(
                            thread_id, config.run_id, dict(projection_entry)
                        )
                    projection_recorded = True
            except Exception:
                logger.exception(
                    "conversation_loop: falha ao persistir contexto planejado"
                )
        source_hash = (
            planned_context.projection.source_transcript_hash
            if planned_context.projection is not None
            else compute_transcript_hash(raw_history)
        )
        with contextlib.suppress(Exception):
            await _persist_context_checkpoint(
                raw_history,
                phase="provider_dispatch",
                iteration=_iteracao,
                source_hash=source_hash,
            )

        partes_texto: list[str] = []
        tool_call_chunks_por_indice: dict[int, dict[str, Any]] = {}
        chunk_count = 0
        text_chunk_count = 0
        reasoning_chars = 0
        tool_chunk_count = 0
        finish_reasons: list[str] = []

        logger.info(
            "llm_turn_started",
            extra={
                "thread_id": thread_id,
                "run_id": config.run_id or "",
                "iteration": _iteracao + 1,
                "history_messages": len(historico),
                "tool_count": len(tools),
                "model": str(getattr(chat_client, "last_model_id", "") or ""),
            },
        )

        stream_usage: dict[str, int] | None = None
        async for chunk in chat_client.astream(
            historico,
            tools=tools,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        ):
            chunk_count += 1
            if chunk.finish_reason:
                finish_reasons.append(str(chunk.finish_reason))
            if chunk.usage:
                stream_usage = cast("dict[str, int]", dict(chunk.usage))
            if chunk.delta_text:
                text_chunk_count += 1
                partes_texto.append(chunk.delta_text)
                await emit(MessageChunk(content=chunk.delta_text))

            reasoning_chars += len(chunk.delta_reasoning or "")
            tool_chunk_count += len(chunk.tool_call_chunks)

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
        tool_calls = [
            replace(
                tool_call,
                id=tool_call.id or f"{turn_identity}:tool:{_iteracao}:{index}",
            )
            for index, tool_call in enumerate(tool_calls)
        ]
        observed_tools.update(tc.name for tc in tool_calls if tc.name)

        logger.info(
            "llm_turn_finished",
            extra={
                "thread_id": thread_id,
                "run_id": config.run_id or "",
                "iteration": _iteracao + 1,
                "model": str(getattr(chat_client, "last_model_id", "") or ""),
                "chunk_count": chunk_count,
                "text_chunk_count": text_chunk_count,
                "text_chars": len(texto_final),
                "reasoning_chars": reasoning_chars,
                "tool_chunk_count": tool_chunk_count,
                "tool_call_count": len(tool_calls),
                "finish_reasons": ",".join(finish_reasons),
                "usage_present": stream_usage is not None,
            },
        )

        if not texto_final and not tool_calls:
            logger.warning(
                "llm_empty_response",
                extra={
                    "thread_id": thread_id,
                    "run_id": config.run_id or "",
                    "iteration": _iteracao + 1,
                    "model": str(getattr(chat_client, "last_model_id", "") or ""),
                    "chunk_count": chunk_count,
                    "reasoning_chars": reasoning_chars,
                    "finish_reasons": ",".join(finish_reasons),
                },
            )
            await emit(
                ErrorSignal(
                    code="EMPTY_MODEL_RESPONSE",
                    message=(
                        "O provedor LLM encerrou o stream sem retornar texto "
                        "ou uma chamada de ferramenta. Tente novamente ou "
                        "troque o modelo."
                    ),
                )
            )
            await emit(MessageBreak())
            await _safe_update_agent_run_status(
                session_store,
                config.run_id,
                status="failed",
                detail="O provedor encerrou o stream sem texto ou tool call.",
                error_code="EMPTY_MODEL_RESPONSE",
            )
            if config.run_id:
                with contextlib.suppress(Exception):
                    await session_store.append_ledger_event(
                        thread_id,
                        event_key=make_run_failed_key(config.run_id),
                        event_type=LedgerEventType.RUN_FAILED,
                        run_id=config.run_id,
                        payload={"error": "EMPTY_MODEL_RESPONSE"},
                    )
            return LoopResult(
                stopped_reason="stop",
                final_message=None,
                usage=last_usage,
                usage_models=tuple(sorted(usage_models)),
                usage_records=tuple(usage_records),
                tool_names=tuple(sorted(observed_tools)),
            )

        tool_calls_for_history = [_sanitize_tool_call(tc, ctx) for tc in tool_calls]
        assistant_msg = VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text=texto_final)]
            if texto_final
            else [],
            tool_calls=tool_calls_for_history,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

        # Do not persist an assistant tool-call message when the repetition
        # guard is about to stop the loop.  Persisting it would leave pending
        # calls in the session history without matching tool results, causing
        # the next run to replay an incomplete turn.
        if tool_calls:
            assinaturas_atual = tuple(_call_signature(tc) for tc in tool_calls)
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
            if repeticoes_seguidas >= _REPEATED_CALL_HARD_LIMIT:
                nomes = ", ".join(sorted({tc.name for tc in tool_calls}))
                mensagem = (
                    f"Execução interrompida: a mesma chamada ({nomes}) foi repetida "
                    f"{repeticoes_seguidas} vezes sem progresso."
                )
                logger.error(
                    "tool_call_loop_stopped",
                    extra={
                        "thread_id": thread_id,
                        "iteration": _iteracao + 1,
                        "tool_names": nomes,
                        "repetitions": repeticoes_seguidas,
                    },
                )
                await emit(ErrorSignal(code="TOOL_CALL_REPEATED", message=mensagem))
                await emit(MessageBreak())
                await _safe_update_agent_run_status(
                    session_store,
                    config.run_id,
                    status="failed",
                    detail=mensagem,
                    error_code="TOOL_CALL_REPEATED",
                )
                if config.run_id:
                    with contextlib.suppress(Exception):
                        await session_store.append_ledger_event(
                            thread_id,
                            event_key=make_run_failed_key(config.run_id),
                            event_type=LedgerEventType.RUN_FAILED,
                            run_id=config.run_id,
                            payload={"error": "TOOL_CALL_REPEATED"},
                        )
                return LoopResult(
                    stopped_reason="loop_cap_exceeded",
                    final_message=None,
                    usage=last_usage,
                    usage_models=tuple(sorted(usage_models)),
                    usage_records=tuple(usage_records),
                    tool_names=tuple(sorted(observed_tools)),
                )

        parent_id = await session_store.append_message(
            thread_id, assistant_msg, parent_message_id=parent_id, run_id=config.run_id
        )
        await emit(MessageBreak())

        iter_metrics = AgentRunMetrics(
            input_tokens=int(last_usage.get("input_tokens", 0) if last_usage else 0),
            output_tokens=int(last_usage.get("output_tokens", 0) if last_usage else 0),
            total_tokens=int(last_usage.get("total_tokens", 0) if last_usage else 0),
        )

        if not tool_calls:
            if config.run_id:
                try:
                    if hasattr(session_store, "append_ledger_event"):
                        await session_store.append_ledger_event(
                            thread_id,
                            event_key=make_run_completed_key(config.run_id),
                            event_type=LedgerEventType.RUN_COMPLETED,
                            run_id=config.run_id,
                            payload={"stopped_reason": "stop"},
                        )
                    elif hasattr(session_store, "append_event"):
                        await session_store.append_event(
                            thread_id,
                            ThreadLedgerEvent(
                                thread_id=thread_id,
                                event_key=make_run_completed_key(config.run_id),
                                type=LedgerEventType.RUN_COMPLETED,
                                run_id=config.run_id,
                                payload={"stopped_reason": "stop"},
                            ),
                        )
                except Exception:
                    logger.exception(
                        "Falha ao registrar RUN_COMPLETED no ledger para thread %s",
                        thread_id,
                    )
                if hasattr(session_store, "update_agent_run_status"):
                    verification_handled = True
                    # 1. Transiciona atomicamente para o estado 'verifying'
                    await _safe_update_agent_run_status(
                        session_store,
                        config.run_id,
                        status="verifying",
                        detail="Executando verificação de quality gates e snapshot criptográfico.",
                        metrics=iter_metrics,
                    )
                    # 2. Despacha o motor de verificação pós-execução
                    from backend.services.verification import run_verification

                    ws_id = getattr(ctx, "workspace_id", None)
                    ws_dir = getattr(ctx, "workspace_dir", None) or getattr(
                        ctx, "cwd", None
                    )
                    v_res = None
                    try:
                        v_res = await run_verification(
                            ws_id,
                            ws_dir,
                            files_touched=files_touched or None,
                        )
                    except Exception:
                        logger.exception(
                            "Falha ao executar verificação pós-execução para run %s (thread %s)",
                            config.run_id,
                            thread_id,
                        )
                        await _safe_update_agent_run_status(
                            session_store,
                            config.run_id,
                            status="failed",
                            detail="Erro interno durante a verificação pós-execução.",
                            error_code="VERIFICATION_ERROR",
                            metrics=iter_metrics,
                        )

                    # 3. Persiste o resultado da verificação e a evidência com SHA-256 no AgentRun
                    if v_res is not None:
                        try:
                            if hasattr(session_store, "get_agent_run") and hasattr(
                                session_store, "upsert_agent_run"
                            ):
                                agent_run = await session_store.get_agent_run(
                                    config.run_id
                                )
                                if agent_run is not None:
                                    agent_run.evidence.append(v_res.to_evidence_entry())
                                    agent_run.metrics = iter_metrics
                                    agent_run.detail = (
                                        "Verificação concluída com sucesso."
                                        if v_res.passed
                                        else f"Falha nos quality gates: {v_res.failure_feedback or ''}"
                                    )
                                    await session_store.upsert_agent_run(agent_run)
                                    # A persistência da evidência não deve alterar a
                                    # máquina de estados. O motor de verificação é a
                                    # única fonte do desfecho terminal e a transição
                                    # ocorre depois que a evidência está durável.
                                    await _safe_update_agent_run_status(
                                        session_store,
                                        config.run_id,
                                        status=v_res.status,
                                        detail=agent_run.detail,
                                        metrics=iter_metrics,
                                    )
                            else:
                                await _safe_update_agent_run_status(
                                    session_store,
                                    config.run_id,
                                    status=v_res.status,
                                    detail=(
                                        "Verificação concluída com sucesso."
                                        if v_res.passed
                                        else "Falha nos quality gates."
                                    ),
                                    metrics=iter_metrics,
                                )
                        except Exception:
                            logger.exception(
                                "Falha ao persistir evidências de verificação para run %s",
                                config.run_id,
                            )
                else:
                    verification_handled = False
                if not verification_handled:
                    await _safe_update_agent_run_status(
                        session_store,
                        config.run_id,
                        status="passed",
                        detail="Execução completada com sucesso.",
                        metrics=iter_metrics,
                    )
            return LoopResult(
                stopped_reason="stop",
                final_message=assistant_msg,
                usage=last_usage,
                usage_models=tuple(sorted(usage_models)),
                usage_records=tuple(usage_records),
                tool_names=tuple(sorted(observed_tools)),
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
                options: list[ToolOption] = []
                if isinstance(raw_options, list):
                    for item in raw_options:
                        if not isinstance(item, dict):
                            continue
                        raw_value = item.get("value")
                        if not raw_value:
                            continue
                        raw_label = item.get("label") or raw_value
                        options.append(
                            {"label": str(raw_label), "value": str(raw_value)}
                        )
                raw_priority = pendente.args.get("approval_priority", 0)
                try:
                    if isinstance(raw_priority, (int, float, str)):
                        numeric_priority = float(raw_priority)
                        priority = (
                            max(0, min(100, int(numeric_priority)))
                            if math.isfinite(numeric_priority)
                            else 0
                        )
                    else:
                        priority = 0
                except (TypeError, ValueError):
                    priority = 0
                expires_at = pendente.args.get("approval_expires_at")
                if approval_gate is not None:
                    await approval_gate.request_approval(
                        thread_id,
                        interrupt_id=interrupt_id,
                        tool_name=pendente.name,
                        tool_call_id=stable_call_id,
                        run_id=config.run_id,
                        args=pendente.args,
                        options=options,
                        priority=priority,
                        expires_at=str(expires_at) if expires_at else None,
                        approval_metadata=approval_metadata,
                    )
                # A falha na gravação de evidências ou atualização do run não pode impedir
                # a emissão de HitlRequested para o cliente.
                try:
                    await _safe_record_run_evidence(
                        session_store,
                        config.run_id,
                        approval={
                            "interrupt_id": interrupt_id,
                            "tool_name": pendente.name,
                            "tool_call_id": stable_call_id,
                            "options": options,
                            "priority": priority,
                        },
                    )
                    await _safe_update_agent_run_status(
                        session_store,
                        config.run_id,
                        status="waiting_approval",
                        detail=f"Aguardando aprovação para {pendente.name}",
                        metrics=iter_metrics,
                    )
                except Exception:
                    logger.exception(
                        "conversation_loop: falha ao persistir evidência/status de aprovação para thread %s",
                        thread_id,
                    )
                await emit(
                    HitlRequested(
                        tool_name=pendente.name,
                        args_json=args_json,
                        interrupt_id=interrupt_id,
                        permission_mode=ctx.permission_mode or "ask",
                        workspace_id=ctx.workspace_id,
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
            if config.run_id:
                try:
                    if hasattr(session_store, "append_ledger_event"):
                        await session_store.append_ledger_event(
                            thread_id,
                            event_key=make_tool_started_key(config.run_id, tc.id),
                            event_type=LedgerEventType.TOOL_STARTED,
                            run_id=config.run_id,
                            tool_call_id=tc.id,
                            payload={
                                "tool_name": tc.name,
                                "args": _sanitize_tool_call(tc, ctx).args,
                            },
                        )
                    elif hasattr(session_store, "append_event"):
                        await session_store.append_event(
                            thread_id,
                            ThreadLedgerEvent(
                                thread_id=thread_id,
                                event_key=make_tool_started_key(config.run_id, tc.id),
                                type=LedgerEventType.TOOL_STARTED,
                                run_id=config.run_id,
                                tool_call_id=tc.id,
                                payload={
                                    "tool_name": tc.name,
                                    "args": _sanitize_tool_call(tc, ctx).args,
                                },
                            ),
                        )
                except Exception:
                    logger.exception(
                        "Falha ao registrar TOOL_STARTED no ledger para tool %s (thread %s)",
                        tc.name,
                        thread_id,
                    )
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

        with contextlib.suppress(Exception):
            await _persist_context_checkpoint(
                raw_history,
                phase="tool_running",
                iteration=_iteracao,
                source_hash=source_hash,
                pending_tool_call_ids=tuple(call.id for call in tool_calls if call.id),
            )

        resultados = await execute_tool_batch(
            tool_calls,
            tool_registry=tool_registry,
            ctx=ctx,
            turn_budget=turn_budget,
            on_event=on_event,
            policy_snapshot=policy_snapshot,
        )
        for resultado in resultados:
            parent_id = await session_store.append_message(
                thread_id, resultado, parent_message_id=parent_id
            )
            call_id = resultado.tool_call_id or ""
            if config.run_id and call_id:
                try:
                    if hasattr(session_store, "append_ledger_event"):
                        await session_store.append_ledger_event(
                            thread_id,
                            event_key=make_tool_finished_key(config.run_id, call_id),
                            event_type=LedgerEventType.TOOL_FINISHED,
                            run_id=config.run_id,
                            tool_call_id=call_id,
                            payload={
                                "tool_name": resultado.name,
                                "is_error": resultado.is_error,
                            },
                        )
                    elif hasattr(session_store, "append_event"):
                        await session_store.append_event(
                            thread_id,
                            ThreadLedgerEvent(
                                thread_id=thread_id,
                                event_key=make_tool_finished_key(
                                    config.run_id, call_id
                                ),
                                type=LedgerEventType.TOOL_FINISHED,
                                run_id=config.run_id,
                                tool_call_id=call_id,
                                payload={
                                    "tool_name": resultado.name,
                                    "is_error": resultado.is_error,
                                },
                            ),
                        )
                except Exception:
                    logger.exception(
                        "Falha ao registrar TOOL_FINISHED no ledger para tool_call %s (thread %s)",
                        call_id,
                        thread_id,
                    )
            if (
                hasattr(session_store, "record_run_evidence")
                and config.run_id
                and resultado.name
            ):
                await session_store.record_run_evidence(
                    config.run_id,
                    tool_used=resultado.name,
                    evidence={
                        "type": "tool_result",
                        "tool_name": resultado.name,
                        "tool_call_id": call_id,
                        "is_error": resultado.is_error,
                    },
                )
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
                    if (
                        config.run_id
                        and hasattr(session_store, "get_agent_run")
                        and hasattr(session_store, "upsert_agent_run")
                    ):
                        try:
                            active_item = next(
                                (t for t in todos if t.status == "in_progress"),
                                None,
                            )
                            active_step_id = (
                                (active_item.step_id or active_item.content)
                                if active_item
                                else None
                            )
                            agent_run = await session_store.get_agent_run(config.run_id)
                            if agent_run is not None:
                                agent_run.plan_step = active_step_id
                                # Recupera gates já concluídos com sucesso para não reexecutar
                                for ev in agent_run.evidence:
                                    if (
                                        isinstance(ev, dict)
                                        and ev.get("kind")
                                        == "quality_gate_verification"
                                        and ev.get("passed")
                                    ):
                                        ev_step = ev.get("step_id")
                                        ev_gate = ev.get("quality_gate")
                                        if ev_step and ev_gate:
                                            executed_step_gates.add(
                                                (str(ev_step), str(ev_gate))
                                            )

                                # Executa quality gates específicos para etapas concluídas
                                for item in todos:
                                    if item.status == "completed" and item.quality_gate:
                                        step_key = item.step_id or item.content
                                        gate_key = item.quality_gate
                                        pair = (step_key, gate_key)
                                        if pair in executed_step_gates:
                                            continue

                                        try:
                                            from backend.services.verification import (
                                                run_verification,
                                            )

                                            ws_id = getattr(ctx, "workspace_id", None)
                                            ws_dir = getattr(
                                                ctx, "workspace_dir", None
                                            ) or getattr(ctx, "cwd", None)
                                            v_res = await run_verification(
                                                ws_id,
                                                ws_dir,
                                                target_gate_ids=[item.quality_gate],
                                            )
                                            v_entry = v_res.to_evidence_entry()
                                            v_entry["step_id"] = step_key
                                            v_entry["quality_gate"] = gate_key
                                            agent_run.evidence.append(v_entry)
                                            if v_res.passed:
                                                executed_step_gates.add(pair)
                                            # The verification result is
                                            # persisted as evidence; the
                                            # final verification pass owns the
                                            # terminal AgentRun status.
                                        except Exception as gate_exc:
                                            logger.exception(
                                                "Falha ao executar quality gate %s para step %s (run_id %s)",
                                                gate_key,
                                                step_key,
                                                config.run_id,
                                            )
                                            agent_run.evidence.append(
                                                {
                                                    "kind": "quality_gate_verification",
                                                    "step_id": step_key,
                                                    "quality_gate": gate_key,
                                                    "passed": False,
                                                    "error": str(gate_exc),
                                                }
                                            )
                                try:
                                    await session_store.upsert_agent_run(agent_run)
                                except Exception:
                                    logger.exception(
                                        "Falha ao persistir AgentRun atualizado com quality gates (run_id %s)",
                                        config.run_id,
                                    )
                        except Exception:
                            logger.exception(
                                "Erro inesperado ao processar plano e quality gates para run_id %s",
                                config.run_id,
                            )

        # Deriva a evidência do workspace após o lote. Isso exclui chamadas
        # rejeitadas e leituras, e também detecta arquivos escritos via terminal.
        for changed_path in await _workspace_changed_paths(ctx):
            if changed_path not in files_touched:
                files_touched.append(changed_path)

        with contextlib.suppress(Exception):
            await _persist_context_checkpoint(
                raw_history,
                phase="provider_dispatch",
                iteration=_iteracao,
                source_hash=source_hash,
                completed_tool_call_ids=tuple(
                    result.tool_call_id for result in resultados if result.tool_call_id
                ),
            )

        if turn_budget.exceeded is not None:
            if config.run_id:
                try:
                    if hasattr(session_store, "append_ledger_event"):
                        await session_store.append_ledger_event(
                            thread_id,
                            event_key=make_run_failed_key(config.run_id),
                            event_type=LedgerEventType.RUN_FAILED,
                            run_id=config.run_id,
                            payload={
                                "error": f"loop_cap_exceeded: {turn_budget.exceeded}"
                            },
                        )
                    elif hasattr(session_store, "append_event"):
                        await session_store.append_event(
                            thread_id,
                            ThreadLedgerEvent(
                                thread_id=thread_id,
                                event_key=make_run_failed_key(config.run_id),
                                type=LedgerEventType.RUN_FAILED,
                                run_id=config.run_id,
                                payload={
                                    "error": f"loop_cap_exceeded: {turn_budget.exceeded}"
                                },
                            ),
                        )
                except Exception:
                    logger.exception(
                        "Falha ao registrar RUN_FAILED (loop_cap_exceeded) no ledger para thread %s",
                        thread_id,
                    )
                if hasattr(session_store, "update_agent_run_status"):
                    await session_store.update_agent_run_status(
                        config.run_id,
                        status="failed",
                        detail=f"Teto de guardrail excedido: {turn_budget.exceeded}",
                        error_code="LOOP_CAP_EXCEEDED",
                        metrics=iter_metrics,
                    )
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

    if config.run_id:
        try:
            if hasattr(session_store, "append_ledger_event"):
                await session_store.append_ledger_event(
                    thread_id,
                    event_key=make_run_failed_key(config.run_id),
                    event_type=LedgerEventType.RUN_FAILED,
                    run_id=config.run_id,
                    payload={"error": f"recursion_limit: {config.max_iterations}"},
                )
            elif hasattr(session_store, "append_event"):
                await session_store.append_event(
                    thread_id,
                    ThreadLedgerEvent(
                        thread_id=thread_id,
                        event_key=make_run_failed_key(config.run_id),
                        type=LedgerEventType.RUN_FAILED,
                        run_id=config.run_id,
                        payload={"error": f"recursion_limit: {config.max_iterations}"},
                    ),
                )
        except Exception:
            logger.exception(
                "Falha ao registrar RUN_FAILED (recursion_limit) no ledger para thread %s",
                thread_id,
            )
        if hasattr(session_store, "update_agent_run_status"):
            final_metrics = AgentRunMetrics(
                input_tokens=int(
                    last_usage.get("input_tokens", 0) if last_usage else 0
                ),
                output_tokens=int(
                    last_usage.get("output_tokens", 0) if last_usage else 0
                ),
                total_tokens=int(
                    last_usage.get("total_tokens", 0) if last_usage else 0
                ),
            )
            await session_store.update_agent_run_status(
                config.run_id,
                status="failed",
                detail=f"Limite de {config.max_iterations} iterações atingido.",
                error_code="RECURSION_LIMIT",
                metrics=final_metrics,
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
    policy_snapshot: PolicySnapshot | None = None,
) -> VMessage:
    """Mesma lógica de execução de ``tool_batch._run_one``, sem
    ``TurnBudget`` (o teto de volume é do turno que gerou o lote original,
    já contabilizado ou não antes da pausa — reaplicá-lo no resume duplicaria
    ou perderia contagem)."""
    allowed, deny_reason = _apply_pre_execute(tool_call, ctx, policy_snapshot)
    if not allowed:
        return VMessage(
            role=MessageRole.TOOL,
            content=[
                ContentBlock(
                    kind="text",
                    text=f"Error: policy_denied: {deny_reason}",
                )
            ],
            tool_call_id=tool_call.id,
            name=tool_call.name,
            is_error=True,
        )

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
        texto = _apply_post_execute(texto)
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
    policy_snapshot: PolicySnapshot | None = None,
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
    from backend.services.media_billing import apply_media_billing_source
    from backend.services.policy_compiler import compile_policy_snapshot

    await apply_media_billing_source(ctx)
    is_untrusted_thread = False
    if hasattr(session_store, "get_session"):
        session_meta = await session_store.get_session(thread_id)
        if session_meta and session_meta.get("mode") == "connect":
            is_untrusted_thread = True
    if hasattr(session_store, "list_agent_runs"):
        runs = await session_store.list_agent_runs(thread_id, limit=5)
        paused_run = next(
            (r for r in runs if r.status == "waiting_approval"),
            runs[0] if runs else None,
        )
        if paused_run and paused_run.untrusted_content:
            is_untrusted_thread = True
    if is_untrusted_thread:
        if not isinstance(ctx._extra, dict):
            ctx._extra = {}
        ctx._extra["untrusted_content"] = True

    if policy_snapshot is None:
        policy_snapshot = compile_policy_snapshot(ctx)

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

    # A retomada deve continuar o mesmo turno persistido, e não criar uma
    # segunda identidade de execução. Isso mantém tool_started/tool_finished
    # correlacionados com a aprovação original após uma pausa HITL.
    resume_run_id = str(pending.get("run_id") or "") or None

    async def _execute_resume_tool(
        tc: ToolCall,
        args: dict[str, Any] | None = None,
        policy_snapshot: PolicySnapshot | None = None,
    ) -> VMessage:
        call = replace(tc, args=args) if args is not None else tc
        call_id = call.id or f"{resume_run_id or thread_id}:tool:{uuid4().hex}"
        call = replace(call, id=call_id)
        sanitized = _sanitize_tool_call(call, ctx)

        async def record(event_type: str, payload: dict[str, Any]) -> None:
            if not resume_run_id:
                return
            try:
                if hasattr(session_store, "append_ledger_event"):
                    await session_store.append_ledger_event(
                        thread_id,
                        event_key=(
                            make_tool_started_key(resume_run_id, call_id)
                            if event_type == "tool_started"
                            else make_tool_finished_key(resume_run_id, call_id)
                        ),
                        event_type=event_type,
                        run_id=resume_run_id,
                        tool_call_id=call_id,
                        payload=payload,
                    )
                elif hasattr(session_store, "append_event"):
                    await session_store.append_event(
                        thread_id,
                        ThreadLedgerEvent(
                            thread_id=thread_id,
                            event_key=(
                                make_tool_started_key(resume_run_id, call_id)
                                if event_type == "tool_started"
                                else make_tool_finished_key(resume_run_id, call_id)
                            ),
                            type=event_type,
                            run_id=resume_run_id,
                            tool_call_id=call_id,
                            payload=payload,
                        ),
                    )
            except Exception:
                logger.exception(
                    "Falha ao registrar %s da retomada no ledger para tool %s",
                    event_type,
                    call.name,
                )

        await record(
            "tool_started",
            {"tool_name": sanitized.name, "args": sanitized.args},
        )
        try:
            result = await _execute_single_call(
                call,
                tool_registry=tool_registry,
                ctx=ctx,
                on_event=on_event,
                policy_snapshot=policy_snapshot,
            )
        except Exception as exc:
            await record(
                "tool_finished",
                {"tool_name": call.name, "is_error": True, "error": str(exc)},
            )
            raise
        await record(
            "tool_finished",
            {"tool_name": result.name, "is_error": result.is_error},
        )
        return result

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
                from backend.engine.hitl import call_requires_separate_approval

                if call_requires_separate_approval(tc.name, tc.args):
                    resultado = VMessage(
                        role=MessageRole.TOOL,
                        content=[
                            ContentBlock(
                                kind="text",
                                text="Esta ação exige uma aprovação separada.",
                            )
                        ],
                        tool_call_id=tc.id,
                        name=tc.name,
                        is_error=True,
                    )
                else:
                    resultado = await _execute_resume_tool(
                        tc, policy_snapshot=policy_snapshot
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
            call_snapshot = policy_snapshot
            if (
                tc.id == flagged_id
                and tc.name == "git_push"
                and bool(args.get("force", False))
                and policy_snapshot is not None
                and not policy_snapshot.allow_force_push
            ):
                from dataclasses import replace as dc_replace

                call_snapshot = dc_replace(policy_snapshot, allow_force_push=True)

            resultado = await _execute_resume_tool(
                replace(tc, args=args), policy_snapshot=call_snapshot
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
