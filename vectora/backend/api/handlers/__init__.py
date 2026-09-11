"""Routers REST/SSE da API do Vectora — um módulo por domínio.

Cada submódulo expõe um `router: APIRouter` (e, em alguns casos, routers
auxiliares) montados em `src.api.server.create_app()`. Reexporta todos aqui
para que o factory importe direto de `src.api.handlers`.
"""

from __future__ import annotations

from backend.api.handlers.admin import router as admin_router
from backend.api.handlers.artifacts import router as artifacts_router
from backend.api.handlers.auth import router as auth_router
from backend.api.handlers.chat import router as chat_router
from backend.api.handlers.license import router as license_router
from backend.api.handlers.memory import router as memory_router
from backend.api.handlers.oauth import router as oauth_router
from backend.api.handlers.plugins import router as plugins_router
from backend.api.handlers.share import router as share_router
from backend.api.handlers.skills import router as skills_router
from backend.api.handlers.terminal import router as terminal_router
from backend.api.handlers.threads import router as threads_router
from backend.api.handlers.tools import router as tools_router
from backend.api.handlers.url_preview import router as url_preview_router
from backend.api.handlers.workspaces import router as workspaces_router
from backend.api.handlers.workspaces import view_router as workspaces_view_router
from backend.api.handlers.workspaces import (
    workspace_scoped_router as workspaces_scoped_view_router,
)

__all__ = [
    "admin_router",
    "artifacts_router",
    "auth_router",
    "chat_router",
    "license_router",
    "memory_router",
    "oauth_router",
    "plugins_router",
    "share_router",
    "skills_router",
    "terminal_router",
    "threads_router",
    "tools_router",
    "url_preview_router",
    "workspaces_router",
    "workspaces_scoped_view_router",
    "workspaces_view_router",
]
