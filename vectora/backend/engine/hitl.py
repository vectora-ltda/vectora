"""``should_require_approval`` — política HITL nativa, como função pura.

Chamada IDENTICAMENTE pelo loop principal (``backend/engine/
conversation_loop.py``) e por qualquer subagente — a mesma função decide
pausa pra qualquer chamador, sem depender de herança de middleware ou de
grafo compilado.

Sobrevivência a restart: a persistência SÍNCRONA da aprovação pendente
(``SessionStore.put_pending_approval``, ANTES de qualquer espera) é o que
dá o invariante — este módulo só decide SE pausa, ``ApprovalGate`` (abaixo)
decide COMO persistir/esperar.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from backend.vtypes.contracts import ToolOption
from backend.vtypes.message import MessageRole

if TYPE_CHECKING:
    from backend.llm.systemone.harness import SystemOneHarness
    from backend.persistence.native.session_store import SessionStore
    from backend.tools.context import ToolContext
    from backend.vtypes.message import VMessage

logger = logging.getLogger(__name__)


def _log_decision(
    tool_name: str,
    ctx: ToolContext,
    *,
    decision: bool,
    mode: str,
    reason: str,
) -> None:
    """Registra a decisão efetiva do gate com contexto auditável."""
    logger.info(
        "hitl.decision tool=%s decision=%s mode=%s reason=%s",
        tool_name,
        decision,
        mode,
        reason,
        extra={
            "tool_name": tool_name,
            "decision": decision,
            "mode": mode,
            "reason": reason,
            "thread_id": ctx.thread_id,
            "workspace_id": ctx.workspace_id,
        },
    )


#: Tools destrutivas candidatas a pausar o loop para aprovação.
REQUIRE_APPROVAL: frozenset[str] = frozenset(
    {
        "terminal",
        "terminal_tool",
        "write_terminal",
        "file_write",
        "file_write_tool",
        "file_edit",
        "install_learned_skill",
        "save_learned_fact",
        "install_mcp_from_registry",
        "install_skill_from_catalog",
        "install_memory_bucket",
        "uninstall_mcp",
        "delete_skill",
        "publish_memory_bucket_tool",
        "save_mcp_env_var",
        "web_crawl",
        "web_map",
        "ha_call_service",
        "computer_use",
        "select_desktop_window",
        "focus_desktop_window",
        "kanban_create",
        "kanban_update_status",
        "apply_memory_consolidation",
        "git_push",
        "call_mcp_tool",
        # Operações com custo variável: a quota e o preço estimado são
        # apresentados ao usuário antes da execução do provider.
        "generate_image",
        "text_to_speech",
        "generate_video",
    }
)

#: Tools auto-aprovadas no modo "accept_edits".
_ACCEPT_EDITS_AUTO: frozenset[str] = frozenset(
    {"file_write", "file_write_tool", "file_edit"}
)

#: Modos que nunca interrompem (rodam autônomos).
_NON_INTERRUPTING_MODES: frozenset[str] = frozenset({"auto", "bypass"})

#: Dentro de workspace jailed, o worker sandboxed já é o backstop real —
#: pausar aqui é fricção redundante.
_JAILED_BYPASS_TOOLS: frozenset[str] = frozenset(
    {"terminal", "terminal_tool", "file_write", "file_write_tool", "file_edit"}
)

#: `computer_use` nunca tem "desfazer" — pausa sempre, mesmo em bypass/auto.
_ALWAYS_INTERRUPT: frozenset[str] = frozenset(
    {"computer_use", "select_desktop_window", "focus_desktop_window"}
)


def _workspace_is_jailed(workspace_id: str) -> bool:
    """True se `workspace_id` tem `[sandbox]` habilitado em `vectora.toml`.

    Defensivo: qualquer erro de I/O ou workspace desconhecida volta `False`
    — nunca relaxa HITL por engano."""
    if not workspace_id:
        return False
    try:
        from pathlib import Path

        from backend.sandbox.policy import parse_policy
        from backend.workspace.workspace import workspace_registry

        ws = workspace_registry.get(workspace_id)
        cwd = getattr(ws, "cwd", None)
        if not cwd:
            return False
        return parse_policy(Path(cwd) / "vectora.toml").enabled
    except Exception:
        logger.debug("hitl: falha ao checar sandbox da workspace %s", workspace_id)
        return False


def _is_self_kanban_update(
    ctx: ToolContext, tool_name: str, args: dict[str, Any]
) -> bool:
    """True se `kanban_update_status` está mudando o status da PRÓPRIA task
    em segundo plano — pedir aprovação criaria um loop esperando a própria
    run pausada aprovar a si mesma."""
    if tool_name != "kanban_update_status":
        return False
    bg_task_id = ctx.background_task_id
    if not bg_task_id:
        return False
    call_task_id = args.get("task_id", "") if isinstance(args, dict) else ""
    return bool(call_task_id) and call_task_id == bg_task_id


def _plan_mode_ja_passou_neste_turno(history: list[VMessage]) -> bool:
    """Varre `history` de trás pra frente até a última mensagem USER; se já
    existe uma mensagem TOOL de uma tool de `REQUIRE_APPROVAL` depois dela,
    o gate já foi passado neste turno — não interrompe de novo."""
    last_user_idx = -1
    for idx, msg in enumerate(history):
        if msg.role == MessageRole.USER:
            last_user_idx = idx

    for msg in history[last_user_idx + 1 :]:
        if msg.role == MessageRole.TOOL and _tool_requires_approval(msg.name or ""):
            return True
    return False


def _mode_should_interrupt(mode: str, tool_name: str, history: list[VMessage]) -> bool:
    """Política canônica dos 5 modos — fonte única de verdade do HITL nativo."""
    requires_approval = _tool_requires_approval(tool_name)
    always_interrupt = tool_name in _ALWAYS_INTERRUPT
    if mode in _NON_INTERRUPTING_MODES:
        decision = False
    elif mode == "accept_edits":
        decision = tool_name not in _ACCEPT_EDITS_AUTO
    elif mode == "plan":
        # Cada operação de mídia acima do limite precisa de sua própria
        # aprovação; um resultado anterior nunca autoriza outra cobrança.
        decision = tool_name in {"generate_image", "text_to_speech", "generate_video"}
        if not decision:
            decision = not _plan_mode_ja_passou_neste_turno(history)
    else:
        decision = True  # "ask" ou desconhecido → mais restritivo
    return requires_approval and (always_interrupt or decision)


def _tool_requires_approval(tool_name: str) -> bool:
    """Resolve the approval candidate from the canonical tool metadata.

    ``REQUIRE_APPROVAL`` remains a compatibility fallback for tools that are
    intentionally resolved before their native module is imported. Once a
    tool is registered, ``ToolExtras.destructive`` is the authoritative
    contract shared by the registry, batching policy, UI metadata and HITL.
    This prevents a newly added destructive tool from silently bypassing the
    approval gate just because somebody forgot to update a second name list.
    """
    if tool_name in REQUIRE_APPROVAL:
        return True
    try:
        from backend.tools.registry import TOOL_REGISTRY

        spec = TOOL_REGISTRY.get(tool_name)
    except Exception:
        logger.warning(
            "hitl: unable to resolve tool metadata",
            extra={"tool_name": tool_name},
        )
        return True
    return spec is not None and spec.extras.destructive


def should_require_approval(
    tool_name: str,
    ctx: ToolContext,
    args: dict[str, Any],
    history: list[VMessage],
) -> bool:
    """Predicate único do HITL nativo — mesma assinatura que `run_conversation`
    já aceita opcionalmente em `should_require_approval`."""
    mode = ctx.permission_mode or "ask"
    if _is_self_kanban_update(ctx, tool_name, args):
        _log_decision(
            tool_name,
            ctx,
            decision=False,
            mode=mode,
            reason="self_kanban_update",
        )
        return False
    if tool_name in {"generate_image", "text_to_speech", "generate_video"}:
        # O limiar é definido pelo backend autenticado no contexto. Ausência
        # do campo mantém o comportamento seguro: toda operação pede revisão.
        threshold = getattr(ctx, "_extra", {}).get("media_approval_threshold")
        if isinstance(threshold, int | float) and not isinstance(threshold, bool):
            from backend.services.media_quota import media_estimate_record

            provider, _, model = ctx.model.partition(":")
            estimate = media_estimate_record(tool_name, provider=provider, model=model)
            if estimate.units <= threshold:
                from backend.persistence.telemetry import telemetry

                telemetry.record_media_quota(
                    "hitl_decision",
                    operation=estimate.operation,
                    provider=estimate.provider,
                    model=estimate.model,
                    estimate_version=estimate.version,
                    units=estimate.units,
                    result="auto_approved",
                )
                _log_decision(
                    tool_name,
                    ctx,
                    decision=False,
                    mode=mode,
                    reason="media_below_threshold",
                )
                return False
            # Custos acima do limiar exigem aprovação em qualquer modo,
            # inclusive `auto` e `bypass`.
            _log_decision(
                tool_name,
                ctx,
                decision=True,
                mode=mode,
                reason="media_above_threshold",
            )
            return True
        # Sem um limiar confiável, falha fechada: o custo deve ser
        # apresentado ao usuário antes da execução.
        _log_decision(
            tool_name,
            ctx,
            decision=True,
            mode=mode,
            reason="media_threshold_unavailable",
        )
        return True
    if tool_name == "git_push":
        force = bool(args.get("force", False))
        if force:
            _log_decision(
                tool_name,
                ctx,
                decision=True,
                mode=mode,
                reason="git_force_push",
            )
            return True
    if tool_name == "http_request":
        method = str(args.get("method", "GET")).upper()
        if (
            method in ("POST", "PUT", "PATCH", "DELETE")
            and mode not in _NON_INTERRUPTING_MODES
        ):
            _log_decision(
                tool_name,
                ctx,
                decision=True,
                mode=mode,
                reason="mutable_http_request",
            )
            return True
    if tool_name == "call_mcp_tool" and mode not in _NON_INTERRUPTING_MODES:
        _log_decision(
            tool_name,
            ctx,
            decision=True,
            mode=mode,
            reason="mcp_tool_invocation",
        )
        return True
    if tool_name in _JAILED_BYPASS_TOOLS and _workspace_is_jailed(ctx.workspace_id):
        _log_decision(
            tool_name,
            ctx,
            decision=False,
            mode=mode,
            reason="workspace_jailed",
        )
        return False
    decision = _mode_should_interrupt(mode, tool_name, history)
    reason = "policy"
    if not decision:
        extra = getattr(ctx, "_extra", None)
        if isinstance(extra, dict):
            elevated = extra.get("systemone_risk_elevated_calls")
            if (
                isinstance(elevated, set)
                and _risk_assessment_key(tool_name, args) in elevated
            ):
                decision = True
                reason = "systemone_risk_elevated"
            elif (
                elevated is None
                and extra.get("systemone_risk_requires_approval") is True
            ):
                decision = True
                reason = "systemone_risk_elevated"
    _log_decision(
        tool_name,
        ctx,
        decision=decision,
        mode=mode,
        reason=reason,
    )
    return decision


def _risk_assessment_key(tool_name: str, args: dict[str, Any]) -> str:
    payload = json.dumps(args, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{tool_name}:{digest}"


async def precompute_tool_risk_assessment(
    harness: SystemOneHarness,
    *,
    tool_name: str,
    ctx: ToolContext,
    args: dict[str, Any],
    risk_score_threshold: float = 0.70,
) -> bool:
    """Calcula a avaliação de risco no fluxo assíncrono anterior ao predicado síncrono `should_require_approval`.

    A avaliação só pode AUMENTAR a exigência de revisão humana (nunca reduzir uma
    aprovação já exigida pelas regras estáticas).
    """
    extra = getattr(ctx, "_extra", None)
    if not isinstance(extra, dict):
        return False

    workspace_id = getattr(ctx, "workspace_id", None) or "default"
    mode = getattr(ctx, "permission_mode", None) or "ask"
    safe_keys = sorted(str(k) for k in args)
    args_summary = f"keys={safe_keys}"

    try:
        res = await harness.assess_tool_risk(
            workspace_id=workspace_id,
            tool_name=tool_name,
            args_summary=args_summary,
            permission_mode=mode,
        )
    except Exception:
        logger.warning("hitl: falha ao pré-calcular risco via SystemOne", exc_info=True)
        return False

    if res.mode in ("off", "shadow"):
        return False

    elevate = False
    if res.policy_result.requires_hitl:
        elevate = True
    elif res.response is not None:
        ans = res.response.answers.get("risk_score")
        score = getattr(ans, "score", 0.0)
        if isinstance(score, int | float) and score >= risk_score_threshold:
            elevate = True

    elevated = extra.setdefault("systemone_risk_elevated_calls", set())
    key = _risk_assessment_key(tool_name, args)
    if elevate:
        elevated.add(key)
        extra["systemone_risk_requires_approval"] = True
    else:
        elevated.discard(key)
        extra["systemone_risk_requires_approval"] = bool(elevated)
    return elevate


_MAX_NON_TOOL_REVIEW_RECORDS = 500
_NON_TOOL_REVIEW_RECORDS: deque[dict[str, Any]] = deque(
    maxlen=_MAX_NON_TOOL_REVIEW_RECORDS
)


def record_non_tool_review_request(
    *,
    workspace_id: str,
    consumer: str,
    pattern_id: str,
    decision_id: str,
    state_version: str,
    reason: str,
) -> dict[str, Any]:
    """Registra em memória (buffer circular limitado ao processo atual) revisão para decisões sem tool call."""
    record = {
        "workspace_id": workspace_id,
        "consumer": consumer,
        "pattern_id": pattern_id,
        "decision_id": decision_id,
        "state_version": state_version,
        "reason": reason,
        "status": "pending_review",
    }
    _NON_TOOL_REVIEW_RECORDS.append(record)
    return dict(record)


def list_non_tool_review_requests(
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Lista registros de revisão pendentes para consumidores que não usam tool calls."""
    if workspace_id is None:
        return [dict(r) for r in _NON_TOOL_REVIEW_RECORDS]
    return [
        dict(r) for r in _NON_TOOL_REVIEW_RECORDS if r["workspace_id"] == workspace_id
    ]


