"""Authenticated proxy for the remote VEXT catalog used by the Library."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Response

from backend.api.handlers.vext import _require_user
from backend.services import registry_client
from backend.services.vext import MAX_PACKAGE_BYTES

_EXTENSION_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
ExtensionId = Annotated[str, Path(pattern=_EXTENSION_ID.pattern)]
Version = Annotated[str, Path(pattern=_VERSION.pattern)]

router = APIRouter(
    prefix="/registry/extensions",
    tags=["registry"],
    dependencies=[Depends(_require_user)],
)


async def _fetch_json(url: str, *, unavailable_detail: str) -> dict[str, object]:
    try:
        async with httpx.AsyncClient(
            timeout=registry_client.HTTP_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            raise HTTPException(
                status_code=404, detail="extensão não encontrada"
            ) from exc
        raise HTTPException(status_code=502, detail=unavailable_detail) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=unavailable_detail) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="resposta inválida do registry")
    return payload


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


@router.get("/{extension_id}/versions")
async def extension_versions(extension_id: ExtensionId) -> dict[str, object]:
    """Return the published versions for one extension."""
    payload = await _fetch_json(
        registry_client.extension_versions_url(extension_id),
        unavailable_detail="versões do registry indisponíveis",
    )
    entries = payload.get("entries", payload.get("versions", []))
    if not isinstance(entries, list):
        raise HTTPException(status_code=502, detail="resposta inválida do registry")
    return {"entries": entries, "total": len(entries)}


@router.get("/{extension_id}/readme")
async def extension_readme(extension_id: ExtensionId) -> dict[str, str]:
    """Return the published README as the content object consumed by Library."""
    payload = await _fetch_json(
        registry_client.extension_readme_url(extension_id),
        unavailable_detail="README do registry indisponível",
    )
    content = payload.get("content", payload.get("readme"))
    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail="resposta inválida do registry")
    return {"content": content}


@router.get("/{extension_id}/download/{version}")
async def download_extension(extension_id: ExtensionId, version: Version) -> Response:
    """Bound and verify an artifact before returning it through app origin."""
    url = registry_client.extension_download_url(extension_id, version)
    digest = hashlib.sha256()
    content = bytearray()
    response_headers: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(
            timeout=registry_client.HTTP_TIMEOUT, follow_redirects=False
        ) as client:
            async with client.stream("GET", url) as upstream:
                upstream.raise_for_status()
                content_length = upstream.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=502, detail="tamanho inválido no registry"
                        ) from exc
                    if declared_size < 0 or declared_size > MAX_PACKAGE_BYTES:
                        raise HTTPException(
                            status_code=413, detail="artefato excede o limite"
                        )
                async for chunk in upstream.aiter_bytes():
                    if len(content) + len(chunk) > MAX_PACKAGE_BYTES:
                        raise HTTPException(
                            status_code=413, detail="artefato excede o limite"
                        )
                    content.extend(chunk)
                    digest.update(chunk)
                response_headers = {
                    "etag": upstream.headers.get("etag", ""),
                    "x-vext-digest": upstream.headers.get("x-vext-digest", ""),
                }
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            raise HTTPException(
                status_code=404, detail="artefato não encontrado"
            ) from exc
        raise HTTPException(status_code=502, detail="falha no registry") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="registry indisponível") from exc

    expected = response_headers["x-vext-digest"].strip().lower()
    if expected:
        if expected.startswith("sha256:"):
            expected = expected.removeprefix("sha256:")
        actual = digest.hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or not hmac.compare_digest(
            expected, actual
        ):
            raise HTTPException(status_code=502, detail="digest do artefato inválido")
    return Response(
        content=bytes(content),
        media_type="application/vnd.vectora.vext+zip",
        headers={
            "Content-Length": str(len(content)),
            "ETag": response_headers["etag"],
            "X-VEXT-Digest": response_headers["x-vext-digest"],
        },
    )
