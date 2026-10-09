"""Validação compartilhada do endpoint CDP publicado pelo Electron."""

from __future__ import annotations

import os
from urllib.parse import urlparse

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def electron_cdp_endpoint() -> str:
    """Resolve e valida o endpoint CDP local, incluindo porta e IPv6."""
    endpoint = os.environ.get("VECTORA_ELECTRON_CDP_URL", "").strip()
    if not endpoint:
        port = os.environ.get("VECTORA_ELECTRON_CDP_PORT", "9223").strip()
        if port.isdigit():
            endpoint = f"http://127.0.0.1:{port}"
        else:
            raise RuntimeError("VECTORA_ELECTRON_CDP_PORT inválido")
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("VECTORA_ELECTRON_CDP_URL contém porta inválida") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOOPBACK_HOSTS
        or port is None
        or not 1 <= port <= 65535
    ):
        raise RuntimeError("VECTORA_ELECTRON_CDP_URL deve apontar para loopback")
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise RuntimeError("VECTORA_ELECTRON_CDP_URL inválido")
    return endpoint
