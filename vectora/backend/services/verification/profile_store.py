"""Armazenamento e resolução de perfis de verificação (WorkspaceVerificationProfile)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.contracts.models import (
    QualityGateSpec,
    WorkspaceVerificationProfile,
)
from backend.settings import settings

logger = logging.getLogger(__name__)


def _profiles_dir() -> Path:
    return settings.vectora_home / "verification"


def _profile_path(workspace_id: str) -> Path:
    safe_id = (workspace_id or "default").replace("/", "_").replace("\\", "_")
    return _profiles_dir() / f"{safe_id}.json"


def get_verification_profile(
    workspace_id: str | None,
) -> WorkspaceVerificationProfile:
    """Carrega o perfil de verificação configurado para o workspace.

    Se não houver perfil customizado salvo, retorna o perfil padrão ativo.
    """
    ws_id = workspace_id or "default"
    path = _profile_path(ws_id)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return WorkspaceVerificationProfile.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "Falha ao carregar perfil de verificação para %s: %s", ws_id, exc
            )

    # Perfil padrão inicial: ativo, sem gates pré-definidos (passa livre a menos que gates sejam adicionados)
    return WorkspaceVerificationProfile(
        workspace_id=ws_id,
        enabled=True,
        gates=[],
        require_clean_git=False,
        inject_stack_trace_on_failure=True,
    )


def save_verification_profile(profile: WorkspaceVerificationProfile) -> None:
    """Persiste o perfil de verificação do workspace."""
    path = _profile_path(profile.workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
