"""Memory Buckets — catálogo, instalação e publicação de buckets RAG
compartilhados.

Endpoints (montados em server.py):
    GET  /memory-buckets/catalog — lista buckets publicados (id, embed_model,
                                 verified, downloads_count, license, ...)
    POST /memory-buckets/install — baixa e instala um bucket como coleção
                                 LanceDB isolada (`shared_{bucket_id}`)
    POST /memory-buckets/publish — empacota e publica uma coleção local

Download é sempre grátis (decisão de produto) — sem gate de tier/quota.
Publicação exige um `session_token` de conta vectora.company — reaproveita
o mesmo `VECTORA_TOKEN` já usado pelo license check (`backend.services.
license._get_token`), não um fluxo de login company↔desktop separado (que
não existe hoje). Sem token configurado, o endpoint retorna erro claro em
vez de tentar publicar sem credencial.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.services.memory_buckets import (
    MemoryBucketsError,
    MemoryBucketsUnavailableError,
    download_memory_bucket,
    publish_memory_bucket,
)
from backend.services.memory_buckets import list_catalog as _list_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory-buckets", tags=["memory-buckets"])


class InstallRequest(BaseModel):
    bucket_id: str
    workspace_id: str | None = None


class PublishRequest(BaseModel):
    bucket_id: str
    name: str
    description: str
    license: str
    workspace_id: str | None = None


def _authorize_workspace(
    request: Request | None,
    workspace_id: str | None,
    *,
    require_scope: bool = False,
) -> None:
    """Validate the optional workspace scope before touching bucket storage.

    Catalog metadata remains public, but an install or publication carrying a
    workspace identifier must prove ownership before any download, local
    extraction, or remote upload occurs.  This keeps a client from using the
    bucket endpoints as a cross-workspace storage primitive.
    """
    if request is None or getattr(request.state, "user", None) is None:
        raise HTTPException(status_code=401, detail="Autenticação obrigatória.")
    if not workspace_id:
        if require_scope:
            raise HTTPException(
                status_code=400,
                detail="workspace_id é obrigatório para operações de storage.",
            )
        return
    from backend.api.handlers.workspaces import require_workspace_access

    workspace = require_workspace_access(workspace_id, request)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado.")


def _authorize_bucket_workspace(
    request: Request, bucket_id: str, workspace_id: str
) -> None:
    """Require that the local bucket belongs to the authorized workspace."""
    from backend.services import rag_buckets
    from backend.workspace.runtime_settings import runtime_settings

    bucket = rag_buckets.get_bucket(runtime_settings, bucket_id)
    if bucket is None or bucket.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Bucket não encontrado.")


@router.get("/catalog")
async def get_catalog(q: str | None = None) -> list[dict]:
    try:
        return await _list_catalog(q)
    except MemoryBucketsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/install")
async def post_install(req: InstallRequest, request: Request) -> dict:
    _authorize_workspace(request, req.workspace_id)
    try:
        collection = await download_memory_bucket(
            req.bucket_id, workspace_id=req.workspace_id
        )
    except MemoryBucketsError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "installed", "collection": collection}


@router.post("/publish")
async def post_publish(req: PublishRequest, request: Request) -> dict:
    _authorize_workspace(request, req.workspace_id, require_scope=True)
    workspace_id = req.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=400,
            detail="workspace_id é obrigatório para operações de storage.",
        )
    _authorize_bucket_workspace(request, req.bucket_id, workspace_id)
    from backend.services import license

    token = license._get_token()
    if not token:
        return {
            "status": "error",
            "error": "Nenhuma conta vectora.company conectada (VECTORA_TOKEN ausente).",
        }
    try:
        remote_bucket_id = await publish_memory_bucket(
            req.bucket_id,
            req.name,
            req.description,
            req.license,
            session_token=token,
        )
    except MemoryBucketsError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "published", "bucket_id": remote_bucket_id}
