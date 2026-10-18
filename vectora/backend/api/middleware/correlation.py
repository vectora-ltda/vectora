"""Middleware para correlação de requisições e cabeçalhos de versão de contrato."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_CONTRACT_VERSION = "0.3.0"


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Resolve ou gera Correlation ID e Request ID
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        request.state.correlation_id = correlation_id
        request.state.request_id = request_id

        response = await call_next(request)

        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Vectora-Contract-Version"] = _CONTRACT_VERSION
        return response
