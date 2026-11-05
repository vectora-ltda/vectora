"""Persistência de estado: checkpoints de workspace, KV transiente e traces.

``checkpoint.py`` (backup/restore de workspace via git/snapshot — rewind),
``kv.py`` (KV genérico com fallback Redis/SQLite) e ``tracer.py`` (coleta
de traces de execução do grafo para diagnóstico).

Lazy imports (``__getattr__``) — mesmo padrão de ``backend.mcp`` — evitam
puxar aiosqlite/Redis no import do pacote quando só um submódulo
específico é necessário.
"""

from __future__ import annotations

__all__ = [
    "get_kv",
    "tracer",
]


def __getattr__(name: str) -> object:
    if name == "get_kv":
        from backend.persistence.kv import get_kv

        return get_kv
    if name == "tracer":
        from backend.persistence.tracer import tracer

        return tracer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
