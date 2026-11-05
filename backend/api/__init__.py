"""Vectora API — FastAPI + SSE streaming.

Reexporta `create_app()` (`src/api/server.py`) — fábrica que monta a app
FastAPI com os routers de `src.api.handlers` e os middlewares de
`src.api.middleware`, em modo `chat` (API + SPA estática) ou `headless`.
"""

from __future__ import annotations

from backend.api.server import create_app

__all__ = ["create_app"]
