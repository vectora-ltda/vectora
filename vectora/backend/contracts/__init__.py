"""Contratos canônicos e versão estática da API Vectora."""

from __future__ import annotations

from backend.contracts.models import (
    ArtifactRef,
    CheckpointRef,
    ContextProjection,
    HarnessRunStatus,
    MemoryRecord,
    ProviderCapability,
    QualityGatePolicy,
    QualityGateRunResult,
    QualityGateSpec,
    UserSettingsV1,
    VerificationOutcome,
    WorkspaceVerificationProfile,
)

CONTRACT_VERSION = "1"

__all__ = [
    "CONTRACT_VERSION",
    "ArtifactRef",
    "CheckpointRef",
    "ContextProjection",
    "HarnessRunStatus",
    "MemoryRecord",
    "ProviderCapability",
    "QualityGatePolicy",
    "QualityGateRunResult",
    "QualityGateSpec",
    "UserSettingsV1",
    "VerificationOutcome",
    "WorkspaceVerificationProfile",
]
