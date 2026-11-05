"""Armazenamento e resolução de perfis de verificação (WorkspaceVerificationProfile)."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import tempfile
from pathlib import Path

from backend.contracts.models import (
    QualityGateSpec,
    WorkspaceVerificationProfile,
)
from backend.settings import settings

logger = logging.getLogger(__name__)


def _profiles_dir() -> Path:
    """Executa a operação profiles dir."""
    return settings.vectora_home / "verification"


def _profile_path(workspace_id: str) -> Path:
    """Executa a operação profile path."""
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
            logger.exception(
                "Falha ao carregar perfil de verificação corrompido para %s: %s",
                ws_id,
                exc,
            )
            return WorkspaceVerificationProfile(
                workspace_id=ws_id,
                enabled=True,
                gates=[
                    QualityGateSpec(
                        id="corrupted_profile_guard",
                        name="Corrupted Profile Guard",
                        command=[
                            sys.executable,
                            "-c",
                            "import sys; sys.stderr.write('Perfil de verificacao corrompido\\n'); sys.exit(1)",
                        ],
                        policy="block",
                        timeout_seconds=5,
                    )
                ],
                require_clean_git=False,
                inject_stack_trace_on_failure=True,
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
    """Persiste o perfil de verificação do workspace atomicamente usando substituição segura."""
    path = _profile_path(profile.workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            content = json.dumps(profile.model_dump(), indent=2, ensure_ascii=False)
            temp_file.write(content)
            temp_file.flush()
        temp_path.replace(path)
    except Exception:
        if temp_path and temp_path.exists():
            with contextlib.suppress(Exception):
                temp_path.unlink()
        raise
