"""Preview seguro de uma URL isolada colada no composer."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/url-preview", tags=["url-preview"])


def _public_host(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, None)
        return all(
            not ipaddress.ip_address(item[4][0]).is_private for item in addresses
        )
    except (OSError, ValueError):
        return False


@router.get("")
async def preview_url(
    url: Annotated[str, Query(min_length=8, max_length=2048)],
) -> dict[str, str | None]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL deve usar http ou https")
    if not await asyncio.to_thread(_public_host, parsed.hostname):
        raise HTTPException(status_code=403, detail="destino não permitido")
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(4.0, connect=2.0)
        ) as client:
            response = await client.get(
                url, headers={"Accept": "text/html,application/xhtml+xml"}
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="preview indisponível")
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise HTTPException(status_code=415, detail="conteúdo não suportado")
        text = response.text[:512_000]
        import re

        def meta(name: str) -> str | None:
            match = re.search(
                rf'<meta[^>]+(?:name|property)=["\']{name}["\'][^>]+content=["\']([^"\']*)',
                text,
                re.IGNORECASE,
            )
            return match.group(1)[:300] if match else None

        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL
        )
        return {
            "url": url,
            "title": (title_match.group(1).strip()[:300] if title_match else None),
            "description": meta("description"),
            "image": meta("og:image"),
            "origin": parsed.netloc,
        }
    except HTTPException:
        raise
    except (httpx.HTTPError, UnicodeError) as exc:
        raise HTTPException(status_code=502, detail="preview indisponível") from exc
