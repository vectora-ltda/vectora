"""Testes unitários para SystemOneHarness, DecisionPatternRegistry, consumidores e HITL."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from backend.engine.goal_mode import GateResult, evaluate_goal_with_systemone
from backend.engine.hitl import (
    list_non_tool_review_requests,
    precompute_tool_risk_assessment,
    record_non_tool_review_request,
    should_require_approval,
)
from backend.llm.systemone.errors import (
    SystemOneConnectionError,
    SystemOneRateLimitError,
    SystemOneValidationError,
)
from backend.llm.systemone.harness import (
    SystemOneHarness,
    WorkspaceBudgetTracker,
)
from backend.llm.systemone.patterns import (
    DEFAULT_PATTERN_REGISTRY,
    DecisionPatternRegistry,
    build_default_patterns,
    serialize_canonical_state,
)
from backend.llm.systemone.preferences import (
    decision_model_enabled_for,
    decision_model_surfaces_for_tool,
)
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.settings import SystemOneSettings
from backend.llm.systemone.types import (
    CapabilityEvidenceLevel,
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ProviderCapabilities,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneMetadata,
    SystemOneRequest,
    SystemOneResponse,
    SystemOneUsage,
)
from backend.scheduling import kanban
from backend.tools.context import ToolContext
from backend.tools.rag import SystemOneRerank
from backend.tools.systemone_tools import (
    systemone_evaluate_pattern,
    systemone_list_patterns,
    systemone_propose_kanban_transition,
)
from backend.vtypes.documents import Document


class FakeDecisionProvider(SystemOneProvider):
    """Provedor falso determinístico para testes de contrato e resiliência do harness."""

    def __init__(
        self,
        provider_name: str = "clef-local",
        *,
        supported_models: list[str] | None = None,
        evidence: CapabilityEvidenceLevel = "observed",
        delay_s: float = 0.0,
    ) -> None:
        """Inicializa o estado da instância."""
        self._provider_name = provider_name
        self._supported_models = supported_models or [
            "clef-flash",
            "@cf/cloudflare/clef-flash",
            "jev-fast",
        ]
        self._evidence: CapabilityEvidenceLevel = evidence
        self._delay_s = delay_s
        self.calls: list[SystemOneRequest] = []
        self.error_to_raise: Exception | None = None
        self.custom_answers: dict[str, Any] | None = None
        self.custom_decide_fn: Any = None
        self.usage_tokens: int = 40

    @property
    def provider_id(self) -> str:
        """Executa a operação provider id."""
        return self._provider_name

    async def capabilities(self) -> ProviderCapabilities:
        """Executa a operação capabilities."""
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=False,
            supports_video=False,
            supported_models=self._supported_models,
            evidence=self._evidence,
        )

    async def health(self) -> bool:
        """Executa a operação health."""
        return self.error_to_raise is None

    async def decide(self, request: SystemOneRequest) -> SystemOneResponse:
        """Executa a operação decide."""
        self.calls.append(request)
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        if self.error_to_raise is not None:
            raise self.error_to_raise
        if self.custom_decide_fn is not None:
            return await self.custom_decide_fn(request)

        answers: dict[str, Any] = {}
        if self.custom_answers is not None:
            answers = dict(self.custom_answers)
        else:
            for q_id, q in request.questions.items():
                if isinstance(q, NoulQuestion):
                    answers[q_id] = NoulAnswer(probability=0.90, confidence=0.92)
                elif isinstance(q, ChoiceQuestion):
                    first = q.options[0]
                    rest = q.options[1:]
                    dist = {first: 0.85}
                    rem = 0.15 / len(rest)
                    for opt in rest:
                        dist[opt] = rem
                    answers[q_id] = ChoiceAnswer(
                        selection=first,
                        distribution=dist,
                        confidence=0.91,
                    )
                elif isinstance(q, ScoreQuestion):
                    mid = (q.min_score + q.max_score) / 2.0
                    answers[q_id] = ScoreAnswer(
                        score=mid,
                        min_score=q.min_score,
                        max_score=q.max_score,
                        confidence=0.90,
                    )

        return SystemOneResponse(
            answers=answers,
            metadata=SystemOneMetadata(
                provider=self.provider_id,
                model=request.model,
                latency_ms=2.0,
                usage=SystemOneUsage(
                    prompt_tokens=25,
                    completion_tokens=15,
                    total_tokens=self.usage_tokens,
                ),
            ),
        )


def test_default_registry_contains_all_12_canonical_patterns() -> None:
    """Verifica que todos os 12 patterns canônicos estão registrados com metadados completos."""
    patterns = build_default_patterns()
    ids = {p.pattern_id for p in patterns}
    expected = {
        "goal_completion_noul",
        "goal_stagnation_choice",
        "tool_risk_assessment_score",
        "kanban_transition_choice",
        "kanban_block_classifier",
        "subagent_routing_choice",
        "rag_rerank_scoring",
        "evidence_support_choice",
        "context_retention_choice",
        "retry_disposition_choice",
        "duplicate_task_noul",
        "browser_dom_action_choice",
    }
    assert ids == expected
    for spec in patterns:
        assert spec.evaluation_spec.dataset_id
        assert spec.evaluation_spec.calibration_version == "uncalibrated-v1"
        assert DEFAULT_PATTERN_REGISTRY.get(spec.pattern_id).version == "v1"


def test_canonical_state_validation_rejects_missing_fields_and_oversized_state() -> (
    None
):
    """Verifica o comportamento de canonical state validation rejects missing fields and oversized state."""
    spec = DEFAULT_PATTERN_REGISTRY.get("kanban_block_classifier")
    with pytest.raises(SystemOneValidationError, match="Campos obrigatórios ausentes"):
        serialize_canonical_state({"task_id": "t-1"}, spec)

    with pytest.raises(SystemOneValidationError, match="excede o limite"):
        serialize_canonical_state(
            {"task_id": "t-1", "error_summary": "x" * 10000},
            spec,
        )


@pytest.mark.asyncio
async def test_harness_separates_recommendation_from_authorization() -> None:
    """Verifica o comportamento de harness separates recommendation from authorization."""
    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    harness = SystemOneHarness(settings, provider)

    # Em modo shadow (padrão), a recomendação nunca é marcada como utilizável
    shadow_res = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="task-1",
        error_summary="Aguardando credencial do usuário",
    )
    assert shadow_res.mode == "shadow"
    assert shadow_res.policy_result.outcome == "accepted"
    assert shadow_res.is_usable_recommendation is False
    assert shadow_res.execution_authorized is False
    assert "shadow_mode_observation_only" in shadow_res.reason_codes

    # Em modo enforced, is_usable_recommendation=True mas execution_authorized continua False
    harness.set_mode("kanban_block_classifier", "enforced", workspace_id="ws-1")
    enforced_res = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="task-1",
        error_summary="Aguardando credencial do usuário",
    )
    assert enforced_res.mode == "enforced"
    assert enforced_res.is_usable_recommendation is True
    assert enforced_res.execution_authorized is False


@pytest.mark.asyncio
async def test_harness_cache_isolation_and_policy_reapplication() -> None:
    """Verifica o comportamento de harness cache isolation and policy reapplication."""
    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    harness = SystemOneHarness(settings, provider)
    harness.set_mode("kanban_block_classifier", "enforced")

    res_ws_a = await harness.classify_kanban_block(
        workspace_id="ws-a",
        task_id="task-1",
        error_summary="Erro de rede",
    )
    assert res_ws_a.from_cache is False
    assert len(provider.calls) == 1

    # Mesma chamada no mesmo workspace usa cache e reaplica policy
    res_ws_a_cached = await harness.classify_kanban_block(
        workspace_id="ws-a",
        task_id="task-1",
        error_summary="Erro de rede",
    )
    assert res_ws_a_cached.from_cache is True
    assert len(provider.calls) == 1

    # Chamada idêntica em outro workspace NÃO compartilha cache
    res_ws_b = await harness.classify_kanban_block(
        workspace_id="ws-b",
        task_id="task-1",
        error_summary="Erro de rede",
    )
    assert res_ws_b.from_cache is False
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_workspace_budget_isolation_and_unknown_cost_accounting() -> None:
    """Verifica o comportamento de workspace budget isolation and unknown cost accounting."""
    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    provider.usage_tokens = 0  # Simula provedor que não reporta tokens
    budget = WorkspaceBudgetTracker(default_limit_tokens=200)
    budget.set_workspace_limit("ws-limited", 160)
    harness = SystemOneHarness(settings, provider, budget_tracker=budget)
    harness.set_mode("kanban_block_classifier", "enforced")

    # Primeira chamada no ws-limited consome a estimativa mínima (não zero)
    res1 = await harness.classify_kanban_block(
        workspace_id="ws-limited",
        task_id="task-1",
        error_summary="Erro 1",
    )
    assert res1.policy_result.outcome == "accepted"
    assert budget.get_consumed("ws-limited") > 0

    # Segunda chamada com estado diferente excede o orçamento de ws-limited
    res2 = await harness.classify_kanban_block(
        workspace_id="ws-limited",
        task_id="task-2",
        error_summary="Erro 2",
    )
    assert res2.policy_result.outcome == "requires_hitl"
    assert "workspace_budget_exhausted" in res2.reason_codes

    # Outro workspace continua operando normalmente
    res_other = await harness.classify_kanban_block(
        workspace_id="ws-other",
        task_id="task-2",
        error_summary="Erro 2",
    )
    assert res_other.policy_result.outcome == "accepted"


@pytest.mark.asyncio
async def test_circuit_breaker_and_retry_after_within_deadline() -> None:
    """Verifica o comportamento de circuit breaker and retry after within deadline."""
    now = [100.0]

    def fake_clock() -> float:
        """Executa a operação fake clock."""
        return now[0]

    async def fake_sleep(seconds: float) -> None:
        """Executa a operação fake sleep."""
        now[0] += seconds

    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    provider.error_to_raise = SystemOneConnectionError(
        "systemone_connection_error", provider="clef-local"
    )

    harness = SystemOneHarness(
        settings,
        provider,
        clock=fake_clock,
        sleep_fn=fake_sleep,
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown_s=30.0,
    )
    harness.set_mode("kanban_block_classifier", "enforced")

    # Falha com retry (2 tentativas = atinge threshold=2 do circuit breaker)
    res1 = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="task-1",
        error_summary="Falha 1",
    )
    assert res1.policy_result.outcome == "requires_hitl"
    assert len(provider.calls) == 2

    # Próxima chamada encontra circuit breaker aberto sem chamar o provider
    res2 = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="task-2",
        error_summary="Falha 2",
    )
    assert "circuit_breaker_open" in res2.reason_codes
    assert len(provider.calls) == 2

    # Após avançar o relógio além do cooldown, o circuit breaker fecha e aceita chamadas
    now[0] += 35.0
    provider.error_to_raise = None
    res3 = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="task-2",
        error_summary="Falha 2",
    )
    assert res3.policy_result.outcome == "accepted"


@pytest.mark.asyncio
async def test_retry_after_exceeding_deadline_fails_fast() -> None:
    """Verifica o comportamento de retry after exceeding deadline fails fast."""
    now = [10.0]
    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    provider.error_to_raise = SystemOneRateLimitError(
        "rate_limited", provider="clef-local", retry_after=60.0
    )
    harness = SystemOneHarness(
        settings,
        provider,
        clock=lambda: now[0],
    )
    res = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="task-1",
        error_summary="Rate limit",
    )
    assert res.policy_result.outcome == "requires_hitl"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_remote_privacy_and_fallback_model_reconstruction() -> None:
    """Verifica o comportamento de remote privacy and fallback model reconstruction."""
    settings = SystemOneSettings(
        enabled=True,
        local_model="clef-flash",
        clef_api_model="@cf/cloudflare/clef-flash",
        allow_remote_fallback=True,
    )
    primary = FakeDecisionProvider("clef-local")
    primary.error_to_raise = SystemOneConnectionError(
        "systemone_connection_error", provider="clef-local"
    )
    remote_fallback = FakeDecisionProvider("clef-api")

    harness = SystemOneHarness(
        settings,
        primary,
        fallback_provider=remote_fallback,
    )
    harness.set_mode("kanban_block_classifier", "enforced")

    # Sem autorização remota no pattern, o fallback remoto NUNCA é chamado
    res_blocked = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="task-1",
        error_summary="Segredo local",
    )
    assert res_blocked.policy_result.outcome == "requires_hitl"
    assert len(remote_fallback.calls) == 0

    # Com override explícito de autorização remota, reconstrói o modelo para o destino remoto
    res_allowed = await harness.decide_pattern(
        "kanban_block_classifier",
        {"task_id": "task-1", "error_summary": "Autorizado remotamente"},
        workspace_id="ws-1",
        allow_remote_override=True,
    )
    assert res_allowed.used_fallback is True
    assert res_allowed.policy_result.outcome == "accepted"
    assert len(remote_fallback.calls) == 1
    assert remote_fallback.calls[0].model == "@cf/cloudflare/clef-flash"


@pytest.mark.asyncio
async def test_remote_override_does_not_enable_disabled_fallback() -> None:
    """A autorização de privacidade não substitui a opção global de fallback."""
    settings = SystemOneSettings(
        enabled=True,
        local_model="clef-flash",
        clef_api_model="@cf/cloudflare/clef-flash",
        allow_remote_fallback=False,
    )
    primary = FakeDecisionProvider("clef-local")
    primary.error_to_raise = SystemOneConnectionError(
        "systemone_connection_error", provider="clef-local"
    )
    remote_fallback = FakeDecisionProvider("clef-api")
    harness = SystemOneHarness(settings, primary, fallback_provider=remote_fallback)
    harness.set_mode("kanban_block_classifier", "enforced")

    result = await harness.decide_pattern(
        "kanban_block_classifier",
        {"task_id": "task-1", "error_summary": "explicit privacy override"},
        workspace_id="ws-1",
        allow_remote_override=True,
    )

    assert result.used_fallback is False
    assert result.policy_result.outcome == "requires_hitl"
    assert primary.calls
    assert all(call.model == "clef-flash" for call in primary.calls)
    assert remote_fallback.calls == []


@pytest.mark.asyncio
async def test_queue_full_and_cancellation_release_budget() -> None:
    """Verifica o comportamento de queue full and cancellation release budget."""
    settings = SystemOneSettings(enabled=True)
    slow_provider = FakeDecisionProvider("clef-local", delay_s=0.3)
    budget = WorkspaceBudgetTracker(default_limit_tokens=10_000)
    harness = SystemOneHarness(
        settings,
        slow_provider,
        budget_tracker=budget,
        max_provider_concurrency=1,
        interactive_reserved_slots=0,
        max_queue_size=1,
    )

    task1 = asyncio.create_task(
        harness.classify_kanban_block(
            workspace_id="ws-1",
            task_id="t1",
            error_summary="s1",
        )
    )
    await asyncio.sleep(0.02)

    # Fila cheia (max_queue_size=1 já ocupado por task1)
    res_full = await harness.classify_kanban_block(
        workspace_id="ws-1",
        task_id="t2",
        error_summary="s2",
    )
    assert "harness_queue_full" in res_full.reason_codes

    # Cancelamento de task1 propaga CancelledError e libera a reserva de orçamento
    task1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task1
    assert budget.get_reserved("ws-1") == 0


@pytest.mark.asyncio
async def test_goal_mode_never_marks_completed_when_quality_gate_fails() -> None:
    """Verifica o comportamento de goal mode never marks completed when quality gate fails."""
    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    provider.custom_answers = {
        "goal_completed": NoulAnswer(probability=0.99, confidence=0.99),
        "evidence_sufficient": NoulAnswer(probability=0.99, confidence=0.99),
    }
    harness = SystemOneHarness(settings, provider)
    harness.set_mode("goal_completion_noul", "enforced", workspace_id="ws-1")

    failed_gates = [
        GateResult(
            command=["pytest"],
            passed=False,
            exit_code=1,
            output="1 failed",
        )
    ]
    disposition, completed, reason = await evaluate_goal_with_systemone(
        harness,
        workspace_id="ws-1",
        goal="Passar todos os testes",
        turn_summary="Todos os testes passaram perfeitamente!",
        gate_results=failed_gates,
    )
    assert disposition == "not_completed"
    assert completed is False
    assert reason == "quality_gate_failed"

    passed_gates = [
        GateResult(
            command=["pytest"],
            passed=True,
            exit_code=0,
            output="10 passed",
        )
    ]
    disposition_ok, completed_ok, _ = await evaluate_goal_with_systemone(
        harness,
        workspace_id="ws-1",
        goal="Passar todos os testes",
        turn_summary="10 passed",
        gate_results=passed_gates,
    )
    assert disposition_ok == "completed"
    assert completed_ok is True


@pytest.mark.asyncio
async def test_kanban_proposal_respects_card_version_and_manual_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica o comportamento de kanban proposal respects card version and manual transitions."""
    card_state: dict[str, Any] = {
        "id": "card-1",
        "status": "blocked",
        "block_kind": "needs_input",
        "block_reason": "Falta token",
        "block_count": 1,
        "claim_lock": "run-1",
        "claim_expires_at": "2099-01-01T00:00:00+00:00",
        "updated_at": "2026-10-19T12:00:00Z",
        "session_id": "sess-1",
        "card_version": "blocked|2026-10-19T12:00:00Z|run-1|1",
    }

    async def fake_snapshot(task_id: str) -> dict[str, Any]:
        """Executa a operação fake snapshot."""
        assert task_id == "card-1"
        return dict(card_state)

    async def fake_deps(task_id: str) -> list[dict[str, Any]]:
        """Executa a operação fake deps."""
        return []

    applied: list[tuple[str, str]] = []

    async def fake_manual_transition(
        task_id: str,
        target_status: str,
        *,
        authorized_session_id: str | None = None,
        authorized_run_id: str | None = None,
    ) -> None:
        """Executa a operação fake manual transition."""
        applied.append((task_id, target_status))

    monkeypatch.setattr(kanban, "get_card_version_snapshot", fake_snapshot)
    monkeypatch.setattr(kanban, "get_dependencies", fake_deps)
    monkeypatch.setattr(kanban, "manual_transition", fake_manual_transition)

    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    harness = SystemOneHarness(settings, provider)
    harness.set_mode("kanban_transition_choice", "enforced", workspace_id="ws-1")

    proposal = await kanban.propose_transition_with_systemone(
        "card-1",
        harness=harness,
        workspace_id="ws-1",
        evidence_summary="Token fornecido",
        authorized_session_id="sess-1",
    )
    assert proposal["recommended_status"] == "ready"
    assert proposal["is_usable"] is True

    # Se card_version mudar antes de aplicar, a proposta é rejeitada (proteção TOCTOU)
    card_state["card_version"] = "blocked|2026-10-19T12:01:00Z|run-1|1"
    with pytest.raises(ValueError, match="card_version mudou"):
        await kanban.apply_verified_transition_proposal(
            proposal,
            authorized_session_id="sess-1",
            authorized_run_id="run-1",
        )
    assert applied == []

    # Restaurando a versão mas mudando o claim_lock, também rejeita
    card_state["card_version"] = proposal["card_version"]
    card_state["claim_lock"] = "other-run"
    with pytest.raises(ValueError, match="perdeu o claim"):
        await kanban.apply_verified_transition_proposal(
            proposal,
            authorized_session_id="sess-1",
            authorized_run_id="run-1",
        )


