"""Proxy for the remote VEXT catalog used by the local Library UI."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Response

from backend.services import registry_client

router = APIRouter(prefix="/registry/extensions", tags=["registry"])


@router.get("")
async def list_extensions(q: str | None = None) -> dict[str, object]:
    """Return published extensions, optionally filtered by the search term."""
    entries = await registry_client.fetch_catalog("extensions")
    if q:
        needle = q.strip().lower()
        entries = [
            entry
            for entry in entries
            if needle
            in f"{entry.get('name', '')} {entry.get('description', '')}".lower()
        ]
    return {"entries": entries, "total": len(entries)}


@router.get("/{extension_id}/download/{version}")
async def download_extension(extension_id: str, version: str) -> Response:
    """Stream a published artifact through the app origin for installation."""
    url = registry_client.extension_download_url(extension_id, version)
    try:
        async with httpx.AsyncClient(
            timeout=registry_client.HTTP_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(
            status_code=status, detail="artefato não encontrado"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="registry indisponível") from exc

    return Response(
        content=response.content,
        media_type="application/vnd.vectora.vext+zip",
        headers={
            "Content-Length": str(len(response.content)),
            "ETag": response.headers.get("etag", ""),
            "X-VEXT-Digest": response.headers.get("x-vext-digest", ""),
        },
    )
