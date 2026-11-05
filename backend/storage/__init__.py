"""Camada de storage do Vectora.

Módulos:
    sqlite/      — pool de conexões aiosqlite com PRAGMAs de hardening
    lancedb/     — cache de conexões + índices IVF/FTS
    vectorstore/ — VectorStoreBackend nativo (LanceDBBackend/QdrantBackend)
    migrations/  — runner de schema versioning
    protocols.py — Protocols tipados para todos os backends
    factory.py   — singletons de store, vector_store_backend, pg_pool

Lazy imports (``__getattr__``) — mesmo padrão de ``backend.mcp`` — evitam
puxar aiosqlite/LanceDB/asyncpg no import do pacote quando só um submódulo
específico é necessário.
"""

from __future__ import annotations

__all__ = [
    "get_store",
    "get_vector_store",
    "get_vector_store_backend",
    "storage_health",
]


def __getattr__(name: str) -> object:
    if name in (
        "get_store",
        "get_vector_store",
        "get_vector_store_backend",
        "storage_health",
    ):
        from backend.storage import factory

        return getattr(factory, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
