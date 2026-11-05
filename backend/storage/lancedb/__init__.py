"""Helpers de conexão e índice para LanceDB (lite mode)."""

from __future__ import annotations

from backend.storage.lancedb.connection import LanceDBConnectionCache, get_lancedb
from backend.storage.lancedb.index import create_fts_index, create_ivf_index
from backend.storage.lancedb.optimize import optimize_table, schedule_optimize

__all__ = [
    "LanceDBConnectionCache",
    "create_fts_index",
    "create_ivf_index",
    "get_lancedb",
    "optimize_table",
    "schedule_optimize",
]
