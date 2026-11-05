"""Middlewares HTTP da API do Vectora — autenticação e rate limiting.

Reexporta `AuthMiddleware` (`auth.py`, valida JWT/sessão por request) e
`attach_limiter`/`get_limiter` (`rate_limit.py`, slowapi por IP) para que
`src.api.server.create_app()` os monte sem importar dos submódulos.
"""

from __future__ import annotations

from backend.api.middleware.auth import AuthMiddleware
from backend.api.middleware.rate_limit import attach_limiter, get_limiter

__all__ = ["AuthMiddleware", "attach_limiter", "get_limiter"]
