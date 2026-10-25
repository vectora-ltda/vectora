"""Planejador Unificado de Contexto e Memória (ContextPlanner).

Seleciona mensagens, projeções, memórias e arquivos por relevância e
preserva a integridade causal de tool-calls/tool-results e instruções do sistema.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import cast

from backend.services.context_compaction import (
    _bound_tool_results,
    _conversation_units,
    _marker,
    _message_tokens,
)
from backend.services.context_manifest import (
    ContextBlock,
    ContextDomain,
    ContextManifest,
    ContextProjection,
)
from backend.services.memory_store import (
    MemoryRecord,
    MemoryStore,
    MemoryTrustLevel,
    get_memory_store,
)
from backend.services.policy_compiler import PolicySnapshot
from backend.vtypes import JsonObject
from backend.vtypes.message import ContentBlock, MessageRole, VMessage

_MEMORY_DOMAINS = {
    "system",
    "short_term",
    "session",
    "project",
    "vector_rag",
    "structural_graph",
}
_MEMORY_TRUST_LEVELS = {"trusted", "verified", "untrusted"}


def _persisted_confidence(value: object) -> float:
    """Normaliza confiança externa sem deixar um item corrompido abortar o loop."""
    if not isinstance(value, (int, float, str)):
        return 1.0
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(confidence):
        return 1.0
    return min(1.0, max(0.0, confidence))


def memory_records_from_backend_items(
    items: Sequence[object],
    *,
    default_scope_id: str,
    default_domain: str = "project",
) -> list[MemoryRecord]:
    """Converte itens do ``StoreBackend`` no contrato do planejador.

    O store nativo entrega itens com ``key`` e ``value`` sem impor um modelo
    de memória. A conversão valida os campos controlados pelo backend e aplica
    escopo/domínio padrão, evitando que conteúdo persistido escolha uma
    política diferente da solicitada pelo planejador.
    """
    domain = default_domain if default_domain in _MEMORY_DOMAINS else "project"
    records: list[MemoryRecord] = []
    for item in items:
        key = getattr(item, "key", None)
        raw_value = getattr(item, "value", None)
        if not isinstance(key, str) or not key or not isinstance(raw_value, Mapping):
            continue
        content = raw_value.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        raw_metadata = raw_value.get("metadata")
        metadata: JsonObject = (
            dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        )
        raw_domain = metadata.get("domain")
        record_domain = (
            raw_domain
            if isinstance(raw_domain, str) and raw_domain in _MEMORY_DOMAINS
            else domain
        )
        raw_scope = metadata.get("scope_id")
        scope_id = (
            str(raw_scope)
            if isinstance(raw_scope, str) and raw_scope
            else default_scope_id
        )
        raw_trust = metadata.get("trust_level")
        trust_level = (
            raw_trust
            if isinstance(raw_trust, str) and raw_trust in _MEMORY_TRUST_LEVELS
            else "untrusted"
        )
        provenance = metadata.get("provenance") or metadata.get("source") or "store"
        records.append(
            MemoryRecord(
                id=key,
                domain=cast("ContextDomain", record_domain),
                scope_id=scope_id,
                content=content,
                trust_level=cast("MemoryTrustLevel", trust_level),
                provenance=str(provenance),
                created_at=str(
                    raw_value.get("created_at") or raw_value.get("updated_at") or ""
                ),
                accessed_at=str(raw_value.get("updated_at") or ""),
                metadata=metadata,
                confidence=_persisted_confidence(raw_value.get("confidence", 1.0)),
                valid_until=(
                    str(raw_value["valid_until"])
                    if raw_value.get("valid_until") is not None
                    else None
                ),
            )
        )
    return records


def compute_transcript_hash(messages: list[VMessage]) -> str:
    """Calcula o hash SHA-256 estável da sequência de mensagens."""
    h = hashlib.sha256()
    for m in messages:
        payload = json.dumps(
            m.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        h.update(len(payload).to_bytes(8, byteorder="big"))
        h.update(payload)
    return h.hexdigest()


def _format_compacted_marker(
    omitted_count: int,
    *,
    preserved_errors: list[str] | None = None,
    preserved_approvals: list[str] | None = None,
    preserved_decisions: list[str] | None = None,
) -> VMessage:
    """Gera marcador de compactação com decisões, aprovações e erros estruturados preservados."""
    lines = [
        f"[{omitted_count} mensagens anteriores compactadas para manter o limite de contexto operacional]"
    ]
    if preserved_decisions:
        lines.append("Decisões anteriores preservadas:")
        lines.extend(f"- {d}" for d in preserved_decisions[:5])
    if preserved_approvals:
        lines.append("Aprovações anteriores preservadas:")
        lines.extend(f"- {a}" for a in preserved_approvals[:5])
    if preserved_errors:
        lines.append("Erros/falhas anteriores preservados:")
        lines.extend(f"- {e}" for e in preserved_errors[:5])
    return VMessage(
        role=MessageRole.SYSTEM,
        content=[ContentBlock(kind="text", text="\n".join(lines))],
    )


def _fit_required_message(message: VMessage, budget: int) -> VMessage:
    """Mantém a mensagem obrigatória dentro do orçamento aproximado."""
    if budget <= 0 or _message_tokens(message) <= budget:
        return message
    text_blocks = [block for block in message.content if block.kind == "text"]
    if not text_blocks:
        return message
    # A estimativa do serviço de tokens varia por modelo; um limite de quatro
    # caracteres por token evita enviar uma mensagem inteira que já estourou o
    # orçamento, mantendo o início da solicitação para diagnóstico.
    max_chars = max(8, budget * 4)
    remaining = max_chars
    bounded = []
    for block in message.content:
        if block.kind != "text" or not block.text:
            bounded.append(block)
            continue
        excerpt = block.text[:remaining]
        remaining -= len(excerpt)
        bounded.append(replace(block, text=excerpt))
        if remaining <= 0:
            break
    bounded.append(
        ContentBlock(
            kind="text", text="\n[mensagem truncada pelo orçamento de contexto]"
        )
    )
    candidate = replace(message, content=bounded)
    while _message_tokens(candidate) > budget and max_chars > 8:
        max_chars = max(8, int(max_chars * 0.75))
        remaining = max_chars
        bounded = []
        for block in message.content:
            if block.kind != "text" or not block.text:
                bounded.append(block)
                continue
            excerpt = block.text[:remaining]
            remaining -= len(excerpt)
            bounded.append(replace(block, text=excerpt))
            if remaining <= 0:
                break
        bounded.append(
            ContentBlock(kind="text", text="\n[truncado pelo orçamento de contexto]")
        )
        candidate = replace(message, content=bounded)
    return candidate


def _fit_compaction_marker(
    omitted_count: int,
    *,
    preserved_errors: list[str],
    preserved_approvals: list[str],
    preserved_decisions: list[str],
    budget: int,
) -> VMessage:
    """Fit a compaction marker while retaining its semantic headings.

    A generic prefix truncation can remove the decisions/errors headings even
    though those are the information the marker exists to preserve.  Compact
    the entries first, then fall back to headings without entries before using
    the generic hard limit for an exceptionally small budget.
    """

    def compact_item(value: str) -> str:
        for prefix in (
            "Chamada de ferramentas: ",
            "Tool ",
            "Aprovação de ",
        ):
            value = value.removeprefix(prefix)
        if ": " in value and value.split(": ", 1)[0].lower().startswith("call_"):
            value = value.split(": ", 1)[1]
        return value

    candidates = [
        (preserved_decisions, preserved_approvals, preserved_errors),
        (
            [compact_item(item) for item in preserved_decisions],
            [compact_item(item) for item in preserved_approvals],
            [compact_item(item) for item in preserved_errors],
        ),
        ([], [], []),
    ]
    for decisions, approvals, errors in candidates:
        candidate = _format_compacted_marker(
            omitted_count,
            preserved_decisions=decisions,
            preserved_approvals=approvals,
            preserved_errors=errors,
        )
        if _message_tokens(candidate) <= budget:
            return candidate

    return _fit_required_message(
        _format_compacted_marker(
            omitted_count,
            preserved_decisions=preserved_decisions,
            preserved_approvals=preserved_approvals,
            preserved_errors=preserved_errors,
        ),
        budget,
    )


@dataclass
class PlannedContext:
    """Resultado do planejamento de contexto para envio ao modelo."""

    messages: list[VMessage]
    manifest: ContextManifest
    projection: ContextProjection | None = None
    untrusted_content: bool = False


class ContextPlanner:
    """Orquestrador de seleção, compactação e enriquecimento de contexto."""

    def __init__(self, memory_store: MemoryStore | None = None) -> None:
        self._memory_store = memory_store or get_memory_store()

    def plan_context(
        self,
        messages: list[VMessage],
        *,
        max_tokens: int,
        enabled: bool = True,
        preserve_latest_tool_result: bool = False,
        workspace_id: str | None = None,
        policy_snapshot: PolicySnapshot | None = None,
        persistent_memory_records: Sequence[MemoryRecord] | None = None,
    ) -> PlannedContext:
        """Seleciona deterministicamente o contexto ideal respeitando orçamentos e prioridades."""
        transcript_hash = compute_transcript_hash(messages)
        is_untrusted = policy_snapshot.untrusted_content if policy_snapshot else False

        blocks: list[ContextBlock] = []

        # 1. Recupera regras ou memórias de projeto relevantes
        project_memories = self._memory_store.retrieve(
            domain="project",
            scope_id=workspace_id or "global",
            limit=5,
        )

        injected_system_messages: list[VMessage] = []
        memory_scope = workspace_id or "global"
        trusted_records = [
            r
            for r in [
                *(persistent_memory_records or ()),
                *project_memories.records,
            ]
            if (
                r.domain == "project"
                and r.scope_id in (memory_scope, "global")
                and r.trust_level != "untrusted"
                and r.active
            )
        ][:5]
        if trusted_records:
            memory_texts = [
                f"- [{r.provenance or 'project'}]: {r.content}" for r in trusted_records
            ]
            mem_content = "[Memória de Projeto e Diretrizes]:\n" + "\n".join(
                memory_texts
            )
            injected_system_messages.append(
                VMessage(
                    role=MessageRole.SYSTEM,
                    content=[ContentBlock(kind="text", text=mem_content)],
                )
            )
            blocks.append(
                ContextBlock(
                    domain="project",
                    title="Diretrizes do Projeto",
                    token_count=_message_tokens(injected_system_messages[0]),
                    reason="Injeção de memória de projeto persistente",
                    provenance=workspace_id,
                )
            )

        full_messages = [*injected_system_messages, *messages]
        tokens_before = sum(_message_tokens(m) for m in full_messages)

        if not enabled or max_tokens <= 0 or len(full_messages) < 2:
            manifest = ContextManifest(
                blocks=[
                    *blocks,
                    ContextBlock(
                        domain="short_term",
                        title="Transcript Operacional Completo",
                        token_count=tokens_before,
                        reason="Histórico integral sem compactação",
                        untrusted_content=is_untrusted,
                    ),
                ],
                total_tokens=tokens_before,
                untrusted_content=is_untrusted,
            )
            return PlannedContext(
                messages=full_messages,
                manifest=manifest,
                untrusted_content=is_untrusted,
            )

        # 2. Aplica limitação segura em resultados de tool
        if preserve_latest_tool_result:
            latest_tool_idx = next(
                (
                    i
                    for i in reversed(range(len(full_messages)))
                    if full_messages[i].role == MessageRole.TOOL
                ),
                None,
            )
            if latest_tool_idx is not None:
                bounded = [
                    *_bound_tool_results(full_messages[:latest_tool_idx]),
                    full_messages[latest_tool_idx],
                    *_bound_tool_results(full_messages[latest_tool_idx + 1 :]),
                ]
            else:
                bounded = _bound_tool_results(full_messages)
        else:
            bounded = _bound_tool_results(full_messages)

        if sum(_message_tokens(m) for m in bounded) <= max_tokens:
            manifest = ContextManifest(
                blocks=[
                    *blocks,
                    ContextBlock(
                        domain="short_term",
                        title="Transcript Operacional",
                        token_count=sum(_message_tokens(m) for m in bounded),
                        reason="Dentro do orçamento de tokens",
                        untrusted_content=is_untrusted,
                    ),
                ],
                total_tokens=sum(_message_tokens(m) for m in bounded),
                untrusted_content=is_untrusted,
            )
            return PlannedContext(
                messages=bounded,
                manifest=manifest,
                untrusted_content=is_untrusted,
            )

        # 3. Compactação causal via conversation units preservando decisões e erros
        systems = [m for m in bounded if m.role == MessageRole.SYSTEM]
        non_system = [m for m in bounded if m.role != MessageRole.SYSTEM]
        units = _conversation_units(non_system)

        used_tokens = sum(_message_tokens(m) for m in systems)
        selected_units: list[list[VMessage]] = []

        baseline_marker = _marker(len(non_system))
        marker_cost = _message_tokens(baseline_marker)
        selection_budget = max_tokens - marker_cost

        latest_tool_unit = None
        if preserve_latest_tool_result:
            latest_tool_unit = next(
                (
                    unit
                    for unit in reversed(units)
                    if any(item.role is MessageRole.TOOL for item in unit)
                ),
                None,
            )

        for unit in reversed(units):
            cost = sum(_message_tokens(m) for m in unit)
            if used_tokens + cost > selection_budget:
                if not selected_units:
                    # A solicitação mais recente é obrigatória. Se ela sozinha
                    # estourar o orçamento, preservamos sua forma causal e
                    # truncamos apenas o conteúdo textual.
                    if len(unit) == 1 and unit[0].role is not MessageRole.TOOL:
                        available = max(1, selection_budget - used_tokens)
                        bounded = _fit_required_message(unit[0], available)
                        bounded_unit = [bounded]
                        units[units.index(unit)] = bounded_unit
                        selected_units.append(bounded_unit)
                        used_tokens += _message_tokens(bounded)
                break
            selected_units.append(unit)
            used_tokens += cost

        if (
            preserve_latest_tool_result
            and latest_tool_unit is not None
            and latest_tool_unit not in selected_units
        ):
            selected_units.append(latest_tool_unit)
            used_tokens += sum(_message_tokens(m) for m in latest_tool_unit)

        unit_order = {id(u): i for i, u in enumerate(units)}
        selected_units.sort(key=lambda u: unit_order[id(u)])
        selected_non_system = [m for unit in selected_units for m in unit]
        omitted = len(non_system) - len(selected_non_system)

        preserved_decisions: list[str] = []
        preserved_errors: list[str] = []
        preserved_approvals: list[str] = []

        if omitted > 0:
            selected_ids = {id(u) for u in selected_units}
            omitted_units = [u for u in units if id(u) not in selected_ids]
            for u in omitted_units:
                for m in u:
                    if m.role is MessageRole.ASSISTANT:
                        if m.tool_calls:
                            tool_names = ", ".join(
                                tc.name for tc in m.tool_calls if tc.name
                            )
                            if tool_names:
                                preserved_decisions.append(
                                    f"Chamada de ferramentas: {tool_names}"
                                )
                        elif m.finish_reason == "stop" and m.text().strip():
                            first_line = m.text().strip().splitlines()[0][:100]
                            if first_line:
                                preserved_decisions.append(first_line)
                    elif m.role is MessageRole.TOOL:
                        is_err = bool(
                            m.is_error
                            or m.finish_reason in {"tool_error", "failed"}
                            or any(getattr(b, "is_error", False) for b in m.content)
                        )
                        if is_err:
                            err_text = (
                                m.text().strip()[:100]
                                or "Falha na execução da ferramenta"
                            )
                            preserved_errors.append(
                                f"Tool {m.tool_call_id or ''}: {err_text}"
                            )
                    elif m.finish_reason in {"failed", "tool_error"}:
                        preserved_errors.append(
                            f"{m.role.value}: {m.text().strip()[:100]}"
                        )

                    extra_val = getattr(m, "extra", None)
                    extra_approval = (
                        extra_val.get("approval")
                        if isinstance(extra_val, dict)
                        else None
                    )
                    if extra_approval and isinstance(extra_approval, dict):
                        t_name = str(extra_approval.get("tool_name") or "ação")
                        dec = str(extra_approval.get("decision") or "aprovado")
                        preserved_approvals.append(f"Aprovação de {t_name}: {dec}")
                    elif (
                        m.role is MessageRole.TOOL
                        and "approval" in (m.tool_call_id or "").lower()
                    ):
                        preserved_approvals.append(
                            f"Aprovação registrada: {m.text().strip()[:100]}"
                        )

            marker_msg = _format_compacted_marker(
                omitted,
                preserved_errors=preserved_errors,
                preserved_approvals=preserved_approvals,
                preserved_decisions=preserved_decisions,
            )
            if not preserve_latest_tool_result:
                fixed_tokens = sum(_message_tokens(m) for m in systems)
                fixed_tokens += sum(_message_tokens(m) for m in selected_non_system)
                marker_msg = _fit_compaction_marker(
                    omitted,
                    preserved_decisions=preserved_decisions,
                    preserved_errors=preserved_errors,
                    preserved_approvals=preserved_approvals,
                    budget=max(1, max_tokens - fixed_tokens),
                )
                # The marker cost depends on the preserved decision/error
                # text, so reserving the cost of the one-token baseline
                # marker is insufficient.  If the fitted marker still leaves
                # the final context over budget, discard the oldest selected
                # messages and fit it again until the complete payload fits.
                while (
                    len(selected_units) > 1
                    and sum(_message_tokens(m) for m in systems)
                    + sum(_message_tokens(m) for m in selected_non_system)
                    + _message_tokens(marker_msg)
                    > max_tokens
                ):
                    # Drop one causal unit at a time so a tool call and its
                    # result can never be split by the compaction marker.
                    selected_units.pop(0)
                    selected_non_system = [
                        message for unit in selected_units for message in unit
                    ]
                    omitted = len(non_system) - len(selected_non_system)
                    fixed_tokens = sum(_message_tokens(m) for m in systems)
                    fixed_tokens += sum(_message_tokens(m) for m in selected_non_system)
                    marker_msg = _fit_compaction_marker(
                        omitted,
                        preserved_decisions=preserved_decisions,
                        preserved_errors=preserved_errors,
                        preserved_approvals=preserved_approvals,
                        budget=max(1, max_tokens - fixed_tokens),
                    )
            final_messages = [*systems, marker_msg, *selected_non_system]
        else:
            final_messages = [*systems, *selected_non_system]

        tokens_after = sum(_message_tokens(m) for m in final_messages)

        projection = ContextProjection(
            start_seq=0,
            end_seq=len(messages),
            source_transcript_hash=transcript_hash,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            preserved_decisions=preserved_decisions,
            preserved_errors=preserved_errors,
            preserved_approvals=preserved_approvals,
            excluded_items_count=omitted,
        )

        blocks.append(
            ContextBlock(
                domain="system",
                title="Instruções de Sistema",
                token_count=sum(_message_tokens(m) for m in systems),
                reason="Instruções e políticas obrigatórias",
            )
        )
        blocks.append(
            ContextBlock(
                domain="short_term",
                title="Histórico Selecionado",
                token_count=sum(_message_tokens(m) for m in selected_non_system),
                reason=f"Unidades causais recentes ({len(selected_units)} unidades mantidas)",
                untrusted_content=is_untrusted,
            )
        )

        manifest = ContextManifest(
            blocks=blocks,
            total_tokens=tokens_after,
            untrusted_content=is_untrusted,
            projection=projection,
        )

        return PlannedContext(
            messages=final_messages,
            manifest=manifest,
            projection=projection,
            untrusted_content=is_untrusted,
        )


_DEFAULT_PLANNER = ContextPlanner()


def plan_context(
    messages: list[VMessage],
    *,
    max_tokens: int,
    enabled: bool = True,
    preserve_latest_tool_result: bool = False,
    workspace_id: str | None = None,
    policy_snapshot: PolicySnapshot | None = None,
    persistent_memory_records: Sequence[MemoryRecord] | None = None,
) -> PlannedContext:
    """Função conveniente para planejar o contexto de um turno."""
    return _DEFAULT_PLANNER.plan_context(
        messages,
        max_tokens=max_tokens,
        enabled=enabled,
        preserve_latest_tool_result=preserve_latest_tool_result,
        workspace_id=workspace_id,
        policy_snapshot=policy_snapshot,
        persistent_memory_records=persistent_memory_records,
    )
