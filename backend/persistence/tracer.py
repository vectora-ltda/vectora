"""VectoraTracer — Observabilidade interna via SQLite.

Grava spans de execução (nodes, tools, MCP calls) em ~/.vectora/data/traces.db.
KISS: sem deps externas, sem servidor, sem Docker. Mesmo padrão SQLite do projeto.

Uso em código async (nodes, async tools):
    from vectora.services.tracer import tracer
    async with tracer.span("orchestrator", "route", session_id=42) as s:
        result = await do_work()
        s.set(routing="search", in_tokens=120, out_tokens=35)

Uso em código sync (tools síncronos):
    t0 = time.perf_counter()
    result = do_sync_work()
    tracer.record_sync("web_search", "call", time.perf_counter() - t0, {"query": q})

Visualizar no terminal:
    vectora traces
    vectora traces --session 1212
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id      TEXT PRIMARY KEY,
    parent_id    TEXT,
    session_id   INTEGER,
    node         TEXT NOT NULL,
    event        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ok',
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    duration_ms  REAL,
    in_tokens    INTEGER,
    out_tokens   INTEGER,
    metadata     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id);
CREATE INDEX IF NOT EXISTS idx_spans_node    ON spans(node);
CREATE INDEX IF NOT EXISTS idx_spans_started ON spans(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_spans_parent  ON spans(parent_id);
"""

