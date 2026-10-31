"""Armazenamento e recuperação estruturada de memórias por domínio e escopo."""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
from collections.abc import Collection, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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
            return MemoryStore._parse_datetime(self.valid_until) > datetime.now(UTC)
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

    def __init__(self, path: str | Path | None = None) -> None:
        """Inicializa o estado da instância."""
        self._records: dict[str, MemoryRecord] = {}
        self._path = Path(path).expanduser() if path is not None else None
        self._lock = threading.RLock()
        self._metrics = {
            "writes": 0,
            "idempotent_writes": 0,
            "conflicts": 0,
            "retrievals": 0,
            "hits": 0,
        }
        if self._path is not None:
            self._initialize_persistence()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Executa a operação  connect."""
        if self._path is None:
            raise RuntimeError("este MemoryStore não possui persistência configurada")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_persistence(self) -> None:
        """Executa a operação  initialize persistence."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS context_memory_records (
                    record_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL NOT NULL,
                    trust_level TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    valid_until TEXT,
                    invalidated_at TEXT,
                    supersedes_id TEXT
                )
                """
            )
            rows = connection.execute("SELECT * FROM context_memory_records").fetchall()
        with self._lock:
            for row in rows:
                record = self._record_from_row(row)
                if record is not None:
                    self._records[record.id] = record

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> MemoryRecord | None:
        """Executa a operação  record from row."""
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(metadata, dict):
            return None
        try:
            return MemoryRecord(
                id=str(row["record_id"]),
                domain=row["domain"],
                scope_id=str(row["scope_id"]),
                content=str(row["content"]),
                importance=float(row["importance"]),
                trust_level=row["trust_level"],
                provenance=str(row["provenance"]),
                created_at=str(row["created_at"]),
                accessed_at=str(row["accessed_at"]),
                metadata=metadata,
                confidence=float(row["confidence"]),
                valid_until=(
                    str(row["valid_until"]) if row["valid_until"] is not None else None
                ),
                invalidated_at=(
                    str(row["invalidated_at"])
                    if row["invalidated_at"] is not None
                    else None
                ),
                supersedes_id=(
                    str(row["supersedes_id"])
                    if row["supersedes_id"] is not None
                    else None
                ),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _record_parameters(record: MemoryRecord) -> tuple[object, ...]:
        """Executa a operação  record parameters."""
        return (
            record.id,
            record.domain,
            record.scope_id,
            record.content,
            record.importance,
            record.trust_level,
            record.provenance,
            record.created_at,
            record.accessed_at,
            json.dumps(record.metadata, ensure_ascii=False, sort_keys=True),
            record.confidence,
            record.valid_until,
            record.invalidated_at,
            record.supersedes_id,
        )

    def _same_record(
        self,
        existing: MemoryRecord,
        *,
        domain: ContextDomain,
        scope_id: str,
        content: str,
        importance: float,
        trust_level: MemoryTrustLevel,
        provenance: str,
        metadata: JsonObject,
        confidence: float,
        valid_until: str | None,
        supersedes_id: str | None,
    ) -> bool:
        """Executa a operação  same record."""
        return (
            existing.domain == domain
            and existing.scope_id == scope_id
            and existing.content == content
            and existing.provenance == provenance
            and existing.metadata == metadata
            and existing.trust_level == trust_level
            and existing.importance == importance
            and existing.confidence == confidence
            and existing.valid_until == valid_until
            and existing.supersedes_id == supersedes_id
        )

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
        authorized_scope_ids: Collection[str] | None = None,
    ) -> MemoryRecord:
        """Grava uma memória de forma idempotente e detecta conflitos.

        Repetir exatamente a mesma gravação devolve o registro existente. Uma
        chave reutilizada com conteúdo, origem ou escopo diferente é rejeitada;
        o chamador deve escolher uma nova versão e relacioná-la por
        ``supersedes_id``. Isso evita sobrescritas silenciosas e mantém a
        auditoria da linha do tempo.
        """
        if not scope_id:
            raise ValueError("scope_id não pode ser vazio")
        if authorized_scope_ids is not None and scope_id not in authorized_scope_ids:
            raise PermissionError(f"escopo de memória não autorizado: {scope_id!r}")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance deve estar entre 0 e 1")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence deve estar entre 0 e 1")
        if valid_until is not None:
            valid_until = self._parse_datetime(valid_until).isoformat()
        clean_metadata = metadata or {}
        with self._lock:
            self._metrics["writes"] += 1
            existing = self._records.get(record_id)
            if existing is None and self._path is not None:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT * FROM context_memory_records WHERE record_id = ?",
                        (record_id,),
                    ).fetchone()
                if row is not None:
                    existing = self._record_from_row(row)
                    if existing is not None:
                        self._records[record_id] = existing
            if existing is not None:
                if self._same_record(
                    existing,
                    domain=domain,
                    scope_id=scope_id,
                    content=content,
                    importance=importance,
                    trust_level=trust_level,
                    provenance=provenance,
                    metadata=clean_metadata,
                    confidence=confidence,
                    valid_until=valid_until,
                    supersedes_id=supersedes_id,
                ):
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
                metadata=clean_metadata,
                confidence=confidence,
                valid_until=valid_until,
                supersedes_id=supersedes_id,
            )
            if self._path is not None:
                try:
                    with self._connect() as connection:
                        connection.execute(
                            """INSERT INTO context_memory_records
                            (record_id, domain, scope_id, content, importance,
                             trust_level, provenance, created_at, accessed_at,
                             metadata_json, confidence, valid_until, invalidated_at,
                             supersedes_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            self._record_parameters(record),
                        )
                except sqlite3.IntegrityError as exc:
                    # Another process may have won the same-key race after the
                    # cache lookup above. Resolve that race through the same
                    # idempotency/conflict contract instead of leaking a raw
                    # SQLite exception to callers.
                    with self._connect() as connection:
                        row = connection.execute(
                            "SELECT * FROM context_memory_records WHERE record_id = ?",
                            (record_id,),
                        ).fetchone()
                    concurrent = self._record_from_row(row) if row is not None else None
                    if concurrent is None:
                        raise MemoryConflictError(
                            f"memória {record_id!r} não pôde ser conciliada após uma corrida de gravação"
                        ) from exc
                    self._records[record_id] = concurrent
                    if self._same_record(
                        concurrent,
                        domain=domain,
                        scope_id=scope_id,
                        content=content,
                        importance=importance,
                        trust_level=trust_level,
                        provenance=provenance,
                        metadata=clean_metadata,
                        confidence=confidence,
                        valid_until=valid_until,
                        supersedes_id=supersedes_id,
                    ):
                        self._metrics["idempotent_writes"] += 1
                        return concurrent
                    self._metrics["conflicts"] += 1
                    raise MemoryConflictError(
                        f"memória {record_id!r} já existe com conteúdo ou proveniência diferente"
                    ) from exc
            self._records[record_id] = record
            return record

    def retrieve(
        self,
        domain: ContextDomain | None = None,
        scope_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
        authorized_scope_ids: Collection[str] | None = None,
    ) -> MemoryRetrievalResult:
        """Executa a operação retrieve."""
        if limit < 0:
            raise ValueError("limit não pode ser negativo")
        if scope_id is not None and not scope_id:
            raise ValueError("scope_id não pode ser vazio")
        if authorized_scope_ids is not None:
            allowed = set(authorized_scope_ids)
            if scope_id is not None and scope_id not in allowed:
                raise PermissionError(f"escopo de memória não autorizado: {scope_id!r}")
        else:
            allowed = None
        t0 = time.perf_counter()
        with self._lock:
            self._metrics["retrievals"] += 1
        now = datetime.now(UTC)
        terms = {term for term in (query or "").casefold().split() if term}
        ranked: list[tuple[float, MemoryRecord]] = []

        with self._lock:
            records = list(self._records.values())
        for rec in records:
            if not rec.active:
                continue
            if domain is not None and rec.domain != domain:
                continue
            if allowed is not None and rec.scope_id not in allowed:
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
        accessed_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._metrics["hits"] += len(selected)
            for rec in selected:
                rec.accessed_at = accessed_at
        if self._path is not None and selected:
            with self._connect() as connection:
                connection.executemany(
                    "UPDATE context_memory_records SET accessed_at = ? WHERE record_id = ?",
                    [(accessed_at, rec.id) for rec in selected],
                )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        return MemoryRetrievalResult(
            records=selected,
            latency_ms=latency_ms,
            domain=domain or "project",
        )

    def invalidate(self, record_id: str) -> bool:
        """Invalida uma versão sem removê-la do histórico auditável."""
        with self._lock:
            record = self._records.get(record_id)
            if record is None or not record.active:
                return False
            record.invalidated_at = datetime.now(UTC).isoformat()
            if self._path is not None:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE context_memory_records SET invalidated_at = ? WHERE record_id = ?",
                        (record.invalidated_at, record_id),
                    )
            return True

    def expire(self, *, before: str | None = None, batch_size: int = 500) -> int:
        """Invalida registros expirados em lotes atômicos e auditáveis."""
        if batch_size <= 0:
            raise ValueError("batch_size deve ser positivo")
        cutoff_dt = self._parse_datetime(before) if before else datetime.now(UTC)
        cutoff = cutoff_dt.isoformat()
        changed = 0
        if self._path is not None:
            while True:
                with self._connect() as connection:
                    rows = connection.execute(
                        "SELECT record_id FROM context_memory_records "
                        "WHERE invalidated_at IS NULL AND valid_until IS NOT NULL "
                        "AND valid_until <= ? ORDER BY record_id LIMIT ?",
                        (cutoff, batch_size),
                    ).fetchall()
                    if not rows:
                        break
                    ids = [str(row["record_id"]) for row in rows]
                    marks = ",".join("?" for _ in ids)
                    cursor = connection.execute(
                        "UPDATE context_memory_records SET invalidated_at = ? "  # noqa: S608  # nosec B608
                        f"WHERE invalidated_at IS NULL AND record_id IN ({marks})",
                        (cutoff, *ids),
                    )
                    changed += cursor.rowcount
                with self._lock:
                    for record_id in ids:
                        record = self._records.get(record_id)
                        if record is not None and record.invalidated_at is None:
                            record.invalidated_at = cutoff
            return changed

        with self._lock:
            for record in self._records.values():
                if not record.active or record.valid_until is None:
                    continue
                try:
                    expires_at = self._parse_datetime(record.valid_until)
                except ValueError:
                    continue
                if expires_at <= cutoff_dt:
                    record.invalidated_at = cutoff
                    changed += 1
        return changed

    def purge_invalidated(
        self, *, before: str | None = None, batch_size: int = 500
    ) -> int:
        """Remove fisicamente memórias já invalidadas após retenção.

        A invalidação mantém o histórico; este segundo passo é deliberado e
        limitado por lote para que expurgo de retenção não bloqueie o store nem
        apague registros ainda dentro da janela de auditoria.
        """
        if batch_size <= 0:
            raise ValueError("batch_size deve ser positivo")
        cutoff_dt = self._parse_datetime(before) if before else datetime.now(UTC)
        cutoff = cutoff_dt.isoformat()
        removed = 0
        if self._path is not None:
            while True:
                with self._connect() as connection:
                    rows = connection.execute(
                        "SELECT record_id FROM context_memory_records "
                        "WHERE invalidated_at IS NOT NULL AND invalidated_at <= ? "
                        "ORDER BY invalidated_at, record_id LIMIT ?",
                        (cutoff, batch_size),
                    ).fetchall()
                    if not rows:
                        break
                    ids = [str(row["record_id"]) for row in rows]
                    marks = ",".join("?" for _ in ids)
                    cursor = connection.execute(
                        "DELETE FROM context_memory_records "  # noqa: S608
                        f"WHERE record_id IN ({marks})",  # nosec B608
                        ids,
                    )
                    removed += cursor.rowcount
                with self._lock:
                    for record_id in ids:
                        self._records.pop(record_id, None)
            return removed

        with self._lock:
            expired_ids = [
                record_id
                for record_id, record in self._records.items()
                if record.invalidated_at is not None
                and self._parse_datetime(record.invalidated_at) <= cutoff_dt
            ][:batch_size]
            for record_id in expired_ids:
                self._records.pop(record_id, None)
            return len(expired_ids)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        """Executa a operação  parse datetime."""
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def metrics(self) -> dict[str, int]:
        """Retorna contadores de auditoria sem expor o armazenamento interno."""
        return dict(self._metrics)


def _default_memory_store_path() -> Path:
    """Executa a operação  default memory store path."""
    from backend.settings import settings

    return settings.vectora_home / "context-memory.db"


@functools.cache
def get_memory_store() -> MemoryStore:
    """Retorna o store padrão, inicializado somente no primeiro uso."""
    return MemoryStore(_default_memory_store_path())