@pytest.mark.asyncio
async def test_rag_systemone_reranker_deterministic_order_and_fallback_on_failure() -> (
    None
):
    """Verifica o comportamento de rag systemone reranker deterministic order and fallback on failure."""
    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    harness = SystemOneHarness(settings, provider)
    harness.set_mode("rag_rerank_scoring", "enforced", workspace_id="ws-1")

    scores_by_id = {"doc-a": 0.30, "doc-b": 0.90, "doc-c": 0.90}

    async def custom_decide(request: SystemOneRequest) -> SystemOneResponse:
        """Executa a operação custom decide."""
        state_obj = json.loads(request.state)
        cid = state_obj["candidate_id"]
        sc = scores_by_id[cid]
        return SystemOneResponse(
            answers={
                "relevance": ScoreAnswer(
                    score=sc, min_score=0.0, max_score=1.0, confidence=0.95
                )
            },
            metadata=SystemOneMetadata(
                provider="clef-local", model=request.model, latency_ms=1.0
            ),
        )

    provider.custom_decide_fn = custom_decide

    docs = [
        Document(page_content="Texto A", metadata={"id": "doc-a"}),
        Document(page_content="Texto B", metadata={"id": "doc-b"}),
        Document(page_content="Texto C", metadata={"id": "doc-c"}),
    ]
    reranker = SystemOneRerank(harness, top_n=3, workspace_id="ws-1")
    ranked = await reranker.acompress_documents(docs, "consulta")
    # doc-b e doc-c empatam em 0.90; desempate determinístico preserva a ordem original (doc-b antes de doc-c)
    assert [d.metadata["id"] for d in ranked] == ["doc-b", "doc-c", "doc-a"]

    # Em caso de falha ou modo off, preserva o ranking original [doc-a, doc-b, doc-c]
    harness.set_mode("rag_rerank_scoring", "off", workspace_id="ws-1")
    fallback_docs = await reranker.acompress_documents(docs, "consulta")
    assert [d.metadata["id"] for d in fallback_docs] == ["doc-a", "doc-b", "doc-c"]
    assert reranker.last_status == "fallback"


