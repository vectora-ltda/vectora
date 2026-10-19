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
from backend.llm.systemone.harness import (
    HarnessDecisionResult,
    SystemOneHarness,
    WorkspaceBudgetTracker,
)
from backend.llm.systemone.patterns import (
    DEFAULT_PATTERN_REGISTRY,
    DecisionPatternRegistry,
    DecisionPatternSpec,
    PatternEvaluationSpec,
    PatternInputSpec,
    PatternOperationMode,
    PatternOperationSpec,
    PatternPolicySpec,
    PatternPrivacySpec,
    build_default_patterns,
    serialize_canonical_state,
)
from backend.llm.systemone.policy import DecisionPolicyEngine, PolicyEvaluationResult
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.settings import (
    SystemOneSettings,
    get_systemone_provider,
    mask_secret,
)
from backend.llm.systemone.types import (
    CapabilityEvidenceLevel,
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
    parse_systemone_response,
)

__all__ = [
    "DEFAULT_PATTERN_REGISTRY",
    "CapabilityEvidenceLevel",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "ClefApiAdapter",
    "ClefLocalAdapter",
    "DecisionPatternRegistry",
    "DecisionPatternSpec",
    "DecisionPolicyEngine",
    "DecisionProviderType",
    "HarnessDecisionResult",
    "JevApiAdapter",
    "NoulAnswer",
    "NoulQuestion",
    "PatternEvaluationSpec",
    "PatternInputSpec",
    "PatternOperationMode",
    "PatternOperationSpec",
    "PatternPolicySpec",
    "PatternPrivacySpec",
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
    "SystemOneHarness",
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
    "WorkspaceBudgetTracker",
    "build_default_patterns",
    "get_systemone_provider",
    "mask_secret",
    "parse_systemone_response",
    "serialize_canonical_state",
]
