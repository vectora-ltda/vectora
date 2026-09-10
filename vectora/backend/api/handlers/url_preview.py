"""Preview seguro de uma URL isolada colada no composer."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/url-preview", tags=["url-preview"])

MAX_PREVIEW_BYTES = 512_000


def _public_host(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, None)
        if not addresses:
            return False
        return all(
            not (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            )
            for item in addresses
            if (address := ipaddress.ip_address(item[4][0]))
        )
    except (OSError, ValueError):
        return False


def _validated_ip(host: str) -> str | None:
    """Retorna um IP público validado para evitar uma nova resolução DNS."""
    try:
        addresses = socket.getaddrinfo(host, None)
        public = [
            str(item[4][0])
            for item in addresses
            if not (
                (address := ipaddress.ip_address(item[4][0])).is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            )
        ]
        return public[0] if public else None
    except (OSError, ValueError):
        return None


@router.get("")
async def preview_url(
    url: Annotated[str, Query(min_length=8, max_length=2048)],
) -> dict[str, str | None]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL deve usar http ou https")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(status_code=400, detail="URL não pode conter credenciais")
    validated_ip = await asyncio.to_thread(_validated_ip, parsed.hostname)
    if validated_ip is None:
        raise HTTPException(status_code=403, detail="destino não permitido")
    port = parsed.port
    ip_host = f"[{validated_ip}]" if ":" in validated_ip else validated_ip
    request_url = parsed._replace(
        netloc=f"{ip_host}:{port}" if port else ip_host
    ).geturl()
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(4.0, connect=2.0),
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                request_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Host": parsed.hostname + (f":{port}" if port else ""),
                },
            ) as response:
                if 300 <= response.status_code < 400:
                    raise HTTPException(
                        status_code=403, detail="redirecionamento não permitido"
                    )
                if response.status_code >= 400:
                    raise HTTPException(status_code=502, detail="preview indisponível")
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    raise HTTPException(
                        status_code=415, detail="conteúdo não suportado"
                    )
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_PREVIEW_BYTES:
                    raise HTTPException(status_code=413, detail="resposta muito grande")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_PREVIEW_BYTES:
                        raise HTTPException(
                            status_code=413, detail="resposta muito grande"
                        )
                    chunks.append(chunk)
                text = b"".join(chunks).decode("utf-8", errors="replace")

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

        def clean(value: str | None) -> str | None:
            if value is None:
                return None
            return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()[:300] or None

        return {
            "url": url,
            "title": clean(title_match.group(1) if title_match else None),
            "description": clean(meta("description")),
            "image": clean(meta("og:image")),
            "origin": parsed.hostname + (f":{port}" if port else ""),
        }
    except HTTPException:
        raise
    except (httpx.HTTPError, UnicodeError) as exc:
        raise HTTPException(status_code=502, detail="preview indisponível") from exc
