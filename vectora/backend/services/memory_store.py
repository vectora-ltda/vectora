"""Armazenamento e recuperação estruturada de memórias por domínio e escopo."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.services.context_manifest import ContextDomain

MemoryTrustLevel = Literal["trusted", "verified", "untrusted"]


@dataclass
class MemoryRecord:
    """Registro estruturado de memória no sistema."""

    id: str
    domain: ContextDomain
    scope_id: str  # workspace_id, thread_id ou "global"
    content: str
    importance: float = 0.5  # 0.0 a 1.0
    trust_level: MemoryTrustLevel = "trusted"
    provenance: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    accessed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryRetrievalResult:
    """Resultado com telemetria e custo de recuperação de memórias."""

    records: list[MemoryRecord]
    latency_ms: int
    estimated_cost_cents: float = 0.0
    domain: ContextDomain = "project"


class MemoryStore:
    """Repositório de memória unificado separando domínios operacionais e semânticos."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def store(
        self,
        record_id: str,
        domain: ContextDomain,
        scope_id: str,
        content: str,
        *,
        importance: float = 0.5,
        trust_level: MemoryTrustLevel = "trusted",
        provenance: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        record = MemoryRecord(
            id=record_id,
            domain=domain,
            scope_id=scope_id,
            content=content,
            importance=importance,
            trust_level=trust_level,
            provenance=provenance,
            metadata=metadata or {},
        )
        self._records[record_id] = record
        return record

    def retrieve(
        self,
        domain: ContextDomain | None = None,
        scope_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> MemoryRetrievalResult:
        t0 = time.perf_counter()
        results: list[MemoryRecord] = []

        for rec in self._records.values():
            if domain is not None and rec.domain != domain:
                continue
            if scope_id is not None and rec.scope_id not in (scope_id, "global"):
                continue
            if query is not None and query.lower() not in rec.content.lower():
                continue
            results.append(rec)

        results.sort(key=lambda r: (r.importance, r.created_at), reverse=True)
        selected = results[:limit]
        latency_ms = int((time.perf_counter() - t0) * 1000)

        return MemoryRetrievalResult(
            records=selected,
            latency_ms=latency_ms,
            domain=domain or "project",
        )


_DEFAULT_MEMORY_STORE = MemoryStore()


def get_memory_store() -> MemoryStore:
    return _DEFAULT_MEMORY_STORE
