"""Armazenamento e recuperação estruturada de memórias por domínio e escopo."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from backend.services.context_manifest import ContextDomain
from backend.vtypes import JsonObject

MemoryTrustLevel = Literal["trusted", "verified", "untrusted"]


class MemoryConflictError(ValueError):
    """Indica uma tentativa de reutilizar uma chave com conteúdo diferente."""


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
    metadata: JsonObject = field(default_factory=dict)
    confidence: float = 1.0
    valid_until: str | None = None
    invalidated_at: str | None = None
    supersedes_id: str | None = None

    @property
    def active(self) -> bool:
        """Retorna se o registro ainda pode ser projetado no contexto."""
        if self.invalidated_at is not None:
            return False
        if self.valid_until is None:
            return True
        try:
            expiry = datetime.fromisoformat(self.valid_until)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            return expiry.astimezone(UTC) > datetime.now(UTC)
        except ValueError:
            return False


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
        self._metrics = {
            "writes": 0,
            "idempotent_writes": 0,
            "conflicts": 0,
            "retrievals": 0,
            "hits": 0,
        }

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
        metadata: JsonObject | None = None,
        confidence: float = 1.0,
        valid_until: str | None = None,
        supersedes_id: str | None = None,
    ) -> MemoryRecord:
        """Grava uma memória de forma idempotente e detecta conflitos.

        Repetir exatamente a mesma gravação devolve o registro existente. Uma
        chave reutilizada com conteúdo, origem ou escopo diferente é rejeitada;
        o chamador deve escolher uma nova versão e relacioná-la por
        ``supersedes_id``. Isso evita sobrescritas silenciosas e mantém a
        auditoria da linha do tempo.
        """
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance deve estar entre 0 e 1")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence deve estar entre 0 e 1")
        self._metrics["writes"] += 1
        existing = self._records.get(record_id)
        if existing is not None:
            same = (
                existing.domain == domain
                and existing.scope_id == scope_id
                and existing.content == content
                and existing.provenance == provenance
                and existing.metadata == (metadata or {})
                and existing.trust_level == trust_level
                and existing.supersedes_id == supersedes_id
            )
            if same:
                self._metrics["idempotent_writes"] += 1
                return existing
            self._metrics["conflicts"] += 1
            raise MemoryConflictError(
                f"memória {record_id!r} já existe com conteúdo ou proveniência diferente"
            )
        record = MemoryRecord(
            id=record_id,
            domain=domain,
            scope_id=scope_id,
            content=content,
            importance=importance,
            trust_level=trust_level,
            provenance=provenance,
            metadata=metadata or {},
            confidence=confidence,
            valid_until=valid_until,
            supersedes_id=supersedes_id,
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
        if limit < 0:
            raise ValueError("limit não pode ser negativo")
        t0 = time.perf_counter()
        self._metrics["retrievals"] += 1
        now = datetime.now(UTC)
        terms = {term for term in (query or "").casefold().split() if term}
        ranked: list[tuple[float, MemoryRecord]] = []

        for rec in self._records.values():
            if not rec.active:
                continue
            if domain is not None and rec.domain != domain:
                continue
            if scope_id is not None and rec.scope_id not in (scope_id, "global"):
                continue
            content_terms = set(rec.content.casefold().split())
            if terms and not terms.intersection(content_terms):
                continue
            try:
                age_days = max(
                    0.0,
                    (now - datetime.fromisoformat(rec.created_at)).total_seconds()
                    / 86400.0,
                )
            except ValueError:
                age_days = 365.0
            recency = 1.0 / (1.0 + age_days / 30.0)
            lexical = len(terms.intersection(content_terms)) / max(len(terms), 1)
            trust = {"trusted": 1.0, "verified": 0.8, "untrusted": 0.1}[rec.trust_level]
            scope_bonus = (
                0.15 if scope_id is not None and rec.scope_id == scope_id else 0.0
            )
            score = (
                0.35 * lexical
                + 0.25 * rec.importance
                + 0.20 * rec.confidence
                + 0.15 * trust
                + 0.05 * recency
                + scope_bonus
            )
            ranked.append((score, rec))

        ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        selected = [rec for _, rec in ranked[:limit]]
        self._metrics["hits"] += len(selected)
        accessed_at = datetime.now(UTC).isoformat()
        for rec in selected:
            rec.accessed_at = accessed_at
        latency_ms = int((time.perf_counter() - t0) * 1000)

        return MemoryRetrievalResult(
            records=selected,
            latency_ms=latency_ms,
            domain=domain or "project",
        )

    def invalidate(self, record_id: str) -> bool:
        """Invalida uma versão sem removê-la do histórico auditável."""
        record = self._records.get(record_id)
        if record is None or not record.active:
            return False
        record.invalidated_at = datetime.now(UTC).isoformat()
        return True

    def expire(self, *, before: str | None = None) -> int:
        """Invalida registros expirados ou anteriores ao limite informado."""
        cutoff = before or datetime.now(UTC).isoformat()
        changed = 0
        for record in self._records.values():
            if (
                record.active
                and record.valid_until is not None
                and record.valid_until <= cutoff
            ):
                record.invalidated_at = cutoff
                changed += 1
        return changed

    def metrics(self) -> dict[str, int]:
        """Retorna contadores de auditoria sem expor o armazenamento interno."""
        return dict(self._metrics)


_DEFAULT_MEMORY_STORE = MemoryStore()


def get_memory_store() -> MemoryStore:
    return _DEFAULT_MEMORY_STORE