@pytest.mark.asyncio
async def test_hitl_risk_assessment_only_elevates_and_never_reduces_approval() -> None:
    """Verifica o comportamento de hitl risk assessment only elevates and never reduces approval."""
    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    provider.custom_answers = {
        "risk_score": ScoreAnswer(
            score=0.80, min_score=0.0, max_score=1.0, confidence=0.95
        )
    }
    harness = SystemOneHarness(settings, provider)
    harness.set_mode("tool_risk_assessment_score", "enforced", workspace_id="ws-1")

    ctx = ToolContext(
        thread_id="t-1",
        workspace_id="ws-1",
        permission_mode="auto",
    )
    ctx._extra["decision_model_preferences"] = {
        "enabled": True,
        "provider": "clef-local",
        "surfaces": {"hitl": True},
    }
    # file_read em modo 'auto' não exigiria aprovação pelas regras estáticas
    assert should_require_approval("file_read", ctx, {"path": "a.txt"}, []) is False

    # Após pré-cálculo assíncrono detectar alto risco, should_require_approval passa a exigir revisão
    elevated = await precompute_tool_risk_assessment(
        harness,
        tool_name="file_read",
        ctx=ctx,
        args={"path": "a.txt"},
    )
    assert elevated is True
    assert should_require_approval("file_read", ctx, {"path": "a.txt"}, []) is True

    # Uma tool destrutiva (ex: computer_use) que já exige aprovação em modo 'ask' NUNCA perde a exigência mesmo com score 0.0
    provider.custom_answers = {
        "risk_score": ScoreAnswer(
            score=0.0, min_score=0.0, max_score=1.0, confidence=0.99
        )
    }
    ctx_ask = ToolContext(
        thread_id="t-2",
        workspace_id="ws-1",
        permission_mode="ask",
    )
    await precompute_tool_risk_assessment(
        harness,
        tool_name="computer_use",
        ctx=ctx_ask,
        args={"action": "click"},
    )
    assert (
        should_require_approval("computer_use", ctx_ask, {"action": "click"}, [])
        is True
    )

    # Revisão durável para decisões sem tool call
    rec = record_non_tool_review_request(
        workspace_id="ws-1",
        consumer="goal_mode",
        pattern_id="goal_completion_noul",
        decision_id="dec-123",
        state_version="v-1",
        reason="systemone_uncertain_probability",
    )
    assert rec["status"] == "pending_review"
    assert any(
        r["decision_id"] == "dec-123" for r in list_non_tool_review_requests("ws-1")
    )


