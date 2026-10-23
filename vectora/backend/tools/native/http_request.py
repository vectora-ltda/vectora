"""Tool: HTTP GET/POST/PUT requests (async)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from backend.services.egress_policy import EgressPolicy, async_is_url_allowed
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


class _PinIPBackend(AutoBackend):
    """Backend de rede que substitui a resolução de hostname pelo IP validado na conexão TCP."""

    def __init__(self, pinned_ip: str) -> None:
        super().__init__()
        self.pinned_ip = pinned_ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109
        local_address: str | None = None,
        socket_options: Any
        | None = None,  # Any justified: assinatura herdada de httpcore.AutoBackend.connect_tcp
    ) -> httpcore.AsyncNetworkStream:
        return await super().connect_tcp(
            self.pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinIPTransport(httpx.AsyncHTTPTransport):
    """Transporte HTTP assíncrono que reutiliza IP validado contra DNS rebinding."""

    def __init__(
        self,
        pinned_ip: str,
        **kwargs: Any,  # Any justified: repasse transparente de opções de httpx.AsyncHTTPTransport
    ) -> None:
        super().__init__(**kwargs)
        pool = self._pool
        ssl_context = getattr(pool, "_ssl_context", None)
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=_PinIPBackend(pinned_ip),
            ssl_context=ssl_context,
        )


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="native",
        destructive=False,
        icon="globe",
    )
)
async def http_request(
    method: str,
    url: str,
    body: str | None = None,
    headers: str | None = None,
    ctx: ToolContext | None = None,
) -> str:
    """Faz requisição HTTP async.

    Args:
        method: GET, POST, PUT, DELETE
        url: URL completa
        body: JSON body (opcional)
        headers: JSON headers (opcional)
        ctx: Contexto da tool

    Returns:
        Response body ou erro
    """
    try:
        policy = EgressPolicy.for_workspace(ctx.workspace_id if ctx else None)

        allowed, reason, pinned_ip = await async_is_url_allowed(url, policy)
        if not allowed or not pinned_ip:
            return f"error: requisição bloqueada pela política de egress: {reason}"

        logger.info(
            "http_request_started",
            extra={
                "tool": "http_request",
                "operation": method.upper(),
                "endpoint": url,
                "status": "started",
            },
        )
        transport = PinIPTransport(pinned_ip)
        async with httpx.AsyncClient(
            transport=transport, timeout=10, follow_redirects=False
        ) as client:
            req_headers = {}
            if headers:
                req_headers = json.loads(headers)
            req_body = None
            if body:
                req_body = body

            resp = await client.request(
                method.upper(), url, content=req_body, headers=req_headers
            )
            logger.info(
                "http_request_completed",
                extra={
                    "tool": "http_request",
                    "operation": method.upper(),
                    "endpoint": url,
                    "status": getattr(resp, "status_code", None),
                    "output_length": len(resp.text),
                },
            )
            return resp.text
    except Exception as e:
        return f"error: {e}"
