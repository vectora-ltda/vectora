"""Domínio e contratos canônicos para SystemOne / Decision Models (JEV e Clef).

Especificação técnica de Decision Models:
- Define os três tipos fundamentais de perguntas/respostas: Noul, Choice e Score.
- Modela requisições e respostas de forma agnóstica a provedor.
- Não converte Noul em booleano nem confunde confidence com acurácia.
- Valida números finitos, probabilidades [0.0, 1.0] e integridade de escolhas.
- Garante modelo serializável e defensivo contra injeção e dados inválidos.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

QuestionType = Literal["noul", "choice", "score"]
DecisionProviderType = Literal["clef-local", "clef-api", "jev-api"]


class BaseQuestion(BaseModel):
    """Pergunta formulada para o modelo de decisão."""

    type: QuestionType
    instructions: str | None = Field(
        default=None,
        description="Instruções específicas para a avaliação desta pergunta.",
    )


class NoulQuestion(BaseQuestion):
    """Pergunta do tipo Noul (probabilidade / verossimilhança de uma afirmação ou hipótese)."""

    type: Literal["noul"] = "noul"
    criterion: str = Field(
        ...,
        description="Critério ou hipótese a ser avaliada em termos de probabilidade.",
    )


class ChoiceQuestion(BaseQuestion):
    """Pergunta do tipo Choice (seleção discreta entre opções mutuamente exclusivas)."""

    type: Literal["choice"] = "choice"
    options: list[str] = Field(
        ...,
        min_length=2,
        description="Lista de alternativas descritas para seleção categórica.",
    )

    @model_validator(mode="after")
    def validate_options_unique(self) -> ChoiceQuestion:
        if len(self.options) != len(set(self.options)):
            raise ValueError("As opções de uma ChoiceQuestion devem ser únicas.")
        for opt in self.options:
            if not opt.strip():
                raise ValueError("Opções vazias não são permitidas em ChoiceQuestion.")
        return self


class ScoreQuestion(BaseQuestion):
    """Pergunta do tipo Score (pontuação numérica finita em uma escala contínua ou discreta)."""

    type: Literal["score"] = "score"
    min_score: float = Field(default=0.0, description="Limite inferior da escala.")
    max_score: float = Field(default=1.0, description="Limite superior da escala.")
    criterion: str = Field(
        ...,
        description="Critério ou rubrica a ser pontuado na escala definida.",
    )

    @model_validator(mode="after")
    def validate_score_bounds(self) -> ScoreQuestion:
        if not math.isfinite(self.min_score) or not math.isfinite(self.max_score):
            raise ValueError("Limites de score devem ser números finitos.")
        if self.min_score >= self.max_score:
            raise ValueError("min_score deve ser estritamente menor que max_score.")
        return self


SystemOneQuestion = Annotated[
    NoulQuestion | ChoiceQuestion | ScoreQuestion,
    Field(discriminator="type"),
]


class BaseAnswer(BaseModel):
    """Resposta base emitida pelo modelo para uma pergunta."""

    type: QuestionType
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Nível de confiança informado pelo modelo (não garante corretude).",
    )


class NoulAnswer(BaseAnswer):
    """Resposta do tipo Noul: preserva a probabilidade [0.0, 1.0] sem truncar para booleano."""

    type: Literal["noul"] = "noul"
    probability: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Probabilidade calibrada da afirmação (0.0 a 1.0).",
    )

    @model_validator(mode="after")
    def validate_finite(self) -> NoulAnswer:
        if not math.isfinite(self.probability):
            raise ValueError("A probabilidade Noul deve ser um número finito.")
        return self


class ChoiceAnswer(BaseAnswer):
    """Resposta do tipo Choice: seleção categórica e distribuição sobre as opções."""

    type: Literal["choice"] = "choice"
    selection: str = Field(
        ...,
        description="Alternativa escolhida pelo modelo de decisão.",
    )
    distribution: dict[str, float] | None = Field(
        default=None,
        description="Distribuição opcional de probabilidade sobre todas as opções.",
    )

    @model_validator(mode="after")
    def validate_distribution(self) -> ChoiceAnswer:
        if self.distribution is not None:
            for opt, prob in self.distribution.items():
                if not math.isfinite(prob) or prob < 0.0 or prob > 1.0:
                    raise ValueError(
                        f"Probabilidade da opção '{opt}' deve estar entre 0.0 e 1.0."
                    )
        return self


class ScoreAnswer(BaseAnswer):
    """Resposta do tipo Score: pontuação numérica finita."""

    type: Literal["score"] = "score"
    score: float = Field(
        ...,
        description="Pontuação atribuída pelo modelo dentro dos limites.",
    )

    @model_validator(mode="after")
    def validate_finite(self) -> ScoreAnswer:
        if not math.isfinite(self.score):
            raise ValueError("O score deve ser um número finito.")
        return self


SystemOneAnswer = Annotated[
    NoulAnswer | ChoiceAnswer | ScoreAnswer,
    Field(discriminator="type"),
]


class SystemOneRequest(BaseModel):
    """Requisição canônica enviada para um provedor de Decision Models."""

    model: str = Field(
        ..., description="Identificador do modelo (ex: clef-flash, jev-1)."
    )
    state: str = Field(
        ...,
        description="Contexto e evidências observadas para tomada de decisão (não-confiável).",
    )
    questions: dict[str, SystemOneQuestion] = Field(
        ...,
        min_length=1,
        description="Mapa de perguntas avaliadas indexadas por seu identificador único.",
    )
    media_urls: list[str] | None = Field(
        default=None,
        description="Mídias anexadas (suportadas apenas se o provedor explicitamente declarar).",
    )
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Teto máximo de tokens consumidos na inferência.",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Temperatura de amostragem (normalmente 0 para decisão determinística).",
    )


class SystemOneUsage(BaseModel):
    """Métricas de consumo de tokens da chamada de decisão."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class SystemOneMetadata(BaseModel):
    """Metadados de observabilidade da execução do Decision Model."""

    provider: str
    model: str
    pattern_version: str = "v1"
    latency_ms: float
    usage: SystemOneUsage = Field(default_factory=SystemOneUsage)


class SystemOneResponse(BaseModel):
    """Resposta canônica retornada por um provedor de Decision Models."""

    answers: dict[str, SystemOneAnswer] = Field(
        ...,
        description="Respostas calculadas indexadas pelos IDs das perguntas originais.",
    )
    metadata: SystemOneMetadata = Field(
        ...,
        description="Metadados de execução, latência e consumo sem expor segredos.",
    )
    raw_payload: dict[str, Any] | None = Field(
        default=None,
        description="Payload original compatível retornado pelo engine para auditoria.",
    )


class ProviderCapabilities(BaseModel):
    """Capacidades declaradas por um provedor de Decision Models."""

    supports_noul: bool = True
    supports_choice: bool = True
    supports_score: bool = True
    supports_image: bool = False
    supports_video: bool = False
    supported_models: list[str] = Field(default_factory=list)
