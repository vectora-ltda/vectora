"""Pacote de integração e suporte a Decision Models (JEV e Clef) com SystemOne."""

from __future__ import annotations

from backend.llm.systemone.api_adapters import ClefApiAdapter, JevApiAdapter
from backend.llm.systemone.clef_local import ClefLocalAdapter
from backend.llm.systemone.errors import (
    SystemOneAuthenticationError,
    SystemOneCapabilityError,
    SystemOneConnectionError,
    SystemOneError,
    SystemOneRateLimitError,
    SystemOneTimeoutError,
    SystemOneValidationError,
)
from backend.llm.systemone.policy import DecisionPolicyEngine, PolicyEvaluationResult
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.settings import (
    SystemOneSettings,
    get_systemone_provider,
    mask_secret,
)
from backend.llm.systemone.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionProviderType,
    NoulAnswer,
    NoulQuestion,
    ProviderCapabilities,
    QuestionType,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneAnswer,
    SystemOneMetadata,
    SystemOneQuestion,
    SystemOneRequest,
    SystemOneResponse,
    SystemOneUsage,
)

__all__ = [
    "ChoiceAnswer",
    "ChoiceQuestion",
    "ClefApiAdapter",
    "ClefLocalAdapter",
    "DecisionPolicyEngine",
    "DecisionProviderType",
    "JevApiAdapter",
    "NoulAnswer",
    "NoulQuestion",
    "PolicyEvaluationResult",
    "ProviderCapabilities",
    "QuestionType",
    "ScoreAnswer",
    "ScoreQuestion",
    "SystemOneAnswer",
    "SystemOneAuthenticationError",
    "SystemOneCapabilityError",
    "SystemOneConnectionError",
    "SystemOneError",
    "SystemOneMetadata",
    "SystemOneProvider",
    "SystemOneQuestion",
    "SystemOneRateLimitError",
    "SystemOneRequest",
    "SystemOneResponse",
    "SystemOneSettings",
    "SystemOneTimeoutError",
    "SystemOneUsage",
    "SystemOneValidationError",
    "get_systemone_provider",
    "mask_secret",
]
