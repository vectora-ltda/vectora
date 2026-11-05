"""Rate limiting para endpoints sensíveis do Vectora.

Usa slowapi (wrapper de limits sobre FastAPI).

Limites por endpoint:
    POST /auth/signin         → 5 req/min por IP (failure-based logic via auth service)
    POST /auth/signup         → 3 req/hora por IP
    POST /auth/change-password → 3 req/hora por user_id
    POST /auth/refresh        → 20 req/min por IP

Os outros endpoints (StreamChat, etc.) não têm rate limit aqui —
são protegidos pela autenticação obrigatória.

``limiter`` é criado em nível de módulo (não dentro de ``attach_limiter``) para
que handlers em outros módulos possam usar ``@limiter.limit(...)`` como
decorator — o decorator precisa da instância no momento em que o módulo do
handler é importado, o que acontece antes do app subir (quando
``attach_limiter`` rodaria). Backend memory:// por padrão; storage
distribuído (Redis) é religado em ``attach_limiter`` quando disponível.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

#: Singleton importável por qualquer handler (`from backend.api.middleware.rate_limit
#: import limiter`) — necessário para o decorator `@limiter.limit(...)` funcionar em
#: módulos importados antes de `attach_limiter(app)` rodar.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def attach_limiter(app: FastAPI) -> None:
    """Liga o ``limiter`` (módulo) ao app FastAPI e religa o storage backend."""
    try:
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded

        # Storage distribuído: com REDIS_URL os contadores vivem no Redis e
        # valem para todas as réplicas; sem ele (ou com o serviço fora do ar
        # — redis_url tem default no defaults.env), memória local (já é o
        # default do `limiter` criado acima).
        from backend.persistence.kv import redis_reachable
        from backend.settings import settings

        storage_uri = (settings.redis_url or "").strip() or "memory://"
        if storage_uri.startswith("redis") and not redis_reachable(storage_uri):
            storage_uri = "memory://"
        if storage_uri != "memory://":
            from limits.storage import storage_from_string

            limiter._storage = storage_from_string(storage_uri)

        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

        logger.info(
            "rate_limit: slowapi configurado (storage=%s)",
            "redis" if storage_uri.startswith("redis") else "memory",
        )
    except Exception as exc:
        logger.warning("rate_limit: slowapi não disponível: %s", exc)


def get_limiter(request: Request) -> Any:
    """Retorna o limiter do state do app (helper para handlers)."""
    return getattr(request.app.state, "limiter", None)
