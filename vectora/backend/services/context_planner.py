"""Planejador Unificado de Contexto e Memória (ContextPlanner).

Seleciona mensagens, projeções, memórias e arquivos por relevância e
preserva a integridade causal de tool-calls/tool-results e instruções do sistema.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from backend.services.context_compaction import (
    _bound_tool_results,
    _conversation_units,
    _marker,
    _message_tokens,
)
from backend.services.context_manifest import (
    ContextBlock,
    ContextManifest,
    ContextProjection,
)
from backend.services.memory_store import MemoryStore, get_memory_store
from backend.services.policy_compiler import PolicySnapshot
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


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
        trusted_records = [
            r for r in project_memories.records if r.trust_level != "untrusted"
        ]
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
                    # A unidade mais recente é obrigatória.
                    selected_units.append(unit)
                    used_tokens += cost
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
) -> PlannedContext:
    """Função conveniente para planejar o contexto de um turno."""
    return _DEFAULT_PLANNER.plan_context(
        messages,
        max_tokens=max_tokens,
        enabled=enabled,
        preserve_latest_tool_result=preserve_latest_tool_result,
        workspace_id=workspace_id,
        policy_snapshot=policy_snapshot,
    )