@pytest.mark.asyncio
async def test_revoked_decision_model_requires_human_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SystemOneHarness(
        SystemOneSettings(enabled=True), FakeDecisionProvider("clef-local")
    )

    async def unexpected_readiness() -> bool:
        pytest.fail("revoked provider must not be queried")

    monkeypatch.setattr(harness, "is_ready", unexpected_readiness)
    ctx = ToolContext(
        thread_id="t-revoked",
        workspace_id="ws-1",
        permission_mode="auto",
        _extra={
            "decision_model_preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"hitl": True},
            },
            "decision_model_revoked": True,
        },
    )
    args = {"path": "secret.txt"}
    assert await precompute_tool_risk_assessment(
        harness, tool_name="file_read", ctx=ctx, args=args
    )
    from backend.engine.hitl import _risk_assessment_key

    assert ctx._extra["systemone_decisions"][
        _risk_assessment_key("file_read", args)
    ] == {
        "decision": "requires_human",
        "reason": "decision_model_revoked",
        "run_id": "",
        "thread_id": "t-revoked",
    }


@pytest.mark.asyncio
async def test_high_confidence_risk_is_a_veto_not_a_human_approval() -> None:
    """A strong deny must be persisted as deny and never converted to HITL approval."""
    provider = FakeDecisionProvider("clef-local")
    provider.custom_answers = {
        "risk_score": ScoreAnswer(
            score=0.99, min_score=0.0, max_score=1.0, confidence=0.99
        )
    }
    harness = SystemOneHarness(SystemOneSettings(enabled=True), provider)
    ctx = ToolContext(
        thread_id="t-veto",
        workspace_id="ws-1",
        permission_mode="auto",
        _extra={
            "decision_model_preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"hitl": True},
            }
        },
    )

    elevated = await precompute_tool_risk_assessment(
        harness,
        tool_name="file_read",
        ctx=ctx,
        args={"path": "secret.txt"},
    )

    from backend.engine.hitl import _risk_assessment_key

    decision = ctx._extra["systemone_decisions"][
        _risk_assessment_key("file_read", {"path": "secret.txt"})
    ]
    assert elevated is False
    assert decision["decision"] == "deny"
    assert (
        should_require_approval("file_read", ctx, {"path": "secret.txt"}, []) is False
    )