_INSERT = """
INSERT OR IGNORE INTO spans
    (span_id, parent_id, session_id, node, event, status,
     started_at, ended_at, duration_ms, in_tokens, out_tokens, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


# ---------------------------------------------------------------------------
# _SpanCtx — acumula dados durante a execução do span
# ---------------------------------------------------------------------------


@dataclass
class _SpanCtx:
    """Contexto mutável de um span ativo. Passado ao caller via 'async with'."""

    span_id: str
    node: str
    event: str
    session_id: int | None
    parent_id: str | None
    _started_at: float = field(default_factory=time.perf_counter, repr=False)
    _started_iso: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(), repr=False
    )
    status: str = "ok"
    in_tokens: int | None = None
    out_tokens: int | None = None
    _metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def set(self, **kwargs: Any) -> None:
        """Adiciona campos extras ao span: routing, model, n_docs, etc."""
        for k, v in kwargs.items():
            if k == "in_tokens":
                self.in_tokens = int(v) if v is not None else None
            elif k == "out_tokens":
                self.out_tokens = int(v) if v is not None else None
            elif k == "status":
                self.status = str(v)
            else:
                self._metadata[k] = v

    def _duration_ms(self) -> float:
        return (time.perf_counter() - self._started_at) * 1000


# ---------------------------------------------------------------------------
# VectoraTracer
# ---------------------------------------------------------------------------


class VectoraTracer:
    """Tracer KISS baseado em SQLite. Thread-safe para sync; async-safe para nodes.

    O asyncio.Semaphore(1) em _write_semaphore serializa escritas assíncronas ao
    aiosqlite. Embora SQLite em WAL mode tolere múltiplos leitores, escritas
    concorrentes da mesma coroutine pool ainda podem causar "database is locked"
    — o semaphore garante que apenas um span seja inserido por vez.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path: Path | None = None
        if db_path:
            self._db_path = Path(db_path)
        self._initialized = False
        # Lazy-initialized: asyncio.Semaphore só pode ser criado dentro de um event loop.
        # Inicializado no primeiro uso via _get_write_semaphore().
        self._write_semaphore: asyncio.Semaphore | None = None

    def _get_write_semaphore(self) -> asyncio.Semaphore:
        """Retorna (criando se necessário) o semaphore de escrita async."""
        if self._write_semaphore is None:
            self._write_semaphore = asyncio.Semaphore(1)
        return self._write_semaphore

    def _resolve_path(self) -> Path:
        """Resolve caminho do banco de traces (lazy — só no primeiro uso)."""
        if self._db_path:
            return self._db_path
        try:
            from backend.settings import settings

            data_dir = Path(settings.data_dir) if settings.data_dir else None  # type: ignore[arg-type]
            if data_dir:
                return data_dir / "traces.db"
        except Exception:
            pass
        # Último fallback (settings indisponível) — lê VECTORA_HOME direto do
        # ambiente para não vazar dados de teste no home real do usuário.
        import os

        home = os.environ.get("VECTORA_HOME")
        base = Path(home) if home else Path.home() / ".vectora"
        return base / "data" / "traces.db"

    def _ensure_sync(self) -> Path:
        """Cria banco e schema usando sqlite3 síncrono (thread-safe)."""
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(path)) as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        return path

    # ------------------------------------------------------------------
    # Async path (nodes, async tools)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def span(
        self,
        node: str,
        event: str,
        session_id: int | None = None,
        parent_id: str | None = None,
    ) -> AsyncGenerator[_SpanCtx]:
        """Context manager async para instrumentar um bloco de código.

        Example:
            async with tracer.span("invoke_llm", "call", session_id=42) as s:
                result = await llm.ainvoke(messages)
                s.set(in_tokens=100, out_tokens=40, model="gemini-2.0-flash")
        """
        import aiosqlite

        ctx = _SpanCtx(
            span_id=str(uuid.uuid4()),
            node=node,
            event=event,
            session_id=session_id,
            parent_id=parent_id,
        )

        try:
            yield ctx
        except Exception as exc:
            ctx.status = "error"
            ctx._metadata["error"] = type(exc).__name__
            raise
        finally:
            ended = datetime.now(UTC).isoformat()
            duration = ctx._duration_ms()
            meta_json = json.dumps(ctx._metadata)
            path = self._resolve_path()

            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                async with self._get_write_semaphore():
                    async with aiosqlite.connect(str(path)) as db:
                        await db.execute("PRAGMA journal_mode=WAL;")
                        await db.executescript(_SCHEMA)
                        await db.execute(
                            _INSERT,
                            (
                                ctx.span_id,
                                ctx.parent_id,
                                ctx.session_id,
                                ctx.node,
                                ctx.event,
                                ctx.status,
                                ctx._started_iso,
                                ended,
                                duration,
                                ctx.in_tokens,
                                ctx.out_tokens,
                                meta_json,
                            ),
                        )
                        await db.commit()
            except Exception:
                logger.debug("tracer: falha ao gravar span (ignorado)", exc_info=True)

    # ------------------------------------------------------------------
    # Sync path (ferramentas síncronas)
    # ------------------------------------------------------------------

    def record_sync(
        self,
        node: str,
        event: str,
        elapsed_s: float,
        metadata: dict[str, Any] | None = None,
        session_id: int | None = None,
        status: str = "ok",
    ) -> None:
        """Grava span síncrono (thread-safe via sqlite3).

        Use em tools síncronos:
            t0 = time.perf_counter()
            result = do_work()
            tracer.record_sync("web_search", "call", time.perf_counter() - t0)
        """
        try:
            path = self._ensure_sync()
            now = datetime.now(UTC).isoformat()
            duration_ms = elapsed_s * 1000
            meta_json = json.dumps(metadata or {})
            span_id = str(uuid.uuid4())
            ended = datetime.now(UTC).isoformat()
            with sqlite3.connect(str(path)) as conn:
                conn.execute(
                    _INSERT,
                    (
                        span_id,
                        None,
                        session_id,
                        node,
                        event,
                        status,
                        now,
                        ended,
                        duration_ms,
                        None,
                        None,
                        meta_json,
                    ),
                )
        except Exception:
            logger.debug("tracer: falha ao gravar span sync (ignorado)", exc_info=True)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get_session(self, session_id: int, limit: int = 200) -> list[dict]:
        """Retorna todos os spans de uma session (mais recentes primeiro)."""
        return await self._query(
            "SELECT * FROM spans WHERE session_id=? ORDER BY started_at DESC LIMIT ?",
            (session_id, limit),
        )

    async def get_recent(self, n: int = 50) -> list[dict]:
        """Retorna os N spans mais recentes (qualquer session)."""
        return await self._query(
            "SELECT * FROM spans ORDER BY started_at DESC LIMIT ?",
            (n,),
        )

    async def clear_session(self, session_id: int) -> int:
        """Remove todos os spans de uma session. Retorna quantidade removida."""
        import aiosqlite

        try:
            path = self._resolve_path()
            if not path.exists():
                return 0
            async with aiosqlite.connect(str(path)) as db:
                cur = await db.execute(
                    "DELETE FROM spans WHERE session_id=?", (session_id,)
                )
                await db.commit()
                return cur.rowcount or 0
        except Exception:
            logger.debug("tracer: falha ao limpar session", exc_info=True)
            return 0

    async def clear_all(self) -> int:
        """Remove todos os spans. Retorna quantidade removida."""
        import aiosqlite

        try:
            path = self._resolve_path()
            if not path.exists():
                return 0
            async with aiosqlite.connect(str(path)) as db:
                cur = await db.execute("DELETE FROM spans")
                await db.commit()
                return cur.rowcount or 0
        except Exception:
            logger.debug("tracer: falha ao limpar traces", exc_info=True)
            return 0

    async def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Executa query e retorna lista de dicts."""
        import aiosqlite

        try:
            path = self._resolve_path()
            if not path.exists():
                return []
            async with aiosqlite.connect(str(path)) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(sql, params)
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
        except Exception:
            logger.debug("tracer: falha na query", exc_info=True)
            return []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

#: Instância singleton — importar diretamente:
#: ``from vectora.services.tracer import tracer``
tracer = VectoraTracer()

__all__ = ["VectoraTracer", "_SpanCtx", "tracer"]
