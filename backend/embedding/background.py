"""Background Worker para Processamento de Embeddings Fire-and-Forget.

Loop assíncrono que:
1. Busca documentos pendentes da fila de embedding a cada 5 segundos
2. Processa até 10 documentos em paralelo (limitado por Semaphore(5))
3. Gera embeddings via `_build_lc_embeddings()` (Cohere↔Voyage↔Ollama↔OpenRouter)
4. Escreve no vector store nativo com idempotência (via queue_id como id do ponto)
5. Retry com exponential backoff (1s → 2s → 4s) até 3 tentativas
6. Move para DLQ após 3 falhas para auditoria manual
"""

import asyncio
import contextlib
import logging
import time
import traceback
from datetime import datetime
from typing import Any

from backend.embedding.queue import EmbeddingQueueRecord, get_embedding_queue
from backend.settings import settings

logger = logging.getLogger(__name__)


class CohereRateLimiter:
    """Token bucket rate limiter para chamadas à API Cohere.

    Garante que o BackgroundEmbeddingWorker nunca ultrapasse o limite de
    chamadas por minuto configurado — especialmente crítico para chaves trial
    (100 calls/min), onde bursts de 5 requests paralelos disparam HTTP 429.

    Algoritmo token bucket:
    - Capacidade = `calls_per_minute` tokens
    - Tokens se regeneram na taxa de 1 por `min_interval` segundos
    - `acquire()` bloqueia assincronamente até um token ficar disponível
    - Cada chamada registra o tempo de espera para métricas via /rag

    Por que token bucket (e não sleep fixo)?
    - Permite bursts curtos (se a fila estava vazia e tokens acumularam)
    - Mas limita a taxa média ao contrato da API ao longo do tempo
    - Não desperdiça capacidade em períodos de baixa demanda
    """

    def __init__(self, calls_per_minute: int) -> None:
        """Inicializa o rate limiter.

        Args:
            calls_per_minute: Limite de chamadas por minuto (ex: 90 para trial)
        """
        self.calls_per_minute = max(1, calls_per_minute)
        # Intervalo mínimo entre chamadas para respeitar o limite
        self.min_interval: float = 60.0 / self.calls_per_minute
        self._lock = asyncio.Lock()
        self._last_call_time: float = 0.0
        # Métricas expostas para o painel /rag
        self.throttle_count: int = 0
        self.total_throttle_seconds: float = 0.0

    async def acquire(self) -> float:
        """Aguarda até que seja seguro fazer mais uma chamada à API.

        Returns:
            Segundos de espera (0.0 se não houve throttle)
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_time
            wait = self.min_interval - elapsed

            if wait > 0:
                self.throttle_count += 1
                self.total_throttle_seconds += wait
                logger.debug(
                    "cohere_rate_limiter_throttle: wait=%.2fs calls_per_min=%d",
                    wait,
                    self.calls_per_minute,
                )
                await asyncio.sleep(wait)

            self._last_call_time = time.monotonic()
            return max(0.0, wait)

    @property
    def avg_wait_s(self) -> float:
        """Tempo médio de espera por throttle (segundos)."""
        if self.throttle_count == 0:
            return 0.0
        return self.total_throttle_seconds / self.throttle_count

    @property
    def effective_rate(self) -> float:
        """Taxa efetiva configurada (calls/min)."""
        return self.calls_per_minute


# Tempos de backoff exponencial para retry de embeddings (segundos)
RETRY_BACKOFF = [1, 2, 4]  # 1s → 2s → 4s
MAX_RETRIES = 3
MAX_PARALLEL = 5  # Max 5 embeddings simultâneos (Semaphore)
BATCH_SIZE = 10  # Processa até 10 registros por batch


def _is_rate_limit_error(exc: Exception) -> bool:
    """``True`` para erros de rate limit / quota da API de embeddings.

    Estes não são transitórios no curto prazo (chave Trial no limite por
    minuto/mês): re-tentar cada chunk só flooda o log e satura o thread pool.
    Disparam o circuit breaker em vez do retry normal.
    """
    text = str(exc).lower()
    return (
        "429" in text
        or "too many requests" in text
        or "toomanyrequests" in text
        or "rate limit" in text
        or "ratelimit" in text
        or "trial key" in text
        or "quota" in text
    )


# Polling adaptativo: ao invés de POLLING_INTERVAL fixo, o worker
# aumenta o intervalo exponencialmente quando a fila está vazia
# e reseta ao encontrar items (evita queries desnecessárias em idle).
POLL_INTERVAL_MIN = 5  # Intervalo mínimo quando há items (segundos)
POLL_INTERVAL_MAX = 30  # Intervalo máximo em idle (segundos)
POLL_BACKOFF_FACTOR = 2  # Multiplicador a cada ciclo vazio


class BackgroundEmbeddingWorker:
    """Worker assíncrono para processamento de embeddings em larga escala."""

    def __init__(self) -> None:
        """Inicializa o worker com configuração global."""
        self.config = settings
        self.running = False
        self.task: asyncio.Task[None] | None = None
        self.semaphore = asyncio.Semaphore(MAX_PARALLEL)
        # Semaphore(1) para serializar escritas no vector store — o backend
        # LanceDB não suporta múltiplas escritas simultâneas no mesmo diretório
        # (Qdrant suportaria concorrência real, mas mantemos serializado pra
        # não duplicar lógica de exclusão mútua por backend).
        self._write_semaphore = asyncio.Semaphore(1)
        # Contadores em memória para o painel /rag.
        # Não acumulam indefinidamente: resetados a cada startup do worker.
        self.processed_count: int = 0
        self.failed_count: int = 0
        # Rate limit tracking — alimentado por `_trip_rate_limit_breaker`.
        # rate_limit_active=True enquanto o último embedding retornou 429;
        # resetado para False após o primeiro embedding bem-sucedido.
        self.rate_limit_active: bool = False
        self.rate_limit_count: int = 0  # Total de 429s nesta sessão
        self.last_rate_limit_at: datetime | None = None
        # Circuit breaker: ao detectar rate limit/quota, o worker pausa e
        # arquiva a fila em vez de re-tentar cada chunk em loop (que floodava o
        # log e saturava o thread pool, travando o backend). pause_reason é
        # exibido ao usuário via status dos jobs RAG.
        self.paused: bool = False
        self.pause_reason: str | None = None
        self.paused_at: datetime | None = None
        # Token bucket rate limiter — evita bursts que disparam HTTP 429.
        # Inicializado com o valor de settings (configurável; default 90/min).
        self._rate_limiter = CohereRateLimiter(self.config.cohere_calls_per_minute)
        # B4 curator: rastreia o momento do último doc indexado com sucesso e
        # os workspace_ids dos docs indexados no batch atual.
        self._last_indexed_at: float = 0.0
        self._indexed_workspaces: set[str] = set()
        self._curator_locks: dict[str, asyncio.Lock] = {}
        # Polling adaptativo: _poll_interval cresce enquanto fila está vazia
        # e é resetado ao encontrar items (evita 12+ queries/min em idle).
        self._poll_interval: float = POLL_INTERVAL_MIN
        self._queue: Any = None
        # Backoff exponencial + dedup de log para erros persistentes no loop
        # principal (ex: storage_mode inconsistente com a licença efetiva).
        self._consecutive_errors: int = 0
        self._last_error_type: type[BaseException] | None = None

    async def _get_queue(self) -> Any:
        """Obtém (e inicializa uma vez) a queue de acordo com storage_mode.

        Usa ``get_effective_storage_mode()`` (não ``self.config.storage_mode``
        cru) porque o modo configurado pode ser "complete" enquanto a licença
        efetiva rebaixa para "lite" — sem isso o worker tenta abrir pool
        Postgres, `require_pro()` levanta 402, e como o loop nunca cacheia
        esse resultado negativo, o erro se repete a cada ciclo.
        """
        if self._queue is not None:
            return self._queue
        from backend.services.license import get_effective_storage_mode

        effective_mode = get_effective_storage_mode()
        if effective_mode == "complete" and self.config.postgres_dsn:
            from backend.embedding.queue import PostgresQueueDB

            q = PostgresQueueDB()
            await q._ensure_table()
            self._queue = q
        else:
            self._queue = await get_embedding_queue(self.config.embedding_queue_dsn)
        return self._queue

    async def start(self) -> None:
        """Inicia o worker como asyncio.Task."""
        if self.running:
            logger.warning("Worker já está rodando")
            return

        self.running = True

        # Executar reconciliação na startup (recuperar records travados)
        await self._reconcile_startup()

        self.task = asyncio.create_task(self._run_loop())
        logger.info("BackgroundEmbeddingWorker iniciado")

    async def stop(self, timeout_seconds: int = 30) -> None:
        """Para o worker gracefully.

        Args:
            timeout_seconds: Segundos para aguardar a terminação
        """
        if not self.running:
            return

        logger.info("Parando BackgroundEmbeddingWorker...")
        self.running = False

        if self.task:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self.task
            except TimeoutError:
                logger.warning(
                    "Worker não terminou a tempo, cancelando",
                    extra={"timeout_seconds": timeout_seconds},
                )
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.task

        logger.info("BackgroundEmbeddingWorker parou")

    async def _reconcile_startup(self) -> None:
        """Recupera records travados em 'processing' na startup."""
        try:
            queue = await self._get_queue()
            await queue.reconcile()
            logger.info("Reconciliação de startup concluída")
        except Exception:
            logger.exception("Erro ao reconciliar startup")

    async def _run_loop(self) -> None:
        """Loop principal: fetch pending → process → retry/success/dlq."""
        while self.running:
            try:
                # Circuit breaker ativo: a fila foi arquivada por rate limit.
                # Não busca novos pendentes (evita re-storm); fica idle até o
                # processo reiniciar ou alguém retomar via /rag.
                if self.paused:
                    await asyncio.sleep(POLL_INTERVAL_MAX)
                    continue

                # Obter queue (lazy-loaded do singleton) — chegar aqui sem
                # exceção já é sinal de recuperação, reseta o backoff.
                queue = await self._get_queue()
                self._consecutive_errors = 0
                self._last_error_type = None

                # Buscar até BATCH_SIZE documentos pendentes
                pending = await queue.get_pending(limit=BATCH_SIZE)

                if not pending:
                    # Fila vazia: backoff adaptativo até POLL_INTERVAL_MAX.
                    # Evita ~720 queries/hora em idle quando não há embeddings.
                    logger.debug(
                        "embedding_queue_empty, aguardando %.0fs",
                        self._poll_interval,
                    )
                    # B4: dispara o curator se houver workspaces novamente indexados
                    # e o debounce de 30s tiver passado.
                    await self._maybe_run_curator()
                    await asyncio.sleep(self._poll_interval)
                    self._poll_interval = min(
                        self._poll_interval * POLL_BACKOFF_FACTOR, POLL_INTERVAL_MAX
                    )
                    continue

                # Items encontrados: reset do intervalo de polling
                self._poll_interval = POLL_INTERVAL_MIN
                logger.debug("Batch encontrado", extra={"count": len(pending)})

                # Processar batch em paralelo (limitado por Semaphore)
                await asyncio.gather(
                    *[self._process_record(record, queue) for record in pending],
                    return_exceptions=True,
                )

                # Pequena pausa antes do próximo batch
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info("Worker foi cancelado")
                break
            except Exception as exc:
                # Backoff exponencial com log único por causa de erro (não o
                # traceback completo a cada ciclo) — evita o loop de spam que
                # aconteceria se _get_queue()/get_pending() falharem de forma
                # persistente (ex: config inconsistente entre storage_mode e
                # DSN configurado).
                self._consecutive_errors += 1
                backoff = min(
                    POLL_INTERVAL_MIN * (2**self._consecutive_errors),
                    POLL_INTERVAL_MAX,
                )
                if self._consecutive_errors <= 1 or self._last_error_type is not type(
                    exc
                ):
                    logger.exception(
                        "Erro no loop principal do worker (tentativa %d, backoff %.0fs)",
                        self._consecutive_errors,
                        backoff,
                    )
                else:
                    logger.warning(
                        "embedding_worker_error_repetido tipo=%s tentativa=%d backoff=%.0fs",
                        type(exc).__name__,
                        self._consecutive_errors,
                        backoff,
                    )
                self._last_error_type = type(exc)
                await asyncio.sleep(backoff)
                continue

    async def _process_record(
        self, record: EmbeddingQueueRecord, queue: Any | None = None
    ) -> None:
        """Processa um registro individual com retry exponencial.

        Args:
            record: Registro da fila de embedding
            queue: Fila de embedding (obtém do singleton se None)
        """
        if queue is None:
            queue = await self._get_queue()

        queue_id = record.queue_id
        attempt = 0

        while attempt < MAX_RETRIES:
            # Outra task do batch pode ter disparado o breaker enquanto este
            # registro esperava o semáforo — não faz mais nenhuma chamada.
            if self.paused:
                return
            try:
                async with self.semaphore:
                    # Marcar como processing
                    await queue.mark_processing(queue_id)

                    # Respeitar limite de chamadas Cohere (token bucket)
                    # Trial keys: 100 calls/min → configurado em 90 por segurança.
                    # acquire() retorna imediatamente se dentro do limite,
                    # ou aguarda async sem bloquear o event loop.
                    await self._rate_limiter.acquire()

                    # Gerar embedding via Cohere
                    embedding_vector = await self._generate_embedding(str(record.text))

                    # Escrever em LanceDB (idempotente via queue_id)
                    await self._write_to_vector_store(record, embedding_vector)

                    # Marcar como success
                    await queue.mark_success(queue_id)
                    self.processed_count += 1
                    # Embedding bem-sucedido: limpa flag de rate limit
                    self.rate_limit_active = False
                    if record.job_id:
                        from backend.api.handlers.workspaces import (
                            _maybe_emit_job_event,
                        )

                        await _maybe_emit_job_event(str(record.job_id))

                    logger.info(
                        "embedding_processed_success",
                        extra={
                            "queue_id": queue_id,
                            "collection": record.collection,
                        },
                    )
                    return  # Sucesso, sair do loop de retry

            except Exception as e:
                # Rate limit / quota: não é transitório no curto prazo. Dispara
                # o circuit breaker (pausa o worker + arquiva a fila) em vez de
                # re-tentar este e todos os outros chunks em loop.
                if _is_rate_limit_error(e):
                    await self._trip_rate_limit_breaker(queue)
                    return

                attempt += 1
                error_trace = traceback.format_exc()
                # Mensagem enxuta no terminal — o erro cru da Cohere despeja
                # headers HTTP inteiros e polui o log. Stack completo fica no
                # `extra` (logging estruturado), não na mensagem.
                _msg = str(e)[:300]
                logger.warning(
                    "embedding_processing_failed [%d/%d]: %s",
                    attempt,
                    MAX_RETRIES,
                    _msg,
                    extra={"queue_id": queue_id, "traceback": error_trace},
                )

                if attempt < MAX_RETRIES:
                    # Exponential backoff antes de retry
                    backoff_time = RETRY_BACKOFF[attempt - 1]
                    logger.info(
                        "embedding_retry_backoff",
                        extra={
                            "queue_id": queue_id,
                            "backoff_seconds": backoff_time,
                            "attempt": attempt,
                        },
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    # 3 falhas, mover para DLQ com stack trace completo
                    self.failed_count += 1
                    dlq_reason = f"{e!s}\n\nStack trace:\n{error_trace}"
                    try:
                        await queue.mark_dlq(queue_id, dlq_reason)
                    except Exception:
                        logger.exception(
                            "Erro ao mover para DLQ",
                            extra={"queue_id": queue_id},
                        )
                    else:
                        logger.info(
                            "embedding_moved_to_dlq",
                            extra={
                                "queue_id": queue_id,
                                "reason": str(e),
                            },
                        )
                        if record.job_id:
                            from backend.api.handlers.workspaces import (
                                _maybe_emit_job_event,
                            )

                            await _maybe_emit_job_event(str(record.job_id))

    async def _trip_rate_limit_breaker(self, queue: Any) -> None:
        """Pausa o worker e arquiva a fila ao detectar rate limit / quota.

        Idempotente entre as tasks paralelas do mesmo batch: a primeira a
        detectar arquiva a fila; as demais apenas retornam. A fila inteira vai
        para a DLQ com um motivo único (não re-tentada no próximo start),
        recuperável via ``retry_failed`` quando a chave estiver normalizada.
        """
        self.rate_limit_active = True
        self.rate_limit_count += 1
        self.last_rate_limit_at = datetime.now()

        if self.paused:
            return

        self.paused = True
        self.paused_at = datetime.now()
        self.pause_reason = (
            "Indexação RAG pausada: a API de embeddings (Cohere) retornou rate "
            "limit (429). A chave Trial tem limite de 100 chamadas/min e "
            "1000/mês. Configure uma chave paga ou aguarde a renovação do "
            "limite e reprocesse a fila."
        )

        archived = await queue.archive_pending(self.pause_reason)
        logger.warning(
            "embedding_worker_paused_rate_limit: %d chunks arquivados",
            archived,
            extra={"reason": self.pause_reason, "archived": archived},
        )

    async def _generate_embedding(self, text: str) -> list[float]:
        """Gera embedding via `_build_lc_embeddings()` — honra o fallback
        multi-provider (Cohere↔Voyage↔Ollama↔OpenRouter) e a preferência de
        runtime (`rag_settings.embed_provider`), em vez de Cohere hardcoded.

        Returns:
            Lista de floats representando o embedding

        Raises:
            ValueError: Se nenhum provider de embedding estiver configurado
        """
        from backend.storage.factory import _build_lc_embeddings

        embeddings_model = _build_lc_embeddings()
        if embeddings_model is None:
            msg = "Nenhum provider de embedding configurado"
            raise ValueError(msg)

        # Indexação usa embed_documents → input_type="search_document".
        # O Cohere v3 é assimétrico: documentos indexados e queries de busca
        # vivem em espaços de embedding distintos. A query (vector_search em
        # rag.py) usa embed_query → input_type="search_query". Indexar com
        # embed_query — como era feito antes — degrada o recall porque grava
        # o documento no espaço errado.
        # Os clients nativos (Cohere/Voyage/Ollama/OpenRouter) são async por
        # baixo (httpx.AsyncClient) — sem retry interno de 429 com backoff
        # longo travando a thread do to_thread; aembed_documents propaga o
        # erro na hora e o circuit breaker do worker assume.
        vectors = await embeddings_model.aembed_documents([text])
        return vectors[0]

    async def _write_to_vector_store(
        self, record: EmbeddingQueueRecord, vector: list[float]
    ) -> None:
        """Escreve documento no vector store nativo — LanceDB (lite) ou
        Qdrant (complete), conforme `storage_mode` — idempotente via
        `queue_id` como id do ponto/linha.

        Args:
            record: Registro com metadata
            vector: Embedding vector
        """
        import json as _json

        from backend.storage.factory import (
            _check_embedding_dimension,
            get_vector_store_backend,
        )
        from backend.storage.vectorstore.base import VectorRow

        # Guard de dimensão: troca de embedding_provider sem reindex quebraria
        # similarity search silenciosamente (vetores de dimensões diferentes
        # na mesma coleção) — levanta antes de escrever, não depois.
        await _check_embedding_dimension(
            str(record.collection), len(vector), provider="cohere"
        )

        meta = _json.loads(str(record.doc_metadata or "{}"))
        row = VectorRow(
            id=str(record.queue_id),
            vector=vector,
            text=str(record.text),
            metadata=meta if isinstance(meta, dict) else {},
        )

        backend = await get_vector_store_backend()
        # Serializa escritas na mesma coleção: LanceDBBackend faz
        # open-or-create sob a mesma corrida que existia aqui antes — várias
        # tasks processando o mesmo collection podiam colidir em
        # "Table already exists" sem essa exclusão mútua.
        async with self._write_semaphore:
            await backend.upsert(str(record.collection), [row])

        logger.debug(
            "vector_store_document_written",
            extra={
                "queue_id": record.queue_id,
                "collection": record.collection,
            },
        )

        # B4: rastreia workspace_id dos docs indexados para o curator
        wid = meta.get("workspace_id") if isinstance(meta, dict) else None
        if wid:
            self._indexed_workspaces.add(wid)
        self._last_indexed_at = time.monotonic()

    async def _maybe_run_curator(self) -> None:
        """Dispara o curator para workspaces com docs recém-indexados (B4).

        Condições para disparo:
        1. rag_curator_enabled = True
        2. Há workspaces pendentes de curadoria
        3. Debounce: último doc foi indexado há ≥ rag_curator_debounce_seconds

        Single flush por batch — ingestar 1000 arquivos = 1 LLM call de síntese.
        """
        if not getattr(self.config, "rag_curator_enabled", True):
            return
        if not self._indexed_workspaces:
            return

        debounce = getattr(self.config, "rag_curator_debounce_seconds", 30.0)
        if time.monotonic() - self._last_indexed_at < debounce:
            return

        # Snapshot e limpeza para o próximo batch
        workspaces_to_curate = set(self._indexed_workspaces)
        self._indexed_workspaces.clear()

        for workspace_id in workspaces_to_curate:
            asyncio.create_task(self._run_curator(workspace_id))  # noqa: RUF006

    async def _run_curator(self, workspace_id: str) -> None:
        """Executa a curadoria para um workspace específico (reentrância protegida)."""
        if workspace_id not in self._curator_locks:
            self._curator_locks[workspace_id] = asyncio.Lock()

        lock = self._curator_locks[workspace_id]
        if lock.locked():
            logger.debug("curator: workspace %s já em curadoria, pulando", workspace_id)
            return

        async with lock:
            try:
                from backend.embedding.curator import curate_workspace_knowledge

                logger.info(
                    "curator: iniciando curadoria do workspace %s", workspace_id
                )
                result = await curate_workspace_knowledge(workspace_id)
                logger.info("curator: %s — %s", workspace_id, result)
            except Exception:
                logger.exception(
                    "curator: falha na curadoria do workspace %s", workspace_id
                )


# Singleton global com lock
_worker: BackgroundEmbeddingWorker | None = None
_worker_lock: asyncio.Lock = asyncio.Lock()


def get_worker_pause_state() -> tuple[bool, str | None]:
    """Estado do circuit breaker do worker (lido sem instanciar o worker).

    Returns:
        ``(paused, reason)`` — ``paused`` True quando a fila foi arquivada por
        rate limit; ``reason`` é a mensagem a exibir ao usuário (ou None).
    """
    if _worker is None:
        return False, None
    return _worker.paused, _worker.pause_reason


async def get_background_worker() -> BackgroundEmbeddingWorker:
    """Obtém ou cria instância singleton do worker (thread-safe).

    Returns:
        Instância do BackgroundEmbeddingWorker

    Note:
        Usa asyncio.Lock para evitar race condition em múltiplas
        inicializações simultâneas.
    """
    global _worker

    if _worker is not None:
        return _worker

    async with _worker_lock:
        # Double-check após adquirir lock
        if _worker is None:
            _worker = BackgroundEmbeddingWorker()

    return _worker
