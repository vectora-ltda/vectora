"""Asynchronous Embedding Queue for Fire-and-Forget Vector Generation.

Manages SQLite-backed queue for embedding documents with Cohere.
Supports retry logic, exponential backoff, Dead Letter Queue (DLQ) for failures,
and reconciliation for crash recovery.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Self
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

Base = declarative_base()


class EmbeddingStatus(StrEnum):
    """Status values for embedding queue records.

    Using str mixin so values are stored as plain strings in SQLite —
    no migration needed and backward-compatible with existing rows.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    DLQ = "dlq"


class EmbeddingQueueRecord(Base):  # type: ignore[valid-type,misc]
    """Modelo SQLAlchemy para registros da fila de embedding."""

    __tablename__ = "embedding_queue"
    __table_args__ = (
        # Index on status for O(log n) WHERE status = '...' queries.
        # Critical for get_pending() and count_pending() which run every poll cycle.
        Index("ix_embedding_queue_status", "status"),
        # Index on job_id para get_job_stats() (progresso por pasta indexada).
        Index("ix_embedding_queue_job_id", "job_id"),
    )

    id = Column(Integer, primary_key=True)
    queue_id = Column(String(36), unique=True, nullable=False)
    # Agrupa chunks enfileirados por uma mesma operação de ingest (uma pasta),
    # permitindo barra de progresso por job. Nulo para enqueues avulsos.
    job_id = Column(String(36), nullable=True)
    text = Column(Text, nullable=False)
    collection = Column(String(255), nullable=False)
    doc_metadata = Column(String(4096), nullable=True)  # String JSON
    status = Column(
        String(20), default=EmbeddingStatus.PENDING
    )  # See EmbeddingStatus enum
    error_message = Column(Text, nullable=True)
    dlq_reason = Column(Text, nullable=True)  # Razão para movimentação para DLQ
    attempt_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    processed_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class EmbeddingQueue:
    """Gerenciador de fila para embedding de documentos quando API Cohere falha."""

    def __init__(self: Self, db_url: str) -> None:
        """Inicializa fila de embedding com conexão de banco de dados."""
        self.db_url = db_url
        self.engine: AsyncEngine | None = None
        self.AsyncSessionLocal: sessionmaker[AsyncSession] | None = None  # ty: ignore[invalid-type-arguments]

    async def init(self) -> None:
        """Inicializa motor de banco de dados assíncrono e cria tabelas."""
        # connect_args timeout: aiosqlite waits up to 30 s for a write lock
        # instead of immediately raising OperationalError: database is locked.
        # Needed because ingest_docs (tool) and BackgroundEmbeddingWorker both
        # write to this SQLite file concurrently.
        self.engine = create_async_engine(
            self.db_url,
            echo=False,
            connect_args={"timeout": 30},
        )

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # type: ignore[attr-defined]

            # Enable WAL mode for better concurrency (Reader + Writer simultaneously)
            # Critical for AsyncIO where Chat and BackgroundWorker write simultaneously
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            await conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            # Redundant with connect_args but explicit for clarity
            await conn.exec_driver_sql("PRAGMA busy_timeout=30000;")

            # Migração idempotente: tabelas antigas não têm `job_id`
            # (create_all não altera tabelas existentes). Adiciona a coluna +
            # índice se ausente, sem perder dados.
            cols = await conn.exec_driver_sql("PRAGMA table_info(embedding_queue);")
            column_names = {str(row._mapping["name"]) for row in cols.fetchall()}
            if "job_id" not in column_names:
                await conn.exec_driver_sql(
                    "ALTER TABLE embedding_queue ADD COLUMN job_id VARCHAR(36);"
                )
                await conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_embedding_queue_job_id "
                    "ON embedding_queue (job_id);"
                )

        self.AsyncSessionLocal = sessionmaker(  # ty: ignore[no-matching-overload]
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        logger.info(
            "embedding_queue_initialized",
            extra={"db_url": self.db_url, "wal_mode": "enabled"},
        )

    async def enqueue(
        self,
        text: str,
        collection: str = "articles",
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> str:
        """Adiciona um texto à fila de embedding para processamento posterior.

        Args:
            text: Conteúdo a ser transformado em embedding
            collection: Nome da coleção (articles, wiki, api_docs, etc)
            metadata: Metadados opcionais do documento
            job_id: Agrupa este chunk a uma operação de ingest (progresso por job)

        Returns:
            ID da fila para rastreamento
        """
        queue_id = str(uuid4())
        metadata_json = json.dumps(metadata or {})

        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                record = EmbeddingQueueRecord(
                    queue_id=queue_id,
                    job_id=job_id,
                    text=text,
                    collection=collection,
                    doc_metadata=metadata_json,
                    status=EmbeddingStatus.PENDING,
                )
                session.add(record)
                await session.commit()

            logger.info(
                "embedding_enqueued",
                extra={
                    "queue_id": queue_id,
                    "collection": collection,
                    "text_length": len(text),
                },
            )

            return queue_id

        except Exception:
            logger.exception(
                "embedding_queue_insert_failed",
                extra={"queue_id": queue_id},
            )
            raise

    async def get_pending(self, limit: int = 10) -> list[EmbeddingQueueRecord]:
        """Obtém documentos pendentes da fila.

        Args:
            limit: Máximo de registros a retornar

        Returns:
            Lista de registros pendentes da fila de embedding
        """
        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import and_, select

                query = select(EmbeddingQueueRecord).where(
                    and_(
                        EmbeddingQueueRecord.status == EmbeddingStatus.PENDING,
                        EmbeddingQueueRecord.attempt_count < 3,  # Max 3 retries
                    )
                )
                result = await session.execute(query)
                records = result.scalars().all()

                logger.debug(
                    "embedding_queue_get_pending",
                    extra={"count": len(records), "limit": limit},
                )

                return records[:limit]

        except Exception:
            logger.exception("embedding_queue_get_pending_failed")
            return []

    async def count_pending(self) -> int:
        """Retorna o total de registros ainda não processados (pending + processing).

        Usado pela UI para exibir o contador em tempo real no rodapé.

        Returns:
            Número de registros com status pending ou processing.
        """
        try:
            if self.AsyncSessionLocal is None:
                return 0
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import func, select

                query = select(func.count()).where(
                    EmbeddingQueueRecord.status.in_(
                        [EmbeddingStatus.PENDING, EmbeddingStatus.PROCESSING]
                    )
                )
                result = await session.execute(query)
                return result.scalar_one() or 0
        except Exception:
            return 0

    async def get_stats(self) -> dict[str, int]:
        """Retorna contagem de registros por status para o painel /rag.

        Returns:
            Dict com chaves pending, processing, success, failed, dlq — cada uma
            com o número de registros naquele status.
        """
        statuses = [s.value for s in EmbeddingStatus]
        result = dict.fromkeys(statuses, 0)
        try:
            if self.AsyncSessionLocal is None:
                return result
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import func, select

                query = select(
                    EmbeddingQueueRecord.status,
                    func.count().label("cnt"),
                ).group_by(EmbeddingQueueRecord.status)
                rows = await session.execute(query)
                for status, cnt in rows:
                    if status in result:
                        result[status] = cnt
        except Exception:
            pass
        return result

    async def get_job_stats(self, job_id: str) -> dict[str, int]:
        """Contagem por status dos chunks de um job de ingest (progresso).

        Returns:
            Dict com ``total`` e contagem por status (pending, processing,
            success, failed, dlq) restrita a ``job_id``.
        """
        statuses = [s.value for s in EmbeddingStatus]
        result = dict.fromkeys(statuses, 0)
        result["total"] = 0
        try:
            if self.AsyncSessionLocal is None:
                return result
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import func, select

                query = (
                    select(
                        EmbeddingQueueRecord.status,
                        func.count().label("cnt"),
                    )
                    .where(EmbeddingQueueRecord.job_id == job_id)
                    .group_by(EmbeddingQueueRecord.status)
                )
                rows = await session.execute(query)
                for status, cnt in rows:
                    if status in result:
                        result[status] = cnt
                    result["total"] += cnt
        except Exception:
            pass
        return result

    async def mark_processing(self, queue_id: str) -> None:
        """Marca um registro da fila como processando.

        Args:
            queue_id: ID do registro a marcar
        """
        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import update

                query = (
                    update(EmbeddingQueueRecord)
                    .where(EmbeddingQueueRecord.queue_id == queue_id)
                    .values(
                        status=EmbeddingStatus.PROCESSING,
                        attempt_count=EmbeddingQueueRecord.attempt_count + 1,
                    )
                )
                await session.execute(query)
                await session.commit()

        except Exception:
            logger.exception(
                "embedding_queue_mark_processing_failed",
                extra={"queue_id": queue_id},
            )

    async def mark_success(self, queue_id: str) -> None:
        """Marca um registro da fila como processado com sucesso.

        Args:
            queue_id: ID do registro a marcar
        """
        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import update

                query = (
                    update(EmbeddingQueueRecord)
                    .where(EmbeddingQueueRecord.queue_id == queue_id)
                    .values(
                        status=EmbeddingStatus.SUCCESS, processed_at=datetime.now(UTC)
                    )
                )
                await session.execute(query)
                await session.commit()

            logger.info("embedding_queue_marked_success", extra={"queue_id": queue_id})

        except Exception:
            logger.exception(
                "embedding_queue_mark_success_failed",
                extra={"queue_id": queue_id},
            )

    async def mark_failed(self, queue_id: str, error_message: str) -> None:
        """Marca um registro da fila como falha.

        Args:
            queue_id: ID do registro a marcar
            error_message: Mensagem de erro
        """
        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import update

                query = (
                    update(EmbeddingQueueRecord)
                    .where(EmbeddingQueueRecord.queue_id == queue_id)
                    .values(status=EmbeddingStatus.FAILED, error_message=error_message)
                )
                await session.execute(query)
                await session.commit()

            logger.error(
                "embedding_queue_marked_failed",
                extra={"queue_id": queue_id, "error": error_message},
            )

        except Exception:
            logger.exception(
                "embedding_queue_mark_failed_failed",
                extra={"queue_id": queue_id},
            )

    async def mark_dlq(self, queue_id: str, reason: str) -> None:
        """Move um registro para Dead Letter Queue (falha permanente).

        Usado após 3 tentativas falhadas de embedding. O registro é armazenado
        para auditoria manual, sem tentativas automáticas futuras.

        Args:
            queue_id: ID do registro a marcar
            reason: Razão para movimentação para DLQ
        """
        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import update

                query = (
                    update(EmbeddingQueueRecord)
                    .where(EmbeddingQueueRecord.queue_id == queue_id)
                    .values(status=EmbeddingStatus.DLQ, dlq_reason=reason)
                )
                await session.execute(query)
                await session.commit()

            logger.error(
                "embedding_queue_moved_to_dlq",
                extra={"queue_id": queue_id, "reason": reason},
            )

        except Exception:
            logger.exception(
                "embedding_queue_mark_dlq_failed",
                extra={"queue_id": queue_id},
            )

    async def archive_pending(self, reason: str) -> int:
        """Move todos os registros pendentes/em processamento para a DLQ.

        Usado pelo circuit breaker quando a API de embeddings está em rate limit:
        em vez de re-tentar cada chunk em loop (floodando o log e saturando o
        thread pool), arquiva a fila inteira de uma vez com um motivo único. Os
        registros ficam recuperáveis via ``retry_failed`` quando a chave estiver
        normalizada — e não são reprocessados automaticamente no próximo start.

        Args:
            reason: Motivo do arquivamento (exibido ao usuário).

        Returns:
            Número de registros arquivados.
        """
        try:
            if self.AsyncSessionLocal is None:
                return 0
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import update

                query = (
                    update(EmbeddingQueueRecord)
                    .where(
                        EmbeddingQueueRecord.status.in_(
                            [EmbeddingStatus.PENDING, EmbeddingStatus.PROCESSING]
                        )
                    )
                    .values(status=EmbeddingStatus.DLQ, dlq_reason=reason)
                )
                result = await session.execute(query)
                await session.commit()
                archived = result.rowcount or 0

            if archived:
                logger.warning(
                    "embedding_queue_archived",
                    extra={"archived": archived, "reason": reason},
                )
            return archived
        except Exception:
            logger.exception("embedding_queue_archive_pending_failed")
            return 0

    async def get_failed(self, limit: int = 10) -> list[EmbeddingQueueRecord]:
        """Obtém registros com falha (status='failed' ou 'dlq').

        Útil para monitoramento e auditoria de embeddings que falharam.

        Args:
            limit: Máximo de registros a retornar

        Returns:
            Lista de registros com falha
        """
        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import or_, select

                query = select(EmbeddingQueueRecord).where(
                    or_(
                        EmbeddingQueueRecord.status == EmbeddingStatus.FAILED,
                        EmbeddingQueueRecord.status == EmbeddingStatus.DLQ,
                    )
                )
                result = await session.execute(query)
                records = result.scalars().all()

                logger.debug(
                    "embedding_queue_get_failed",
                    extra={"count": len(records), "limit": limit},
                )

                return records[:limit]

        except Exception:
            logger.exception("embedding_queue_get_failed_failed")
            return []

    async def reconcile(self) -> None:
        """Recupera registros travados em 'processing' que não terminaram.

        Caso de uso: A aplicação crashou enquanto processava um embedding.
        Ao reiniciar, este método move registros com status='processing'
        e updated_at >2 minutos atrás de volta para 'pending' para retry.

        Isso garante que trabalhos incompletos não fiquem travados forever.
        """
        try:
            if self.AsyncSessionLocal is None:
                msg = "AsyncSessionLocal não foi inicializado"
                raise RuntimeError(msg)
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import and_, select, update

                # Encontrar registros em processing há mais de 2 minutos
                cutoff_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(
                    minutes=2
                )

                # Primeiro, contar quantos serão recuperados
                count_query = select(EmbeddingQueueRecord).where(
                    and_(
                        EmbeddingQueueRecord.status == EmbeddingStatus.PROCESSING,
                        EmbeddingQueueRecord.updated_at < cutoff_time,
                    )
                )
                result = await session.execute(count_query)
                stalled_records = result.scalars().all()
                stalled_count = len(stalled_records)

                if stalled_count > 0:
                    # Mover de volta para pending
                    update_query = (
                        update(EmbeddingQueueRecord)
                        .where(
                            and_(
                                EmbeddingQueueRecord.status
                                == EmbeddingStatus.PROCESSING,
                                EmbeddingQueueRecord.updated_at < cutoff_time,
                            )
                        )
                        .values(status=EmbeddingStatus.PENDING)
                    )
                    await session.execute(update_query)
                    await session.commit()

                    logger.warning(
                        "embedding_queue_reconciled",
                        extra={"recovered_count": stalled_count},
                    )
                else:
                    logger.debug("embedding_queue_reconciliation_complete")

        except Exception:
            logger.exception("embedding_queue_reconcile_failed")

    async def cleanup_old_records(self, days: int = 30) -> int:
        """Remove registros antigos de DLQ e failed do banco de dados.

        Evita crescimento indefinido de registros de falha. Deve ser chamado
        periodicamente (ex: startup do worker, ou manualmente via /rag cleanup).

        Args:
            days: Deletar registros mais antigos que N dias (default 30)

        Returns:
            Número de registros deletados
        """
        try:
            if self.AsyncSessionLocal is None:
                return 0
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import delete

                cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
                query = delete(EmbeddingQueueRecord).where(
                    EmbeddingQueueRecord.status.in_(
                        [EmbeddingStatus.DLQ, EmbeddingStatus.FAILED]
                    )
                    & (EmbeddingQueueRecord.created_at < cutoff)
                )
                result = await session.execute(query)
                await session.commit()
                deleted = result.rowcount or 0

                if deleted:
                    logger.info(
                        "embedding_queue_cleanup",
                        extra={"deleted": deleted, "older_than_days": days},
                    )
                return deleted
        except Exception:
            logger.exception("embedding_queue_cleanup_failed")
            return 0

    async def retry_failed(self, limit: int = 100) -> int:
        """Move itens failed/DLQ de volta para pending para nova tentativa.

        Permite ao operador reprocessar embeddings que falharam sem precisar
        re-enfileirar os documentos originais. Os contadores de tentativa e
        mensagens de erro são preservados para auditoria.

        Args:
            limit: Número máximo de itens a mover para pending (default 100)

        Returns:
            Número de registros movidos para pending
        """
        try:
            if self.AsyncSessionLocal is None:
                return 0
            async with self.AsyncSessionLocal() as session:
                from sqlalchemy import or_, select, update

                # Buscar IDs dos itens a reprocessar (até limit)
                select_query = (
                    select(EmbeddingQueueRecord.queue_id)
                    .where(
                        or_(
                            EmbeddingQueueRecord.status == EmbeddingStatus.FAILED,
                            EmbeddingQueueRecord.status == EmbeddingStatus.DLQ,
                        )
                    )
                    .limit(limit)
                )
                id_result = await session.execute(select_query)
                ids = [row[0] for row in id_result.all()]

                if not ids:
                    return 0

                update_query = (
                    update(EmbeddingQueueRecord)
                    .where(EmbeddingQueueRecord.queue_id.in_(ids))
                    .values(status=EmbeddingStatus.PENDING)
                )
                await session.execute(update_query)
                await session.commit()

                retried = len(ids)
                logger.info(
                    "embedding_queue_retry_failed",
                    extra={"retried": retried},
                )
                return retried

        except Exception:
            logger.exception("embedding_queue_retry_failed_error")
            return 0

    async def close(self) -> None:
        """Fecha a conexão do banco de dados."""
        if self.engine:
            await self.engine.dispose()
            logger.info("embedding_queue_closed")


# Instância singleton global com lock para evitar race condition em async
_queue: EmbeddingQueue | None = None
_queue_lock: asyncio.Lock = asyncio.Lock()


async def get_embedding_queue(db_url: str | None) -> EmbeddingQueue:
    """Obtém ou cria instância global da fila de embedding de forma thread-safe.

    O `asyncio.Lock` garante que apenas uma coroutine inicializa `_queue`,
    eliminando a race condition quando chamadas simultâneas chegam antes da
    primeira inicialização completar.

    Args:
        db_url: URL de conexão SQLAlchemy. Obrigatório — levanta ValueError se None.
    """
    if db_url is None:
        msg = "embedding_queue_dsn não configurado. Verifique as settings."
        raise ValueError(msg)

    global _queue
    if _queue is not None:
        return _queue
    async with _queue_lock:
        # Double-check após adquirir o lock
        if _queue is None:
            _queue = EmbeddingQueue(db_url)
            await _queue.init()
    return _queue


# ---------------------------------------------------------------------------
# Backend Postgres — QueueDB protocol (F7 — complete mode)
# ---------------------------------------------------------------------------


class PostgresQueueDB:
    """Implementação Postgres do protocolo ``QueueDB``.

    Gere a tabela ``vectora_embedding_queue`` usando asyncpg com
    ``SELECT … FOR UPDATE SKIP LOCKED`` para dequeue concorrente seguro.
    F8 adicionará o multi-worker completo com retry e DLQ.

    Schema (Postgres — criado pela migration correspondente):

        CREATE TABLE IF NOT EXISTS vectora_embedding_queue (
            id           BIGSERIAL PRIMARY KEY,
            queue_id     TEXT        NOT NULL UNIQUE,
            task_json    JSONB       NOT NULL,
            status       TEXT        NOT NULL DEFAULT 'pending',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ
        );
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS vectora_embedding_queue (
            id           BIGSERIAL    PRIMARY KEY,
            queue_id     TEXT         NOT NULL UNIQUE,
            task_json    JSONB        NOT NULL,
            status       TEXT         NOT NULL DEFAULT 'pending',
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_veq_status
            ON vectora_embedding_queue (status);
    """

    async def _ensure_table(self) -> None:
        """Cria a tabela se não existir (idempotente)."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(self._CREATE_TABLE)

    async def health(self) -> dict[str, object]:
        """Verifica se a conexão Postgres está acessível."""
        try:
            from backend.storage.factory import get_pg_pool

            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return {"ok": True, "error": None}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def enqueue(self, task: dict[str, Any]) -> str:
        """Enfileira uma tarefa e retorna o ``queue_id``."""
        import json
        import uuid

        from backend.storage.factory import get_pg_pool

        queue_id = task.get("queue_id") or str(uuid.uuid4())
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO vectora_embedding_queue (queue_id, task_json)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (queue_id) DO NOTHING
                """,
                queue_id,
                json.dumps(task),
            )
        return queue_id

    async def dequeue(self, limit: int = 1) -> list[dict[str, Any]]:
        """Retira até ``limit`` tarefas pending com SKIP LOCKED (sem bloqueio).

        Marca as tarefas retiradas como ``processing``.
        """
        import json

        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id, queue_id, task_json
                    FROM vectora_embedding_queue
                    WHERE status = 'pending'
                    ORDER BY id
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    limit,
                )
                if not rows:
                    return []
                ids = [r["id"] for r in rows]
                await conn.execute(
                    """
                    UPDATE vectora_embedding_queue
                    SET status = 'processing'
                    WHERE id = ANY($1::bigint[])
                    """,
                    ids,
                )

        result = []
        for row in rows:
            task = json.loads(row["task_json"])
            task["queue_id"] = row["queue_id"]
            result.append(task)
        return result

    async def ack(self, queue_id: str) -> None:
        """Marca a tarefa como concluída com sucesso."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE vectora_embedding_queue
                SET status = 'success', processed_at = now()
                WHERE queue_id = $1
                """,
                queue_id,
            )

    async def nack(self, queue_id: str, error: str = "") -> None:
        """Marca a tarefa como falha (voltará para reprocessamento em F8)."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE vectora_embedding_queue
                SET status = 'failed', processed_at = now()
                WHERE queue_id = $1
                """,
                queue_id,
            )

    # ------------------------------------------------------------------
    # Compatibilidade com a interface do BackgroundEmbeddingWorker (F8)
    # ------------------------------------------------------------------

    async def get_pending(self, limit: int = 10) -> list[Any]:
        """Retorna até ``limit`` tarefas via SKIP LOCKED, marcando como 'processing'.

        Retorna objetos ``SimpleNamespace`` com os mesmos atributos de
        ``EmbeddingQueueRecord`` para compatibilidade com o worker existente.
        """
        import types

        tasks = await self.dequeue(limit)
        result = []
        for t in tasks:
            ns = types.SimpleNamespace(
                queue_id=t.get("queue_id", ""),
                text=t.get("text", ""),
                collection=t.get("collection", ""),
                doc_metadata=t.get("doc_metadata"),
                attempt_count=t.get("attempt_count", 0),
                status="processing",
            )
            result.append(ns)
        return result

    async def mark_processing(self, queue_id: str) -> None:
        """No-op: SKIP LOCKED já marca como 'processing' no dequeue."""

    async def mark_success(self, queue_id: str) -> None:
        """Alias de ``ack`` para compatibilidade com o worker."""
        await self.ack(queue_id)

    async def mark_failed(
        self, queue_id: str, error: str = "", move_to_dlq: bool = False
    ) -> None:
        """Alias de ``nack`` para compatibilidade com o worker."""
        await self.nack(queue_id, error)

    async def move_to_dlq(self, queue_id: str, reason: str = "") -> None:
        """Marca a tarefa como DLQ (dead-letter)."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE vectora_embedding_queue
                SET status = 'dlq', processed_at = now()
                WHERE queue_id = $1
                """,
                queue_id,
            )

    async def reconcile(self) -> None:
        """Reset de tarefas presas em 'processing' por mais de 5 minutos."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE vectora_embedding_queue
                SET status = 'pending'
                WHERE status = 'processing'
                  AND created_at < now() - INTERVAL '5 minutes'
                """
            )

    async def archive_pending(self, reason: str) -> int:
        """Move tarefas pending/processing para DLQ (circuit breaker de rate limit)."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE vectora_embedding_queue
                SET status = 'dlq', processed_at = now()
                WHERE status IN ('pending', 'processing')
                """
            )
        # asyncpg retorna "UPDATE <n>" — extrai o n.
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0
