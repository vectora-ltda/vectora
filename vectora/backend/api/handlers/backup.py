"""Endpoints internos para o wizard local de reinstalação.

Somente o processo Electron, por meio do token efêmero entregue no spawn do
backend, pode pedir inspeção ou importação. O renderer nunca envia caminhos de
destino e a confirmação é obrigatória para publicar dados.
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.settings import settings
from backend.storage.backup_manifest import (
    BackupPreview,
    inspect_backup,
    restore_backup,
)

router = APIRouter(prefix="/storage/backup", tags=["backup"])


class InspectBackupRequest(BaseModel):
    archive_path: str = Field(min_length=1)


class RestoreBackupRequest(BaseModel):
    archive_path: str = Field(min_length=1)
    categories: list[str] | None = None
    confirmed: bool = False


def _require_desktop_bridge(token: str | None) -> None:
    expected = os.getenv("VECTORA_DESKTOP_BRIDGE_TOKEN", "")
    if not expected or token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="ponte local não autorizada")


def _preview_payload(preview: BackupPreview) -> dict[str, object]:
    return {
        "version": preview.version,
        "app_version": preview.app_version,
        "size_bytes": preview.size_bytes,
        "categories": preview.categories,
        "compatible": preview.compatible,
        "storage_mode": preview.storage_mode,
    }


@router.post("/inspect")
async def inspect_local_backup(
    payload: InspectBackupRequest,
    x_vectora_desktop_bridge: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Inspeciona arquivo escolhido pelo diálogo nativo sem escrever estado."""
    _require_desktop_bridge(x_vectora_desktop_bridge)
    return _preview_payload(inspect_backup(payload.archive_path))


@router.post("/restore")
async def restore_local_backup(
    payload: RestoreBackupRequest,
    x_vectora_desktop_bridge: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Importa categorias confirmadas usando o banco configurado no backend."""
    _require_desktop_bridge(x_vectora_desktop_bridge)
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="confirmação explícita necessária")
    db_path = settings.db_file or settings.vectora_home / "data" / "backend.db"
    selected = None if payload.categories is None else set(payload.categories)
    preview = restore_backup(payload.archive_path, db_path, selected)
    return _preview_payload(preview)
