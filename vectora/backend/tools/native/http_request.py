"""Tool: HTTP GET/POST/PUT requests (async)."""

import json
import logging

import httpx

from backend.services.egress_policy import EgressPolicy, is_url_allowed
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


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
        policy = None
        if ctx and ctx.workspace_id:
            policy = EgressPolicy(workspace_id=ctx.workspace_id)

        allowed, reason = is_url_allowed(url, policy)
        if not allowed:
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
        async with httpx.AsyncClient(timeout=10) as client:
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
