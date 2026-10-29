"""Tools consultivas para inspeção e avaliação governada de Decision Patterns (SystemOne).

Regras de segurança:
- Aceita exclusivamente patterns registrados no `DEFAULT_PATTERN_REGISTRY`.
- Obtém `workspace_id` e sessão diretamente do `ToolContext` autenticado.
- Não expõe mutação de policy, orçamento, modos ou registry a agentes.
- Nenhuma tool deste módulo altera estado externo ou autoriza execução autônoma.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.llm.systemone.harness import SystemOneHarness, WorkspaceBudgetTracker
from backend.llm.systemone.patterns import DEFAULT_PATTERN_REGISTRY
from backend.llm.systemone.settings import SystemOneSettings, get_systemone_provider
from backend.llm.systemone.types import DecisionProviderType
from backend.scheduling import kanban
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_SHARED_TOOL_BUDGET = WorkspaceBudgetTracker(
    default_limit_tokens=50_000, window_seconds=3600.0
)
_DEFAULT_HARNESS_CACHE: tuple[tuple[object, ...], SystemOneHarness] | None = None


def _resolve_authenticated_workspace(ctx: ToolContext) -> str | None:
    """Extrai o identificador de escopo autenticado do `ToolContext`."""
    ws = getattr(ctx, "workspace_id", None) or getattr(ctx, "thread_id", None)
    if not ws or not str(ws).strip():
        return None
    return str(ws).strip()


def _build_default_harness(
    provider_override: DecisionProviderType | None = None,
) -> SystemOneHarness:
    """Constrói ou reutiliza uma instância governada do `SystemOneHarness` a partir da configuração ativa."""
    global _DEFAULT_HARNESS_CACHE
    try:
        from backend.settings import settings

        s1_settings = SystemOneSettings(
            enabled=settings.systemone_enabled,
            provider=provider_override or settings.systemone_provider,
            local_base_url=settings.systemone_local_base_url,
            local_model=settings.systemone_local_model,
            laya_base_url=settings.systemone_laya_base_url,
            laya_model=settings.systemone_laya_model,
            laya_api_key=settings.systemone_laya_api_key,
            clef_api_base_url=settings.systemone_clef_api_base_url,
            clef_account_id=settings.systemone_clef_account_id,
            clef_api_model=settings.systemone_clef_api_model,
            jev_api_base_url=settings.systemone_jev_api_base_url,
            jev_api_model=settings.systemone_jev_api_model,
            allow_remote_fallback=settings.systemone_allow_remote_fallback,
            confidence_threshold_hitl=settings.systemone_confidence_threshold_hitl,
        )
        clef_key = settings.clef_api_key
        jev_key = settings.jev_api_key
    except Exception:
        s1_settings = SystemOneSettings(enabled=False)
        clef_key = jev_key = None

    cache_key = (
        s1_settings.enabled,
        s1_settings.provider,
        s1_settings.local_base_url,
        s1_settings.local_model,
        s1_settings.laya_base_url,
        s1_settings.laya_model,
        s1_settings.laya_api_key,
        s1_settings.clef_api_base_url,
        s1_settings.clef_api_model,
        s1_settings.clef_account_id,
        s1_settings.jev_api_base_url,
        s1_settings.jev_api_model,
        s1_settings.allow_remote_fallback,
        s1_settings.confidence_threshold_hitl,
        clef_key,
        jev_key,
    )
    if _DEFAULT_HARNESS_CACHE is not None and _DEFAULT_HARNESS_CACHE[0] == cache_key:
        return _DEFAULT_HARNESS_CACHE[1]

    provider = get_systemone_provider(
        s1_settings,
        clef_api_key=clef_key,
        jev_api_key=jev_key,
    )
    harness = SystemOneHarness(
        s1_settings,
        provider,
        registry=DEFAULT_PATTERN_REGISTRY,
        budget_tracker=_SHARED_TOOL_BUDGET,
    )
    _DEFAULT_HARNESS_CACHE = (cache_key, harness)
    return harness


@vtool(extras=ToolExtras(destructive=False, category="planning", icon="cpu"))
async def systemone_list_patterns(ctx: ToolContext) -> str:
    """Lista os Decision Patterns versionados registrados no catálogo SystemOne.

    Somente leitura — retorna metadados de entrada, tipo de pergunta e modo operacional
    sem permitir alterações de policy ou registry.
    """
    ws_id = _resolve_authenticated_workspace(ctx)
    if ws_id is None:
        return json.dumps(
            {"status": "error", "error": "contexto de workspace/sessão não autenticado"}
        )

    patterns = [
        {
            "pattern_id": spec.pattern_id,
            "version": spec.version,
            "owner": spec.owner,
            "primary_question_type": spec.primary_question_type,
            "required_fields": list(spec.input_spec.required_fields),
            "default_mode": spec.operation_spec.default_mode,
            "description": spec.description,
        }
        for spec in DEFAULT_PATTERN_REGISTRY.list_patterns()
    ]
    return json.dumps(
        {
            "status": "ok",
            "workspace_id": ws_id,
            "patterns": patterns,
        },
        ensure_ascii=False,
    )


@vtool(extras=ToolExtras(destructive=False, category="planning", icon="cpu"))
async def systemone_evaluate_pattern(
    ctx: ToolContext,
    pattern_id: str,
    state: dict[str, Any],
    pattern_version: str | None = None,
) -> str:
    """Executa uma avaliação consultiva sobre um Decision Pattern registrado.

    Args:
        pattern_id: Identificador canônico de um pattern registrado.
        state: Dicionário estruturado contendo as evidências obrigatórias do pattern.
        pattern_version: Versão opcional do pattern (padrão: versão ativa mais recente).

    Returns:
        JSON com recomendação estruturada, resultado de policy e `execution_authorized=False`.
    """
    ws_id = _resolve_authenticated_workspace(ctx)
    if ws_id is None:
        return json.dumps(
            {"status": "error", "error": "contexto de workspace/sessão não autenticado"}
        )

    try:
        DEFAULT_PATTERN_REGISTRY.get(pattern_id, version=pattern_version)
    except KeyError:
        return json.dumps(
            {
                "status": "error",
                "error": f"pattern {pattern_id!r} não registrado no catálogo",
            },
            ensure_ascii=False,
        )

    extra = getattr(ctx, "_extra", None)
    harness = extra.get("systemone_harness") if isinstance(extra, dict) else None
    if not isinstance(harness, SystemOneHarness):
        harness = _build_default_harness()
        if isinstance(extra, dict):
            extra["systemone_harness"] = harness

    try:
        result = await harness.decide_pattern(
            pattern_id,
            state,
            workspace_id=ws_id,
            pattern_version=pattern_version,
        )
    except Exception as exc:
        logger.warning("systemone_tools: falha ao avaliar pattern", exc_info=True)
        return json.dumps(
            {
                "status": "error",
                "error": f"falha de validação ou execução: {exc}",
            },
            ensure_ascii=False,
        )

    answers_dump = (
        {k: v.model_dump() for k, v in result.response.answers.items()}
        if result.response is not None
        else {}
    )
    return json.dumps(
        {
            "status": "ok",
            "decision_id": result.decision_id,
            "pattern_id": result.pattern_id,
            "pattern_version": result.pattern_version,
            "mode": result.mode,
            "outcome": result.policy_result.outcome,
            "requires_hitl": result.policy_result.requires_hitl,
            "is_usable_recommendation": result.is_usable_recommendation,
            "execution_authorized": False,
            "state_version": result.state_version,
            "reason_codes": result.reason_codes,
            "answers": answers_dump,
        },
        ensure_ascii=False,
    )


@vtool(extras=ToolExtras(destructive=False, category="workspace", icon="trello"))
async def systemone_propose_kanban_transition(
    ctx: ToolContext,
    task_id: str,
    evidence_summary: str = "",
) -> str:
    """Propõe de forma consultiva uma transição válida para um card Kanban da sessão atual.

    Não move o card nem altera o estado no banco; vincula a recomendação ao `card_version`.

    Args:
        task_id: ID do card Kanban pertencente à sessão atual.
        evidence_summary: Resumo estruturado das evidências observadas.
    """
    ws_id = _resolve_authenticated_workspace(ctx)
    session_id = getattr(ctx, "thread_id", None)
    if ws_id is None or not session_id:
        return json.dumps(
            {"status": "error", "error": "sessão ou workspace ausente no contexto"}
        )

    from backend.llm.systemone.preferences import (
        decision_model_enabled_for,
        selected_decision_model_provider,
    )

    if not decision_model_enabled_for(ctx, "kanban"):
        return json.dumps(
            {
                "status": "disabled",
                "error": "Decision Model não está habilitado para decisões do Kanban",
            },
            ensure_ascii=False,
        )

    extra = getattr(ctx, "_extra", None)
    harness = extra.get("systemone_harness") if isinstance(extra, dict) else None
    if not isinstance(harness, SystemOneHarness):
        harness = _build_default_harness(selected_decision_model_provider(ctx))
        if isinstance(extra, dict):
            extra["systemone_harness"] = harness

    try:
        proposal = await kanban.propose_transition_with_systemone(
            task_id,
            harness=harness,
            workspace_id=ws_id,
            evidence_summary=evidence_summary,
            authorized_session_id=session_id,
        )
        return json.dumps({"status": "ok", "proposal": proposal}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps(
            {"status": "error", "error": str(exc)},
            ensure_ascii=False,
        )