@pytest.mark.asyncio
async def test_disabled_hitl_surface_does_not_call_decision_provider() -> None:
    """A disabled surface is authoritative even when the model is enabled."""

    class MustNotRunHarness:
        async def assess_tool_risk(self, **_kwargs):
            raise AssertionError("disabled HITL surface called the provider")

    ctx = ToolContext(
        thread_id="t-disabled",
        workspace_id="ws-1",
        permission_mode="auto",
        _extra={
            "decision_model_preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"hitl": False},
            }
        },
    )
    result = await precompute_tool_risk_assessment(
        cast("SystemOneHarness", MustNotRunHarness()),
        tool_name="file_read",
        ctx=ctx,
        args={"path": "safe.txt"},
    )
    assert result is False
    decision = next(iter(ctx._extra["systemone_decisions"].values()))
    assert decision["decision"] == "unavailable"
    assert decision["reason"] == "decision_model_surface_disabled"


@pytest.mark.asyncio
async def test_missing_risk_score_requires_human_instead_of_implicit_allow() -> None:
    provider = FakeDecisionProvider("clef-local")
    provider.custom_answers = {}
    harness = SystemOneHarness(SystemOneSettings(enabled=True), provider)
    ctx = ToolContext(
        thread_id="t-invalid-risk",
        workspace_id="ws-1",
        permission_mode="auto",
        _extra={
            "decision_model_preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"hitl": True},
            }
        },
    )

    elevated = await precompute_tool_risk_assessment(
        harness,
        tool_name="file_read",
        ctx=ctx,
        args={"path": "private.txt"},
    )

    from backend.engine.hitl import _risk_assessment_key

    decision = ctx._extra["systemone_decisions"][
        _risk_assessment_key("file_read", {"path": "private.txt"})
    ]
    assert elevated is True
    assert decision["decision"] == "requires_human"
    assert decision["reason"] == "systemone_empty_answers"


