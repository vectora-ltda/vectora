"""Local lifecycle API for installed VEXT extensions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.services.vext import MAX_PACKAGE_BYTES
from backend.services.vext_install import VextInstallStore
from backend.services.vext_registry import VextTrustStore
from backend.settings import settings

router = APIRouter(prefix="/vext", tags=["vext"])


def _store() -> VextInstallStore:
    return VextInstallStore(settings.vectora_home / "data" / "vext")


def _trust_store() -> VextTrustStore:
    return VextTrustStore(settings.vectora_home / "data" / "vext" / "trust.json")


class LifecycleRequest(BaseModel):
    version: str = Field(min_length=1, max_length=64)


@router.post("/install")
async def install(artifact: Annotated[UploadFile, File(...)]) -> dict[str, object]:
    """Install and activate a signed artifact uploaded by the Library."""
    if artifact.filename is None or not artifact.filename.lower().endswith(".vext"):
        raise HTTPException(status_code=400, detail="arquivo .vext obrigatório")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="vectora-vext-", suffix=".vext", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            total = 0
            while chunk := await artifact.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES:
                    raise HTTPException(status_code=413, detail="artefato muito grande")
                temporary.write(chunk)
        item = _store().install(temporary_path, trust_store=_trust_store())
    except HTTPException:
        raise
    except (OSError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        await artifact.close()
    return {"id": item.extension_id, "version": item.version, "active": True}


@router.get("/installed")
async def list_installed() -> dict[str, object]:
    """List immutable local versions and their active state."""
    records = _store().list_installed()
    return {
        "extensions": [
            {
                "id": item.extension_id,
                "version": item.version,
                "active": item.active,
            }
            for item in records
        ]
    }


@router.post("/{extension_id}/activate")
async def activate(extension_id: str, body: LifecycleRequest) -> dict[str, object]:
    """Activate a previously installed and verified version."""
    try:
        item = _store().activate(extension_id, body.version, trust_store=_trust_store())
    except (OSError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": item.extension_id, "version": item.version, "active": True}


@router.post("/{extension_id}/deactivate")
async def deactivate(extension_id: str) -> dict[str, object]:
    """Deactivate an extension without deleting its rollback versions."""
    if not _store().deactivate(extension_id):
        raise HTTPException(status_code=404, detail="extensão não instalada")
    return {"id": extension_id, "active": False}


@router.post("/{extension_id}/rollback")
async def rollback(extension_id: str, body: LifecycleRequest) -> dict[str, object]:
    """Switch to a verified immutable local version."""
    try:
        item = _store().rollback(extension_id, body.version, trust_store=_trust_store())
    except (OSError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": item.extension_id, "version": item.version, "active": True}
