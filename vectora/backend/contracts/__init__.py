"""Contratos canônicos e versão estática da API Vectora."""

from __future__ import annotations

from backend.contracts.models import (
    QualityGatePolicy,
    QualityGateRunResult,
    QualityGateSpec,
    UserSettingsV1,
    WorkspaceVerificationProfile,
)

CONTRACT_VERSION = "1"

__all__ = [
    "CONTRACT_VERSION",
    "QualityGatePolicy",
    "QualityGateRunResult",
    "QualityGateSpec",
    "UserSettingsV1",
    "WorkspaceVerificationProfile",
]