@pytest.mark.asyncio
async def test_subagent_surface_uses_its_own_preference_flag() -> None:
    provider = FakeDecisionProvider("clef-local")
    provider.custom_answers = {
        "risk_score": ScoreAnswer(
            score=0.80, min_score=0.0, max_score=1.0, confidence=0.95
        )
    }
    harness = SystemOneHarness(SystemOneSettings(enabled=True), provider)
    ctx = ToolContext(
        thread_id="child-thread",
        workspace_id="ws-1",
        permission_mode="auto",
        _extra={
            "subagent_execution": True,
            "decision_model_preferences": {
                "enabled": True,
                "surfaces": {"hitl": True, "subagents": True},
            },
        },
    )

    elevated = await precompute_tool_risk_assessment(
        harness,
        tool_name="file_read",
        ctx=ctx,
        args={"path": "input.txt"},
        surface="subagents",
    )

    assert elevated is True
    assert "systemone_decisions" in ctx._extra


@pytest.mark.asyncio
async def test_plan_mode_uses_its_own_flag_without_bypassing_approval() -> None:
    """A Plan Mode decision may add a gate but cannot remove the static one."""
    provider = FakeDecisionProvider("clef-local")
    provider.custom_answers = {
        "risk_score": ScoreAnswer(
            score=0.80, min_score=0.0, max_score=1.0, confidence=0.95
        )
    }
    harness = SystemOneHarness(SystemOneSettings(enabled=True), provider)
    ctx = ToolContext(
        thread_id="plan-thread",
        workspace_id="ws-1",
        permission_mode="plan",
        _extra={
            "decision_model_preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"plan_mode": True, "browser": False},
            }
        },
    )

    from backend.llm.systemone.preferences import decision_model_surfaces_for_tool

    plan_surface = decision_model_surfaces_for_tool(ctx, "file_write")
    assert plan_surface == ("plan_mode",)
    assert decision_model_surfaces_for_tool(ctx, "browser_navigate") == (
        "plan_mode",
        "browser",
    )
    assert decision_model_enabled_for(ctx, ("plan_mode", "browser")) is False

    elevated = await precompute_tool_risk_assessment(
        harness,
        tool_name="file_write",
        ctx=ctx,
        args={"path": "plan.txt"},
        surface=plan_surface,
    )
    assert elevated is True
    assert should_require_approval("file_write", ctx, {"path": "plan.txt"}, []) is True

    ctx._extra["decision_model_preferences"]["surfaces"]["plan_mode"] = False
    assert (
        await precompute_tool_risk_assessment(
            harness,
            tool_name="file_write",
            ctx=ctx,
            args={"path": "plan.txt"},
            surface=plan_surface,
        )
        is False
    )
    decision = next(iter(ctx._extra["systemone_decisions"].values()))
    assert decision["decision"] == "unavailable"
    assert decision["reason"] == "decision_model_surface_disabled"


