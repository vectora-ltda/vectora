"""Local lifecycle API for installed VEXT extensions."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from backend.services.vext import MAX_PACKAGE_BYTES
from backend.services.vext_artifact import verify_vext
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
    """List installed versions with manifest metadata for the Library.

    The local store is authoritative for artifacts installed during development
    and offline use. Returning the manifest lets the frontend render those
    extensions even when the remote registry has not published them yet.
    """
    records = _store().list_installed()
    extensions: list[dict[str, object]] = []
    for item in records:
        try:
            manifest = verify_vext(item.artifact).manifest.model_dump(mode="json")
        except (OSError, ValueError):
            # A corrupt artifact must not make the whole Library unavailable.
            continue
        extensions.append(
            {
                "id": item.extension_id,
                "version": item.version,
                "active": item.active,
                "manifest": manifest,
            }
        )
    return {"extensions": extensions}


@router.get("/{extension_id}/frontend", response_class=Response)
async def frontend(extension_id: str) -> Response:
    """Serve a small same-origin host page for an installed frontend contribution.

    Frontend bundles are kept inside the verified ``.vext`` artifact.  The host
    page executes only the active artifact's declared entrypoint in a sandboxed
    iframe, so installing an extension never writes executable files into the
    application bundle.
    """
    item = _store().active(extension_id)
    if item is None:
        raise HTTPException(status_code=404, detail="extensão não instalada")
    try:
        result = verify_vext(item.artifact)
        if (
            result.manifest.id != extension_id
            or not result.manifest.frontend_entrypoint
        ):
            raise ValueError("extensão não possui frontend")
        entrypoint = result.manifest.frontend_entrypoint
        with zipfile.ZipFile(item.artifact) as archive:
            code = archive.read(entrypoint).decode("utf-8")
    except (
        OSError,
        KeyError,
        UnicodeDecodeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The artifact was authenticated at installation time and the entrypoint is
    # declared by its verified manifest.  Escape the closing tag before placing
    # it in HTML to prevent a payload from terminating the module element.
    code = code.replace("</script", "<\\/script")
    html = (
        "<!doctype html><meta charset='utf-8'>"
        "<main id='vext-root'></main><script type='module'>"
        f"{code}\nconst root=document.getElementById('vext-root');"
        "if(typeof mount==='function') await mount(root);"
        "</script>"
    )
    return Response(content=html, media_type="text/html")


@router.get("/{extension_id}/icon", response_class=Response)
async def icon(extension_id: str) -> Response:
    """Serve an extension's declared SVG icon from its verified artifact."""
    item = _store().active(extension_id)
    if item is None:
        raise HTTPException(status_code=404, detail="extensão não instalada")
    try:
        result = verify_vext(item.artifact)
        icon_path = result.manifest.icon
        if not icon_path:
            raise ValueError("extensão não possui ícone")
        with zipfile.ZipFile(item.artifact) as archive:
            data = archive.read(icon_path)
    except (
        OSError,
        KeyError,
        UnicodeDecodeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
