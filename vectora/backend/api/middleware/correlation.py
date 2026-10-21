"""Middleware para correlação de requisições e cabeçalhos de versão de contrato."""

from __future__ import annotations

import logging
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.contracts import CONTRACT_VERSION

logger = logging.getLogger(__name__)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _resolve_id(raw_val: str | None) -> str:
    """Valida ID de correlação/requisição (alfanumérico + ._:- até 128 chars) ou gera UUID."""
    if raw_val and _ID_PATTERN.fullmatch(raw_val):
        return raw_val
    return str(uuid.uuid4())


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Resolve ou gera Correlation ID e Request ID validados
        correlation_id = _resolve_id(request.headers.get("X-Correlation-ID"))
        request_id = _resolve_id(request.headers.get("X-Request-ID"))

        request.state.correlation_id = correlation_id
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Erro não tratado na requisição (correlation_id=%s, request_id=%s)",
                correlation_id,
                request_id,
            )
            response = JSONResponse(
                status_code=500,
                content={"error": "INTERNAL", "message": "Internal Server Error"},
            )

        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Vectora-Contract-Version"] = CONTRACT_VERSION
        return response
