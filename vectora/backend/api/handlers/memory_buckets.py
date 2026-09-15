"""Memory Buckets — catálogo, instalação e publicação de buckets RAG
compartilhados.

Endpoints (montados em server.py):
    GET  /rag-library/catalog — lista buckets publicados (id, embed_model,
                                 verified, downloads_count, license, ...)
    POST /rag-library/install — baixa e instala um bucket como coleção
                                 LanceDB isolada (`shared_{bucket_id}`)
    POST /rag-library/publish — empacota e publica uma coleção local

Download é sempre grátis (decisão de produto) — sem gate de tier/quota.
Publicação exige um `session_token` de conta vectora.company — reaproveita
o mesmo `VECTORA_TOKEN` já usado pelo license check (`backend.services.
license._get_token`), não um fluxo de login company↔desktop separado (que
não existe hoje). Sem token configurado, o endpoint retorna erro claro em
vez de tentar publicar sem credencial.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.services.memory_buckets import (
    MemoryBucketsError,
    download_memory_bucket,
    publish_memory_bucket,
)
from backend.services.memory_buckets import list_catalog as _list_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag-library", tags=["memory-library"])


class InstallRequest(BaseModel):
    bucket_id: str


class PublishRequest(BaseModel):
    bucket_id: str
    name: str
    description: str
    license: str


@router.get("/catalog")
async def get_catalog(q: str | None = None) -> list[dict]:
    return await _list_catalog(q)


@router.post("/install")
async def post_install(req: InstallRequest) -> dict:
    try:
        collection = await download_memory_bucket(req.bucket_id)
    except MemoryBucketsError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "installed", "collection": collection}


@router.post("/publish")
async def post_publish(req: PublishRequest) -> dict:
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
