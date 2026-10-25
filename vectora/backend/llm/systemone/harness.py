"""Harness global governado para execução de Decision Patterns no SystemOne.

Garantias arquiteturais:
- Desacopla consumidores dos provedores específicos (Clef Local, Clef API, JEV API).
- Separa execução de inferência da avaliação de policy e da autorização do executor.
- `accepted` significa exclusivamente 'recomendação utilizável', nunca 'ação autorizada'.
- Aplica deadlines totais, limites de fila, reserva de capacidade para decisões interativas,
  circuit breaker por destino, cache isolado por workspace com reaplicação de policy
  e controle de orçamento por workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.llm.systemone.errors import (
    SystemOneCapabilityError,
    SystemOneError,
    SystemOneRateLimitError,
    SystemOneTimeoutError,
    SystemOneValidationError,
)
from backend.llm.systemone.patterns import (
    DEFAULT_PATTERN_REGISTRY,
    DecisionPatternRegistry,
    DecisionPatternSpec,
    PatternOperationMode,
    serialize_canonical_state,
)
from backend.llm.systemone.policy import DecisionPolicyEngine, PolicyEvaluationResult
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.settings import SystemOneSettings
from backend.llm.systemone.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    ScoreAnswer,
    SystemOneQuestion,
    SystemOneRequest,
    SystemOneResponse,
    SystemOneUsage,
)

logger = logging.getLogger(__name__)

_REMOTE_PROVIDERS = frozenset({"clef-api", "jev-api"})
_MIN_ACCOUNTED_TOKENS = 16


class HarnessDecisionResult(BaseModel):
    """Resultado governado retornado por `SystemOneHarness.decide_pattern()`."""

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workspace_id: str
    pattern_id: str
    pattern_version: str
    policy_version: str
    calibration_version: str
    mode: PatternOperationMode
    provider: str
    model: str
    runtime_identity: str | None = None
    state_version: str
    response: SystemOneResponse | None = None
    policy_result: PolicyEvaluationResult
    reason_codes: list[str] = Field(default_factory=list)
    is_usable_recommendation: bool = False
    execution_authorized: Literal[False] = False
    deadline_ms: float
    elapsed_ms: float
    queue_wait_ms: float = 0.0
    attempts: int = 0
    used_fallback: bool = False
    from_cache: bool = False
    usage: SystemOneUsage = Field(default_factory=SystemOneUsage)


@dataclass
class _CircuitBreakerState:
    consecutive_failures: int = 0
    opened_until: float = 0.0


class WorkspaceBudgetTracker:
    """Controle de orçamento de tokens por workspace com reserva prévia, janela de renovação e reconciliação."""

    def __init__(
        self,
        default_limit_tokens: int = 100_000,
        *,
        window_seconds: float | None = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._default_limit = default_limit_tokens
        self._window_seconds = window_seconds
        self._clock = clock
        self._limits: dict[str, int] = {}
        self._consumed: dict[str, int] = {}
        self._reserved: dict[str, int] = {}
        self._window_started_at: dict[str, float] = {}

    def _refresh_window_if_expired(self, workspace_id: str) -> None:
        if self._window_seconds is None or self._window_seconds <= 0:
            return
        now = self._clock()
        started = self._window_started_at.get(workspace_id)
        if started is None:
            self._window_started_at[workspace_id] = now
            return
        if now - started >= self._window_seconds:
            self._consumed[workspace_id] = 0
            self._window_started_at[workspace_id] = now

    def reset(self, workspace_id: str | None = None) -> None:
        """Zera o consumo acumulado de um workspace ou de todos os workspaces."""
        if workspace_id is None:
            self._consumed.clear()
            self._window_started_at.clear()
            return
        self._consumed.pop(workspace_id, None)
        self._window_started_at.pop(workspace_id, None)

    def set_workspace_limit(self, workspace_id: str, limit_tokens: int) -> None:
        self._limits[workspace_id] = max(0, limit_tokens)

    def get_consumed(self, workspace_id: str) -> int:
        self._refresh_window_if_expired(workspace_id)
        return self._consumed.get(workspace_id, 0)

    def get_reserved(self, workspace_id: str) -> int:
        return self._reserved.get(workspace_id, 0)

    def reserve(self, workspace_id: str, estimated_tokens: int) -> bool:
        self._refresh_window_if_expired(workspace_id)
        limit = self._limits.get(workspace_id, self._default_limit)
        used = self._consumed.get(workspace_id, 0)
        reserved = self._reserved.get(workspace_id, 0)
        estimate = max(_MIN_ACCOUNTED_TOKENS, estimated_tokens)
        if used + reserved + estimate > limit:
            return False
        self._reserved[workspace_id] = reserved + estimate
        return True

    def commit(
        self,
        workspace_id: str,
        reserved_tokens: int,
        actual_tokens: int | None,
    ) -> int:
        self._refresh_window_if_expired(workspace_id)
        estimate = max(_MIN_ACCOUNTED_TOKENS, reserved_tokens)
        current_reserved = self._reserved.get(workspace_id, 0)
        self._reserved[workspace_id] = max(0, current_reserved - estimate)
        # Não trata custo desconhecido/zero de inferência realizada como gratuito
        accounted = (
            actual_tokens
            if actual_tokens is not None and actual_tokens > 0
            else estimate
        )
        self._consumed[workspace_id] = self._consumed.get(workspace_id, 0) + accounted
        return accounted

    def release(self, workspace_id: str, reserved_tokens: int) -> None:
        estimate = max(_MIN_ACCOUNTED_TOKENS, reserved_tokens)
        current_reserved = self._reserved.get(workspace_id, 0)
        self._reserved[workspace_id] = max(0, current_reserved - estimate)


class SystemOneHarness:
    """Harness central para avaliação estruturada com Decision Models."""

    def __init__(
        self,
        settings: SystemOneSettings,
        primary_provider: SystemOneProvider,
        *,
        fallback_provider: SystemOneProvider | None = None,
        registry: DecisionPatternRegistry | None = None,
        policy_engine: DecisionPolicyEngine | None = None,
        budget_tracker: WorkspaceBudgetTracker | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Any] = asyncio.sleep,
        max_provider_concurrency: int = 6,
        interactive_reserved_slots: int = 2,
        max_workspace_concurrency: int = 4,
        max_queue_size: int = 16,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown_s: float = 30.0,
    ) -> None:
        self._settings = settings
        self._primary_provider = primary_provider
        self._fallback_provider = fallback_provider
        self._registry = registry or DEFAULT_PATTERN_REGISTRY
        self._policy_engine = policy_engine or DecisionPolicyEngine(settings)
        self._budget = budget_tracker or WorkspaceBudgetTracker()
        self._clock = clock
        self._sleep_fn = sleep_fn

        self._max_provider_concurrency = max(1, max_provider_concurrency)
        self._interactive_reserved_slots = max(
            0, min(interactive_reserved_slots, self._max_provider_concurrency - 1)
        )
        self._non_interactive_limit = max(
            1, self._max_provider_concurrency - self._interactive_reserved_slots
        )
        self._max_workspace_concurrency = max(1, max_workspace_concurrency)
        self._max_queue_size = max(1, max_queue_size)

        self._provider_sem = asyncio.Semaphore(self._max_provider_concurrency)
        self._non_interactive_sem = asyncio.Semaphore(self._non_interactive_limit)
        self._workspace_sems: dict[str, asyncio.Semaphore] = {}
        self._queued_count = 0

        self._cb_threshold = max(1, circuit_breaker_threshold)
        self._cb_cooldown_s = max(0.1, circuit_breaker_cooldown_s)
        self._circuit_breakers: dict[str, _CircuitBreakerState] = {}

        self._mode_overrides: dict[tuple[str | None, str], PatternOperationMode] = {}
        self._response_cache: OrderedDict[
            tuple[Any, ...], tuple[float, SystemOneResponse]
        ] = OrderedDict()
        self._response_cache_max = 512
        self._response_cache_ttl_s = 300.0
        self._inflight: dict[tuple[Any, ...], asyncio.Future[SystemOneResponse]] = {}
        self.last_rerank_status = "not_called"

    @property
    def registry(self) -> DecisionPatternRegistry:
        return self._registry

    @property
    def budget_tracker(self) -> WorkspaceBudgetTracker:
        return self._budget

    def set_mode(
        self,
        pattern_id: str,
        mode: PatternOperationMode,
        *,
        workspace_id: str | None = None,
    ) -> None:
        """Configura o modo operacional ('off', 'shadow', 'advisory', 'enforced') para um pattern."""
        self._registry.get(pattern_id)
        self._mode_overrides[(workspace_id, pattern_id)] = mode

    def get_mode(
        self,
        pattern_id: str,
        *,
        workspace_id: str | None = None,
    ) -> PatternOperationMode:
        if (
            workspace_id is not None
            and (workspace_id, pattern_id) in self._mode_overrides
        ):
            return self._mode_overrides[(workspace_id, pattern_id)]
        if (None, pattern_id) in self._mode_overrides:
            return self._mode_overrides[(None, pattern_id)]
        spec = self._registry.get(pattern_id)
        return spec.operation_spec.default_mode

    def _resolve_model_for_provider(self, provider_id: str) -> str:
        if provider_id == "clef-local":
            return self._settings.local_model
        if provider_id == "laya-local":
            return self._settings.laya_model
        if provider_id == "clef-api":
            return self._settings.clef_api_model
        if provider_id == "jev-api":
            return self._settings.jev_api_model
        return self._settings.local_model

    def _is_circuit_open(self, provider_id: str) -> bool:
        state = self._circuit_breakers.get(provider_id)
        if state is None:
            return False
        return state.opened_until > self._clock()

    def _record_provider_success(self, provider_id: str) -> None:
        state = self._circuit_breakers.get(provider_id)
        if state is not None:
            state.consecutive_failures = 0
            state.opened_until = 0.0

    def _record_provider_failure(self, provider_id: str) -> None:
        state = self._circuit_breakers.setdefault(provider_id, _CircuitBreakerState())
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._cb_threshold:
            state.opened_until = self._clock() + self._cb_cooldown_s

    def _is_provider_privacy_allowed(
        self,
        spec: DecisionPatternSpec,
        provider_id: str,
        *,
        allow_remote_override: bool = False,
    ) -> bool:
        if provider_id not in _REMOTE_PROVIDERS:
            return True
        if not (spec.privacy_spec.allow_remote_providers or allow_remote_override):
            return False
        return not (
            spec.privacy_spec.allowed_remote_providers
            and provider_id not in spec.privacy_spec.allowed_remote_providers
        )

    async def _check_provider_capabilities(
        self,
        provider: SystemOneProvider,
        questions: dict[str, SystemOneQuestion],
        target_model: str,
    ) -> None:
        caps = await provider.capabilities()
        if caps.evidence == "unverified":
            raise SystemOneCapabilityError(
                f"Provedor '{provider.provider_id}' possui compatibilidade não verificada.",
                provider=provider.provider_id,
            )
        for q in questions.values():
            if q.type == "noul" and not caps.supports_noul:
                raise SystemOneCapabilityError(
                    "Provedor não suporta perguntas Noul",
                    provider=provider.provider_id,
                )
            if q.type == "choice" and not caps.supports_choice:
                raise SystemOneCapabilityError(
                    "Provedor não suporta perguntas Choice",
                    provider=provider.provider_id,
                )
            if q.type == "score" and not caps.supports_score:
                raise SystemOneCapabilityError(
                    "Provedor não suporta perguntas Score",
                    provider=provider.provider_id,
                )
        if caps.supported_models and target_model not in caps.supported_models:
            raise SystemOneCapabilityError(
                f"Modelo '{target_model}' não suportado por '{provider.provider_id}'",
                provider=provider.provider_id,
            )

    def _build_rejected_or_hitl_result(
        self,
        *,
        spec: DecisionPatternSpec,
        workspace_id: str,
        mode: PatternOperationMode,
        state_version: str,
        outcome: Literal["rejected", "requires_hitl"],
        reason_code: str,
        correlation_id: str,
        deadline_ms: float,
        elapsed_ms: float,
        queue_wait_ms: float = 0.0,
        attempts: int = 0,
        used_fallback: bool = False,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> HarnessDecisionResult:
        prov = provider_id or self._primary_provider.provider_id
        mdl = model or self._resolve_model_for_provider(prov)
        return HarnessDecisionResult(
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            pattern_id=spec.pattern_id,
            pattern_version=spec.version,
            policy_version=spec.policy_spec.policy_version,
            calibration_version=spec.evaluation_spec.calibration_version,
            mode=mode,
            provider=prov,
            model=mdl,
            state_version=state_version,
            response=None,
            policy_result=PolicyEvaluationResult(
                outcome=outcome,
                requires_hitl=(outcome == "requires_hitl"),
                reason=reason_code,
            ),
            reason_codes=[reason_code],
            is_usable_recommendation=False,
            deadline_ms=deadline_ms,
            elapsed_ms=elapsed_ms,
            queue_wait_ms=queue_wait_ms,
            attempts=attempts,
            used_fallback=used_fallback,
        )

    async def _invoke_provider_with_retries(
        self,
        provider: SystemOneProvider,
        request: SystemOneRequest,
        *,
        max_retries: int,
        deadline_at: float,
    ) -> tuple[SystemOneResponse, int]:
        attempts = 0
        while True:
            attempts += 1
            try:
                resp = await provider.decide(request)
                self._record_provider_success(provider.provider_id)
                return resp, attempts
            except asyncio.CancelledError:
                raise
            except SystemOneError as exc:
                if exc.is_retryable:
                    self._record_provider_failure(provider.provider_id)
                if not exc.is_retryable or attempts > max_retries:
                    raise
                wait_s = 0.05 * attempts
                if (
                    isinstance(exc, SystemOneRateLimitError)
                    and exc.retry_after is not None
                ):
                    wait_s = exc.retry_after
                remaining_s = deadline_at - self._clock()
                if wait_s >= remaining_s or remaining_s <= 0:
                    raise SystemOneTimeoutError(
                        "systemone_retry_exceeds_deadline",
                        provider=provider.provider_id,
                    ) from exc
                await self._sleep_fn(wait_s)

    def _preflight_validate(
        self,
        *,
        spec: DecisionPatternSpec,
        workspace_id: str,
        mode: PatternOperationMode,
        resolved_state_version: str,
        corr_id: str,
        deadline_ms: float,
        start_clock: float,
        primary_id: str,
        primary_model: str,
        allow_remote_override: bool,
    ) -> HarnessDecisionResult | None:
        if not self._settings.enabled or mode == "off":
            reason_code = (
                "systemone_disabled"
                if not self._settings.enabled
                else "pattern_mode_off"
            )
            return self._build_rejected_or_hitl_result(
                spec=spec,
                workspace_id=workspace_id,
                mode=mode,
                state_version=resolved_state_version,
                outcome="rejected",
                reason_code=reason_code,
                correlation_id=corr_id,
                deadline_ms=deadline_ms,
                elapsed_ms=(self._clock() - start_clock) * 1000.0,
            )
        if not self._is_provider_privacy_allowed(
            spec, primary_id, allow_remote_override=allow_remote_override
        ):
            return self._build_rejected_or_hitl_result(
                spec=spec,
                workspace_id=workspace_id,
                mode=mode,
                state_version=resolved_state_version,
                outcome="requires_hitl",
                reason_code="remote_provider_not_authorized_for_pattern",
                correlation_id=corr_id,
                deadline_ms=deadline_ms,
                elapsed_ms=(self._clock() - start_clock) * 1000.0,
                provider_id=primary_id,
                model=primary_model,
            )
        return None

    async def _run_primary_or_fallback(
        self,
        *,
        spec: DecisionPatternSpec,
        req: SystemOneRequest,
        questions: dict[str, SystemOneQuestion],
        primary_id: str,
        primary_model: str,
        deadline_at: float,
        allow_remote_override: bool,
    ) -> tuple[SystemOneResponse | None, int, bool, str, str, str]:
        """Executa no provedor primário ou no fallback autorizado, retornando `(resp, attempts, used_fallback, reason_code, prov_id, model)`."""
        attempts = 0
        primary_failed_error: SystemOneError | None = None

        if self._is_circuit_open(primary_id):
            primary_failed_error = SystemOneError(
                "circuit_breaker_open",
                provider=primary_id,
                is_retryable=False,
            )
        else:
            try:
                await self._check_provider_capabilities(
                    self._primary_provider, questions, primary_model
                )
                resp, attempts = await self._invoke_provider_with_retries(
                    self._primary_provider,
                    req,
                    max_retries=spec.operation_spec.max_retries,
                    deadline_at=deadline_at,
                )
                return resp, attempts, False, "", primary_id, primary_model
            except asyncio.CancelledError:
                raise
            except SystemOneError as exc:
                primary_failed_error = exc

        can_fallback = (
            self._fallback_provider is not None
            and self._settings.allow_remote_fallback
            and (spec.operation_spec.allow_fallback or allow_remote_override)
        )
        if not can_fallback or self._fallback_provider is None:
            reason_code = (
                "circuit_breaker_open"
                if primary_failed_error is not None
                and primary_failed_error.message == "circuit_breaker_open"
                else "systemone_primary_failed"
            )
            return None, attempts, False, reason_code, primary_id, primary_model

        fb_id = self._fallback_provider.provider_id
        fb_model = self._resolve_model_for_provider(fb_id)
        if not self._is_provider_privacy_allowed(
            spec, fb_id, allow_remote_override=allow_remote_override
        ):
            return (
                None,
                attempts,
                False,
                "fallback_remote_not_authorized_for_pattern",
                fb_id,
                fb_model,
            )
        if self._is_circuit_open(fb_id):
            return None, attempts, True, "circuit_breaker_open", fb_id, fb_model

        fb_req = req.model_copy(update={"model": fb_model})
        try:
            await self._check_provider_capabilities(
                self._fallback_provider, questions, fb_model
            )
            resp, fb_attempts = await self._invoke_provider_with_retries(
                self._fallback_provider,
                fb_req,
                max_retries=0,
                deadline_at=deadline_at,
            )
            return resp, attempts + fb_attempts, True, "", fb_id, fb_model
        except asyncio.CancelledError:
            raise
        except SystemOneError:
            return (
                None,
                attempts,
                True,
                "systemone_primary_and_fallback_failed",
                fb_id,
                fb_model,
            )

    def _build_governed_result(
        self,
        *,
        spec: DecisionPatternSpec,
        workspace_id: str,
        mode: PatternOperationMode,
        resolved_state_version: str,
        corr_id: str,
        deadline_ms: float,
        start_clock: float,
        resp: SystemOneResponse,
        queue_wait_ms: float = 0.0,
        attempts: int = 0,
        used_fallback: bool = False,
        from_cache: bool = False,
    ) -> HarnessDecisionResult:
        policy_eval = self._policy_engine.evaluate_response(
            resp,
            is_high_impact=spec.policy_spec.is_high_impact,
            confidence_threshold=spec.policy_spec.confidence_threshold,
            choice_ambiguity_margin=spec.policy_spec.choice_ambiguity_margin,
            noul_uncertainty_band=spec.policy_spec.noul_uncertainty_band,
        )
        usable = policy_eval.outcome == "accepted" and mode in ("advisory", "enforced")
        reasons = [policy_eval.reason]
        if mode == "shadow":
            reasons.append("shadow_mode_observation_only")

        return HarnessDecisionResult(
            correlation_id=corr_id,
            workspace_id=workspace_id,
            pattern_id=spec.pattern_id,
            pattern_version=spec.version,
            policy_version=spec.policy_spec.policy_version,
            calibration_version=spec.evaluation_spec.calibration_version,
            mode=mode,
            provider=resp.metadata.provider,
            model=resp.metadata.model,
            state_version=resolved_state_version,
            response=resp,
            policy_result=policy_eval,
            reason_codes=reasons,
            is_usable_recommendation=usable,
            deadline_ms=deadline_ms,
            elapsed_ms=(self._clock() - start_clock) * 1000.0,
            queue_wait_ms=queue_wait_ms,
            attempts=attempts,
            used_fallback=used_fallback,
            from_cache=from_cache,
            usage=resp.metadata.usage,
        )

    async def decide_pattern(
        self,
        pattern_id: str,
        state: dict[str, Any] | str,
        *,
        workspace_id: str,
        pattern_version: str | None = None,
        state_version: str | None = None,
        correlation_id: str | None = None,
        question_overrides: dict[str, SystemOneQuestion] | None = None,
        allow_remote_override: bool = False,
    ) -> HarnessDecisionResult:
        """Executa um pattern registrado com governança completa de privacidade, orçamento e policy."""
        corr_id = correlation_id or str(uuid.uuid4())
        start_clock = self._clock()
        spec = self._registry.get(pattern_id, version=pattern_version)
        mode = self.get_mode(pattern_id, workspace_id=workspace_id)
        deadline_ms = spec.operation_spec.deadline_ms

        canonical_state, computed_hash = serialize_canonical_state(state, spec)
        resolved_state_version = state_version or (
            str(
                state.get("card_version")
                or state.get("observation_version")
                or computed_hash
            )
            if isinstance(state, dict)
            else computed_hash
        )

        questions = dict(spec.questions)
        if question_overrides:
            if set(question_overrides.keys()) != set(questions.keys()):
                raise SystemOneValidationError(
                    "question_overrides deve conter exatamente os IDs de perguntas do pattern."
                )
            for q_id, q_override in question_overrides.items():
                if q_override.type != questions[q_id].type:
                    raise SystemOneValidationError(
                        f"Tipo da pergunta '{q_id}' não pode ser alterado por override."
                    )
            questions = dict(question_overrides)

        primary_id = self._primary_provider.provider_id
        primary_model = self._resolve_model_for_provider(primary_id)

        preflight_err = self._preflight_validate(
            spec=spec,
            workspace_id=workspace_id,
            mode=mode,
            resolved_state_version=resolved_state_version,
            corr_id=corr_id,
            deadline_ms=deadline_ms,
            start_clock=start_clock,
            primary_id=primary_id,
            primary_model=primary_model,
            allow_remote_override=allow_remote_override,
        )
        if preflight_err is not None:
            return preflight_err

        questions_fp = hashlib.sha256(
            json.dumps(
                {k: v.model_dump() for k, v in questions.items()},
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        cache_key = (
            workspace_id,
            computed_hash,
            resolved_state_version,
            spec.pattern_id,
            spec.version,
            primary_id,
            primary_model,
            spec.operation_spec.temperature,
            spec.operation_spec.max_tokens,
            questions_fp,
        )

        cached_entry = self._response_cache.get(cache_key)
        cached_resp = None
        if cached_entry is not None:
            cached_at, cached_resp = cached_entry
            if self._clock() - cached_at > self._response_cache_ttl_s:
                self._response_cache.pop(cache_key, None)
                cached_resp = None
            else:
                self._response_cache.move_to_end(cache_key)
        if cached_resp is not None:
            return self._build_governed_result(
                spec=spec,
                workspace_id=workspace_id,
                mode=mode,
                resolved_state_version=resolved_state_version,
                corr_id=corr_id,
                deadline_ms=deadline_ms,
                start_clock=start_clock,
                resp=cached_resp,
                from_cache=True,
            )

        estimated_tokens = max(
            _MIN_ACCOUNTED_TOKENS,
            spec.operation_spec.max_tokens + max(1, len(canonical_state) // 4),
        )
        admission_error: str | None = None
        if not self._budget.reserve(workspace_id, estimated_tokens):
            admission_error = "workspace_budget_exhausted"
        elif self._queued_count >= self._max_queue_size:
            self._budget.release(workspace_id, estimated_tokens)
            admission_error = "harness_queue_full"

        if admission_error is not None:
            return self._build_rejected_or_hitl_result(
                spec=spec,
                workspace_id=workspace_id,
                mode=mode,
                state_version=resolved_state_version,
                outcome="requires_hitl",
                reason_code=admission_error,
                correlation_id=corr_id,
                deadline_ms=deadline_ms,
                elapsed_ms=(self._clock() - start_clock) * 1000.0,
                provider_id=primary_id,
                model=primary_model,
            )

        ws_sem = self._workspace_sems.setdefault(
            workspace_id, asyncio.Semaphore(self._max_workspace_concurrency)
        )
        deadline_s = deadline_ms / 1000.0
        deadline_at = start_clock + deadline_s
        queue_wait_ms = 0.0
        attempts = 0
        used_fallback = False

        self._queued_count += 1
        try:
            async with asyncio.timeout(deadline_s):
                if not spec.operation_spec.interactive_priority:
                    await self._non_interactive_sem.acquire()
                try:
                    async with self._provider_sem, ws_sem:
                        queue_wait_ms = (self._clock() - start_clock) * 1000.0
                        req = SystemOneRequest(
                            model=primary_model,
                            state=canonical_state,
                            questions=questions,
                            max_tokens=spec.operation_spec.max_tokens,
                            temperature=spec.operation_spec.temperature,
                        )
                        (
                            resp,
                            attempts,
                            used_fallback,
                            err_code,
                            target_prov,
                            target_mdl,
                        ) = await self._run_primary_or_fallback(
                            spec=spec,
                            req=req,
                            questions=questions,
                            primary_id=primary_id,
                            primary_model=primary_model,
                            deadline_at=deadline_at,
                            allow_remote_override=allow_remote_override,
                        )
                        if resp is None:
                            self._budget.release(workspace_id, estimated_tokens)
                            return self._build_rejected_or_hitl_result(
                                spec=spec,
                                workspace_id=workspace_id,
                                mode=mode,
                                state_version=resolved_state_version,
                                outcome="requires_hitl",
                                reason_code=err_code,
                                correlation_id=corr_id,
                                deadline_ms=deadline_ms,
                                elapsed_ms=(self._clock() - start_clock) * 1000.0,
                                queue_wait_ms=queue_wait_ms,
                                attempts=attempts,
                                used_fallback=used_fallback,
                                provider_id=target_prov,
                                model=target_mdl,
                            )
                finally:
                    if not spec.operation_spec.interactive_priority:
                        self._non_interactive_sem.release()
        except asyncio.CancelledError:
            self._budget.release(workspace_id, estimated_tokens)
            raise
        except TimeoutError:
            self._budget.release(workspace_id, estimated_tokens)
            return self._build_rejected_or_hitl_result(
                spec=spec,
                workspace_id=workspace_id,
                mode=mode,
                state_version=resolved_state_version,
                outcome="requires_hitl",
                reason_code="harness_deadline_exceeded",
                correlation_id=corr_id,
                deadline_ms=deadline_ms,
                elapsed_ms=(self._clock() - start_clock) * 1000.0,
                queue_wait_ms=queue_wait_ms,
                attempts=attempts,
                used_fallback=used_fallback,
                provider_id=primary_id,
                model=primary_model,
            )
        except Exception:
            self._budget.release(workspace_id, estimated_tokens)
            raise
        finally:
            self._queued_count = max(0, self._queued_count - 1)

        self._budget.commit(
            workspace_id,
            estimated_tokens,
            resp.metadata.usage.total_tokens,
        )
        if not used_fallback:
            self._response_cache[cache_key] = (self._clock(), resp)
            self._response_cache.move_to_end(cache_key)
            while len(self._response_cache) > self._response_cache_max:
                self._response_cache.popitem(last=False)

        return self._build_governed_result(
            spec=spec,
            workspace_id=workspace_id,
            mode=mode,
            resolved_state_version=resolved_state_version,
            corr_id=corr_id,
            deadline_ms=deadline_ms,
            start_clock=start_clock,
            resp=resp,
            queue_wait_ms=queue_wait_ms,
            attempts=attempts,
            used_fallback=used_fallback,
            from_cache=False,
        )

    async def classify_kanban_block(
        self,
        *,
        workspace_id: str,
        task_id: str,
        error_summary: str,
        correlation_id: str | None = None,
    ) -> HarnessDecisionResult:
        """Classifica a razão de bloqueio de uma task entre os 4 BLOCK_KINDS."""
        return await self.decide_pattern(
            "kanban_block_classifier",
            {"task_id": task_id, "error_summary": error_summary[:4000]},
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )

    async def propose_kanban_transition(
        self,
        *,
        workspace_id: str,
        task_id: str,
        current_status: str,
        allowed_targets: list[str],
        card_version: str,
        evidence_summary: str = "",
        correlation_id: str | None = None,
    ) -> HarnessDecisionResult:
        """Propõe uma transição de status restrita aos destinos manuais permitidos + no_recommendation."""
        valid_options = list(dict.fromkeys([*allowed_targets, "no_recommendation"]))
        if len(valid_options) < 2:
            valid_options = ["no_recommendation", current_status]

        override = {
            "target_status": ChoiceQuestion(
                options=valid_options,
                instructions=(
                    "Selecione apenas um destino permitido para o estado atual do card "
                    "ou 'no_recommendation' quando não houver evidência suficiente."
                ),
            )
        }
        return await self.decide_pattern(
            "kanban_transition_choice",
            {
                "task_id": task_id,
                "current_status": current_status,
                "allowed_targets": allowed_targets,
                "card_version": card_version,
                "evidence_summary": evidence_summary[:4000],
            },
            workspace_id=workspace_id,
            state_version=card_version,
            correlation_id=correlation_id,
            question_overrides=override,
        )

    async def rerank_candidates(
        self,
        *,
        workspace_id: str,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int | None = None,
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ordena candidatos já autorizados via Score, preservando o ranking original em falha ou modo shadow."""
        self.last_rerank_status = "not_called"
        if not candidates:
            return []
        limit = top_k if top_k is not None and top_k > 0 else len(candidates)
        mode = self.get_mode("rag_rerank_scoring", workspace_id=workspace_id)
        if not self._settings.enabled or mode == "off":
            self.last_rerank_status = "fallback"
            return list(candidates[:limit])

        score_sem = asyncio.Semaphore(max(1, self._max_queue_size // 2))

        async def _score_one(
            idx: int, item: dict[str, Any]
        ) -> tuple[float, int, str, dict[str, Any]] | None:
            cand_id = str(item.get("id", f"cand_{idx}"))
            snippet = str(item.get("snippet") or item.get("text") or "")[:2000]
            async with score_sem:
                res = await self.decide_pattern(
                    "rag_rerank_scoring",
                    {
                        "query": query[:1000],
                        "candidate_id": cand_id,
                        "snippet": snippet,
                    },
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            if not res.is_usable_recommendation or res.response is None:
                return None
            ans = res.response.answers.get("relevance")
            if not isinstance(ans, ScoreAnswer):
                return None
            return (ans.score, idx, cand_id, item)

        rerank_deadline_s = (
            self._registry.get("rag_rerank_scoring").operation_spec.deadline_ms / 1000.0
        )
        try:
            async with asyncio.timeout(rerank_deadline_s):
                results = await asyncio.gather(
                    *(_score_one(idx, item) for idx, item in enumerate(candidates))
                )
        except TimeoutError:
            # A single deadline covers queue admission, provider calls and semaphore
            # waits. Preserve the source order when the aggregate budget expires.
            self.last_rerank_status = "fallback"
            return list(candidates[:limit])
        scored: list[tuple[float, int, str, dict[str, Any]]] = []
        for entry in results:
            if entry is None:
                # Em shadow ou em qualquer falha/HITL, preserva integralmente o ranking original
                self.last_rerank_status = "fallback"
                return list(candidates[:limit])
            scored.append(entry)

        # Desempate determinístico: maior score primeiro, menor índice original, menor candidate_id
        scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        self.last_rerank_status = "applied"
        return [entry[3] for entry in scored[:limit]]

    async def evaluate_goal_progress(
        self,
        *,
        workspace_id: str,
        goal: str,
        turn_summary: str,
        gates_passed: bool,
        gate_results_summary: list[dict[str, Any]] | None = None,
        correlation_id: str | None = None,
    ) -> HarnessDecisionResult:
        """Avalia progresso de objetivo no Goal Mode incluindo evidências verificáveis dos gates."""
        return await self.decide_pattern(
            "goal_completion_noul",
            {
                "goal": goal[:4000],
                "turn_summary": turn_summary[:6000],
                "gates_passed": gates_passed,
                "gate_results": gate_results_summary or [],
            },
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )

    async def assess_tool_risk(
        self,
        *,
        workspace_id: str,
        tool_name: str,
        args_summary: str,
        permission_mode: str,
        correlation_id: str | None = None,
    ) -> HarnessDecisionResult:
        """Avalia score de risco para chamada de tool no fluxo assíncrono anterior ao predicado HITL."""
        return await self.decide_pattern(
            "tool_risk_assessment_score",
            {
                "tool_name": tool_name,
                "args_summary": args_summary[:4000],
                "permission_mode": permission_mode,
            },
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