class ApprovalGate:
    """1 instância por processo. `request_approval` persiste IMEDIATA e
    SINCRONAMENTE em `pending_approvals` (o que sobrevive a restart) — o
    `asyncio.Event` local é só um fast-path opcional pra quando o processo
    que pausou é o mesmo que recebe o resume, evitando poll de DB nesse
    caso comum; não é ele que garante sobrevivência a restart, a
    persistência síncrona é quem garante."""

    def __init__(self, session_store: SessionStore) -> None:
        self._session_store = session_store
        self._events: dict[str, Any] = {}
        self._ephemeral_args: dict[str, dict[str, Any]] = {}

    async def request_approval(
        self,
        thread_id: str,
        *,
        interrupt_id: str,
        tool_name: str,
        tool_call_id: str,
        args: dict[str, Any],
        reasoning: str | None = None,
        options: list[ToolOption] | None = None,
        priority: int = 0,
        expires_at: str | None = None,
        approval_metadata: dict[str, Any] | None = None,
    ) -> None:
        import asyncio

        safe_args = dict(args)
        if tool_name == "write_terminal":
            raw_input = str(safe_args.pop("input_data", ""))
            safe_args["input_preview"] = "<redacted>"
            safe_args["input_length"] = len(raw_input.encode("utf-8"))
            self._ephemeral_args[interrupt_id] = dict(args)
        elif tool_name in {"generate_image", "text_to_speech", "generate_video"}:
            self._ephemeral_args[interrupt_id] = dict(args)
            safe_args = dict(approval_metadata or {})
        logger.info(
            "hitl.request",
            extra={
                "tool_name": tool_name,
                "priority": priority,
                "has_options": bool(options),
            },
        )
        if tool_name in {"generate_image", "text_to_speech", "generate_video"}:
            from backend.persistence.telemetry import telemetry

            telemetry.record_media_quota(
                "hitl_requested",
                operation=str((approval_metadata or {}).get("operation", tool_name)),
                provider=str((approval_metadata or {}).get("provider", "")),
                model=str((approval_metadata or {}).get("model", "")),
                units=(approval_metadata or {}).get("estimated_units"),
                idempotency_key=(approval_metadata or {}).get("idempotency_key"),
                result="pending",
            )
        await self._session_store.put_pending_approval(
            thread_id,
            interrupt_id=interrupt_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=safe_args,
            reasoning=reasoning,
            options=options,
            priority=priority,
            expires_at=expires_at,
        )
        self._events[thread_id] = asyncio.Event()

    def ephemeral_args(self, interrupt_id: str) -> dict[str, Any] | None:
        """Return raw approval arguments only while the process is alive."""
        args = self._ephemeral_args.get(interrupt_id)
        return dict(args) if args is not None else None

    async def wait_for_resume(self, thread_id: str, *, timeout_s: float) -> bool:
        """Espera o fast-path local (mesmo processo resolve o resume) até
        `timeout_s`. `False` no timeout não é erro — o resume pode chegar
        por uma request HTTP nova (processo reiniciado, ou só um handler
        diferente), que consulta `pending_approvals` direto via
        `SessionStore.get_pending_approval`, sem depender deste evento."""
        import asyncio

        event = self._events.get(thread_id)
        if event is None:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_s)
            return True
        except TimeoutError:
            return False

    async def resolve(self, thread_id: str, *, interrupt_id: str | None = None) -> None:
        """Libera o fast-path local e limpa a aprovação pendente
        persistida — chamado depois que a decisão (approve/reject/edit) já
        foi processada e o resultado já foi persistido no histórico."""
        if interrupt_id is not None:
            self._ephemeral_args.pop(interrupt_id, None)
        pending = await self._session_store.get_pending_approval(thread_id)
        if pending is not None and interrupt_id is None:
            self._ephemeral_args.pop(pending["interrupt_id"], None)
        await self._session_store.clear_pending_approval(thread_id)
        event = self._events.pop(thread_id, None)
        if event is not None:
            event.set()