@pytest.mark.parametrize(
    ("tool_name", "background_task_id", "surfaces", "enabled"),
    [
        ("browser_navigate", "", {"subagents": True, "browser": False}, False),
        ("browser_navigate", "", {"subagents": False, "browser": True}, False),
        ("browser_navigate", "", {"subagents": True, "browser": True}, True),
        ("call_mcp_tool", "", {"subagents": True, "mcp": False}, False),
        (
            "write_terminal",
            "job-1",
            {"subagents": True, "background": True, "terminal": False},
            False,
        ),
    ],
)
def test_decision_model_requires_provenance_and_capability_flags(
    tool_name: str,
    background_task_id: str,
    surfaces: dict[str, bool],
    enabled: bool,
) -> None:
    ctx = ToolContext(
        thread_id="child-thread",
        workspace_id="ws-1",
        background_task_id=background_task_id,
        _extra={
            "subagent_execution": True,
            "decision_model_preferences": {"enabled": True, "surfaces": surfaces},
        },
    )
    applicable = decision_model_surfaces_for_tool(ctx, tool_name)
    assert applicable[0] == "subagents"
    assert decision_model_enabled_for(ctx, applicable) is enabled


def test_connector_decisions_require_the_connector_surface_flag() -> None:
    ctx = ToolContext(
        thread_id="external-thread",
        permission_mode="auto",
        _extra={
            "connector_execution": True,
            "decision_model_preferences": {
                "enabled": True,
                "surfaces": {"hitl": True, "connectors": False},
            },
        },
    )
    surface = decision_model_surfaces_for_tool(ctx, "file_write")
    assert surface == ("connectors", "hitl")
    assert decision_model_enabled_for(ctx, surface) is False

    ctx._extra["decision_model_preferences"]["surfaces"]["connectors"] = True
    assert decision_model_enabled_for(ctx, surface) is True


