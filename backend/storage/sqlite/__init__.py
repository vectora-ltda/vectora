"""Pool de conexões SQLite assíncronas."""

from __future__ import annotations

from backend.storage.sqlite.pool import AsyncConnectionPool

__all__ = ["AsyncConnectionPool"]
