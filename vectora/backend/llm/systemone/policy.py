"""Policy de execução, guardrails e resolução segura de decisões (HITL).

Princípios de segurança para Decision Models:
- Decision Models apenas recomendam; o código de policy decide aceitar, solicitar revisão (HITL) ou rejeitar.
- Um modelo JAMAIS concede permissões, altera policy nem executa ferramentas diretamente.
- Baixa confiança, empates, falhas ou decisões de alto impacto acionam HITL.
- Fallback remoto é proibido sem consentimento configurado explicitamente.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.llm.systemone.errors import SystemOneError
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.settings import SystemOneSettings
from backend.llm.systemone.types import (
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
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

    def evaluate_response(
        self,
        response: SystemOneResponse,
        *,
        is_high_impact: bool = False,
    ) -> PolicyEvaluationResult:
        """Avalia se as respostas de um Decision Model atendem aos critérios de segurança."""
        threshold = self._settings.confidence_threshold_hitl

        # Decisões de alto impacto (ex: remoção de arquivos, push, alterações de auth) sempre exigem HITL
        if is_high_impact:
            return PolicyEvaluationResult(
                outcome="requires_hitl",
                requires_hitl=True,
                reason="Operação classificada como de alto impacto exige confirmação humana obrigatória.",
                answers=response.answers,
                response=response,
            )

        for q_id, ans in response.answers.items():
            # Checa nível de confiança se reportado
            if ans.confidence is not None and ans.confidence < threshold:
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

            # Para escolhas (Choice), verifica se houve empate ou distribuição excessivamente ambígua
            if isinstance(ans, ChoiceAnswer) and ans.distribution:
                probs = sorted(ans.distribution.values(), reverse=True)
                if len(probs) >= 2 and abs(probs[0] - probs[1]) < 0.05:
                    return PolicyEvaluationResult(
                        outcome="requires_hitl",
                        requires_hitl=True,
                        reason=(
                            f"Empate ou ambiguidade detectada na pergunta Choice '{q_id}': "
                            f"as duas opções principais diferem por menos de 5% de probabilidade."
                        ),
                        answers=response.answers,
                        response=response,
                    )

            # Para Noul (probabilidade), probabilidade próxima a 0.5 indica incerteza máxima
            if isinstance(ans, NoulAnswer):
                if 0.45 <= ans.probability <= 0.55:
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
        is_high_impact: bool = False,
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
        except SystemOneError as exc:
            logger.warning(
                "Falha ao executar decisão no provedor primário %s: %s",
                provider.provider_id,
                exc,
            )
            # Verifica se fallback remoto é permitido
            if self._settings.allow_remote_fallback and fallback_provider is not None:
                logger.info(
                    "Tentando fallback explícito para provedor remoto %s",
                    fallback_provider.provider_id,
                )
                try:
                    resp = await fallback_provider.decide(request)
                except Exception as fallback_exc:
                    logger.exception(
                        "Falha também no provedor de fallback %s: %s",
                        fallback_provider.provider_id,
                        fallback_exc,
                    )
                    return PolicyEvaluationResult(
                        outcome="requires_hitl",
                        requires_hitl=True,
                        reason="Falha na execução do provedor primário e de fallback. Requer intervenção humana.",
                    )
            else:
                return PolicyEvaluationResult(
                    outcome="requires_hitl",
                    requires_hitl=True,
                    reason="Falha no provedor de decisão primário. Requer intervenção humana.",
                )

        if resp is None:
            return PolicyEvaluationResult(
                outcome="requires_hitl",
                requires_hitl=True,
                reason="Nenhuma resposta pôde ser obtida dos provedores de decisão.",
            )

        return self.evaluate_response(resp, is_high_impact=is_high_impact)