@pytest.mark.asyncio
async def test_systemone_tools_enforce_registered_patterns_and_authenticated_context() -> (
    None
):
    """Verifica o comportamento de systemone tools enforce registered patterns and authenticated context."""
    unauth_ctx = ToolContext(thread_id="", workspace_id="")
    raw_unauth = await systemone_list_patterns(unauth_ctx)
    assert json.loads(raw_unauth)["status"] == "error"

    settings = SystemOneSettings(enabled=True)
    provider = FakeDecisionProvider("clef-local")
    harness = SystemOneHarness(settings, provider)
    harness.set_mode("kanban_block_classifier", "advisory", workspace_id="ws-auth")

    auth_ctx = ToolContext(
        thread_id="sess-auth",
        workspace_id="ws-auth",
        _extra={"systemone_harness": harness},
    )
    raw_list = await systemone_list_patterns(auth_ctx)
    parsed_list = json.loads(raw_list)
    assert parsed_list["status"] == "ok"
    assert len(parsed_list["patterns"]) == 12

    # Pattern não registrado deve ser recusado
    raw_unknown = await systemone_evaluate_pattern(
        auth_ctx,
        pattern_id="unregistered_custom_pattern",
        state={"foo": "bar"},
    )
    assert json.loads(raw_unknown)["status"] == "error"

    # Pattern registrado avalia e retorna execution_authorized=False
    raw_eval = await systemone_evaluate_pattern(
        auth_ctx,
        pattern_id="kanban_block_classifier",
        state={"task_id": "t-1", "error_summary": "Falta input"},
    )
    parsed_eval = json.loads(raw_eval)
    assert parsed_eval["status"] == "ok"
    assert parsed_eval["execution_authorized"] is False
    assert parsed_eval["is_usable_recommendation"] is True


@pytest.mark.asyncio
async def test_kanban_decision_tool_respects_surface_preference(monkeypatch):
    from backend.tools import systemone_tools

    ctx = ToolContext(
        thread_id="sess-auth",
        workspace_id="ws-auth",
        _extra={
            "systemone_harness": object(),
            "decision_model_preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"kanban": False},
            },
        },
    )
    unused = False

    def fail_if_built(provider_override=None):
        nonlocal unused
        unused = True
        raise AssertionError("disabled Kanban surface must not construct a provider")

    monkeypatch.setattr(systemone_tools, "_build_default_harness", fail_if_built)
    result = json.loads(await systemone_propose_kanban_transition(ctx, "task-1"))

    assert result == {
        "status": "disabled",
        "error": "Decision Model não está habilitado para decisões do Kanban",
    }
    assert unused is False
