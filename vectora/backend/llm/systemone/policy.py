"""Policy de execução, guardrails e resolução segura de decisões (HITL).

Princípios de segurança para Decision Models:
- Decision Models apenas recomendam; o código de policy decide aceitar, solicitar revisão (HITL) ou rejeitar.
- Um modelo JAMAIS concede permissões, altera policy nem executa ferramentas diretamente.
- Baixa confiança, ausência de confiança, empates, falhas ou decisões de alto impacto acionam HITL.
- Fallback remoto é proibido sem consentimento configurado explicitamente.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from backend.llm.systemone.errors import SystemOneError
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.settings import SystemOneSettings
from backend.llm.systemone.types import (
    ChoiceAnswer,
    NoulAnswer,
    SystemOneAnswer,
    SystemOneRequest,
    SystemOneResponse,
)

logger = logging.getLogger(__name__)

PolicyDecisionOutcome = Literal["accepted", "requires_hitl", "rejected"]


class PolicyEvaluationResult(BaseModel):
    """Resultado da aplicação da policy de segurança sobre a resposta do Decision Model."""

    outcome: PolicyDecisionOutcome
    requires_hitl: bool = False
    reason: str
    answers: dict[str, SystemOneAnswer] = Field(default_factory=dict)
    response: SystemOneResponse | None = None


class DecisionPolicyEngine:
    """Motor de governança e policy para avaliar recomendações dos Decision Models."""

    def __init__(self, settings: SystemOneSettings) -> None:
        self._settings = settings

    def _resolve_target_model(
        self,
        provider_id: str,
        explicit_model: str | None = None,
    ) -> str | None:
        if explicit_model:
            return explicit_model
        if provider_id == "clef-local":
            return self._settings.local_model
        if provider_id == "clef-api":
            return self._settings.clef_api_model
        if provider_id == "jev-api":
            return self._settings.jev_api_model
        return None

    def evaluate_response(  # noqa: PLR0911 - each policy violation has a distinct terminal reason
        self,
        response: SystemOneResponse,
        *,
        is_high_impact: bool = False,
        confidence_threshold: float | None = None,
        choice_ambiguity_margin: float = 0.05,
        noul_uncertainty_band: tuple[float, float] = (0.45, 0.55),
    ) -> PolicyEvaluationResult:
        """Avalia se as respostas de um Decision Model atendem aos critérios de segurança."""
        threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else self._settings.confidence_threshold_hitl
        )

        # Decisões de alto impacto sempre exigem HITL
        if is_high_impact:
            return PolicyEvaluationResult(
                outcome="requires_hitl",
                requires_hitl=True,
                reason="Operação classificada como de alto impacto exige confirmação humana obrigatória.",
                answers=response.answers,
                response=response,
            )

        if not response.answers:
            return PolicyEvaluationResult(
                outcome="requires_hitl",
                requires_hitl=True,
                reason="systemone_empty_answers",
                response=response,
            )

        low_band, high_band = noul_uncertainty_band

        for q_id, ans in response.answers.items():
            if ans.confidence is None:
                return PolicyEvaluationResult(
                    outcome="requires_hitl",
                    requires_hitl=True,
                    reason="systemone_missing_confidence",
                    answers=response.answers,
                    response=response,
                )

            if ans.confidence < threshold:
                return PolicyEvaluationResult(
                    outcome="requires_hitl",
                    requires_hitl=True,
                    reason=(
                        f"Confiança reportada para a pergunta '{q_id}' ({ans.confidence:.2f}) "
                        f"ficou abaixo do limiar de segurança ({threshold:.2f})."
                    ),
                    answers=response.answers,
                    response=response,
                )

            if isinstance(ans, ChoiceAnswer) and ans.distribution:
                probs = sorted(ans.distribution.values(), reverse=True)
                if (
                    len(probs) >= 2
                    and abs(probs[0] - probs[1]) < choice_ambiguity_margin
                ):
                    return PolicyEvaluationResult(
                        outcome="requires_hitl",
                        requires_hitl=True,
                        reason=(
                            f"Empate ou ambiguidade detectada na pergunta Choice '{q_id}': "
                            f"as duas opções principais diferem por menos de "
                            f"{choice_ambiguity_margin * 100:.0f}% de probabilidade."
                        ),
                        answers=response.answers,
                        response=response,
                    )

            if isinstance(ans, NoulAnswer):
                if low_band <= ans.probability <= high_band:
                    return PolicyEvaluationResult(
                        outcome="requires_hitl",
                        requires_hitl=True,
                        reason=(
                            f"Incerteza máxima detectada na pergunta Noul '{q_id}': "
                            f"probabilidade estimada ({ans.probability:.2f}) próxima a 0.50."
                        ),
                        answers=response.answers,
                        response=response,
                    )

        return PolicyEvaluationResult(
            outcome="accepted",
            requires_hitl=False,
            reason="Respostas do Decision Model atendem a todos os critérios de confiança e política.",
            answers=response.answers,
            response=response,
        )

    async def execute_decision(
        self,
        provider: SystemOneProvider,
        request: SystemOneRequest,
        *,
        fallback_provider: SystemOneProvider | None = None,
        fallback_model: str | None = None,
        is_high_impact: bool = False,
        confidence_threshold: float | None = None,
        choice_ambiguity_margin: float = 0.05,
        noul_uncertainty_band: tuple[float, float] = (0.45, 0.55),
    ) -> PolicyEvaluationResult:
        """Executa a decisão com governança de fallback, isolamento de exceções e policy evaluation."""
        if not self._settings.enabled:
            return PolicyEvaluationResult(
                outcome="rejected",
                requires_hitl=False,
                reason="Decision Models estão desabilitados nas configurações do Vectora.",
            )

        resp: SystemOneResponse | None = None
        try:
            resp = await provider.decide(request)
        except SystemOneError:
            logger.warning(
                "systemone_primary_failed",
                extra={"provider": provider.provider_id},
            )
            if self._settings.allow_remote_fallback and fallback_provider is not None:
                logger.info(
                    "Tentando fallback explícito para provedor remoto %s",
                    fallback_provider.provider_id,
                )
                target_model = self._resolve_target_model(
                    fallback_provider.provider_id,
                    explicit_model=fallback_model,
                )
                fb_request = (
                    request.model_copy(update={"model": target_model})
                    if target_model is not None and target_model != request.model
                    else request
                )
                try:
                    resp = await fallback_provider.decide(fb_request)
                except Exception:
                    logger.warning(
                        "systemone_fallback_failed",
                        extra={"provider": fallback_provider.provider_id},
                    )
                    return PolicyEvaluationResult(
                        outcome="requires_hitl",
                        requires_hitl=True,
                        reason="systemone_primary_and_fallback_failed",
                    )
            else:
                return PolicyEvaluationResult(
                    outcome="requires_hitl",
                    requires_hitl=True,
                    reason="Falha no provedor de decisão. Requer intervenção humana.",
                )

        if resp is None:
            return PolicyEvaluationResult(
                outcome="requires_hitl",
                requires_hitl=True,
                reason="Nenhuma resposta pôde ser obtida dos provedores de decisão.",
            )

        return self.evaluate_response(
            resp,
            is_high_impact=is_high_impact,
            confidence_threshold=confidence_threshold,
            choice_ambiguity_margin=choice_ambiguity_margin,
            noul_uncertainty_band=noul_uncertainty_band,
        )
