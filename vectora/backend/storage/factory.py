"""Factories singleton para backends de storage do Vectora.

Cada ``get_*()`` retorna sempre a mesma instância por processo (singleton
lazy). A impl concreta é escolhida por ``settings.storage_mode``:
    "lite"     — SQLite + LanceDB (default)
    "complete" — Postgres + Qdrant + Redis (Pro)

Uso:
    from backend.storage.factory import get_store, get_vector_store_backend

    store = await get_store()   # VectoraStore (sempre — não lê storage_mode;
                                 # o store Postgres real é aberto por
                                 # backend.services.agent_factory._ensure_infra)
    backend = await get_vector_store_backend()  # LanceDBBackend ou QdrantBackend

O agente real (motor nativo) vive em ``backend.services.agent_factory``.

Garantia de produto — usuários/auth/settings/config NUNCA em Postgres:
``storage_mode`` ("lite"/"complete") afeta apenas o store de memórias/skills
(protocolo ``StoreBackend`` em ``backend/storage/protocols.py``), vector
store e cache. Usuários, sessões, audit, invites (``backend/rbac/auth.py``)
e configurações (``runtime_settings``, ``~/.vectora/config.toml``) sempre
vivem em SQLite/JSON/TOML, mesmo com ``storage_mode == "complete"`` —
funcionam como fallback garantido independente do banco principal.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.storage.protocols import StoreBackend
    from backend.storage.vectorstore.base import VectorStoreHealth

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singletons por processo
# ---------------------------------------------------------------------------

_store: StoreBackend | None = None  # sempre VectoraStore — ver get_store()
_pg_pool: Any = None  # asyncpg.Pool (complete mode only)
_vector_stores: dict[str, Any] = {}  # collection → lancedb.AsyncTable (raw)
_optimize_tasks: dict[str, Any] = {}  # collection → asyncio.Task (schedule_optimize)
_vector_store_backend: Any = None  # VectorStoreBackend nativo — 1 por processo

# ---------------------------------------------------------------------------
# Store (memórias/skills — wrap do build_store existente)
# ---------------------------------------------------------------------------


async def get_store(embedding_model: str | None = None) -> StoreBackend:
    """Retorna (ou cria) o store singleton (protocolo ``StoreBackend``).

    Wrap fino sobre ``backend.llm.backends.build_store()`` — sempre
    ``VectoraStore`` (aiosqlite), independente de ``storage_mode``; não
    existe caminho Postgres aqui (esse vive em
    ``backend.services.agent_factory._ensure_infra``, usado pelo motor de
    conversa nativo).

    Returns:
        ``VectoraStore`` com índice de embeddings opcional (fallback
        Cohere↔Voyage↔Ollama↔OpenRouter), já inicializado (``setup()``
        chamado).
    """
    global _store
    store = _store
    if store is None:
        from backend.llm.backends import build_store

        store = await build_store(embedding_model)
        _store = store
        logger.debug("storage/factory: store criado (%s)", type(store).__name__)
    return store


# ---------------------------------------------------------------------------
# VectorStore nativo — LanceDBBackend (lite) · QdrantBackend (complete)
# ---------------------------------------------------------------------------


async def get_vector_store_backend() -> Any:
    """Retorna (ou cria) o ``VectorStoreBackend`` singleton do processo.

    Ponto único de roteamento: ``storage_mode == "lite"`` devolve
    ``LanceDBBackend`` (arquivo local); ``"complete"`` devolve
    ``QdrantBackend`` (servidor real). Um backend só lida com todas as
    coleções — ``collection`` é parâmetro de cada chamada, não do
    construtor.

    Consumido por ``tools/rag.py`` (`vector_search`, `manage_retriever`) e
    ``embedding/background.py`` (worker de indexação) — nenhum dos dois
    deve mais abrir ``lancedb.connect_async`` diretamente.
    """
    global _vector_store_backend
    if _vector_store_backend is not None:
        return _vector_store_backend

    from backend.services.license import get_effective_storage_mode
    from backend.settings import settings as _s

    mode = get_effective_storage_mode()
    if mode == "complete" and _s.qdrant_url:
        from backend.storage.vectorstore.qdrant_backend import QdrantBackend

        _vector_store_backend = QdrantBackend(
            url=_s.qdrant_url, api_key=_s.qdrant_api_key
        )
        logger.debug("storage/factory: vector store backend = Qdrant (complete)")
    else:
        from backend.storage.vectorstore.lancedb_backend import LanceDBBackend

        _vector_store_backend = LanceDBBackend(lancedb_dir=str(_s.lancedb_dir))
        logger.debug("storage/factory: vector store backend = LanceDB (lite)")

    return _vector_store_backend


# ---------------------------------------------------------------------------
# VectorStore raw (lancedb.AsyncTable — compatibilidade com código existente)
# ---------------------------------------------------------------------------


async def get_vector_store(
    collection: str = "articles",
    *,
    path: str | None = None,
) -> Any:
    """Retorna (ou cria) o ``lancedb.AsyncTable`` para ``collection``.

    Usado pelo background worker e pelos nós de RAG que operam na API baixo nível
    do LanceDB (escrita batch, índices IVF, FTS). Para busca/upsert/delete
    roteados por ``storage_mode``, prefira ``get_vector_store_backend()``.

    Args:
        collection: Nome da tabela/coleção LanceDB. Default ``"articles"``.
        path:       Diretório LanceDB. None = ``settings.lancedb_dir``.

    Returns:
        ``lancedb.AsyncTable`` ou None se a tabela não existir.
    """
    cache_key = f"{path or ''}::{collection}"
    if cache_key in _vector_stores:
        return _vector_stores[cache_key]

    from backend.storage.lancedb.connection import get_lancedb

    db = await get_lancedb(path)
    try:
        table = await db.open_table(collection)
    except Exception:
        logger.debug("storage/factory: tabela %r não existe em %r", collection, path)
        table = None

    _vector_stores[cache_key] = table
    if table is not None and cache_key not in _optimize_tasks:
        # `optimize_table`/`create_ivf_index` existiam há tempo mas nunca
        # eram chamados em lugar nenhum do app — nenhuma tabela LanceDB
        # recebia compactação periódica nem ganhava índice IVF_PQ conforme
        # crescia. Agenda no primeiro `open_table` bem-sucedido de cada
        # coleção (uma vez por processo); falha ao agendar não impede o
        # caller de usar a tabela normalmente.
        try:
            from backend.storage.lancedb.optimize import schedule_optimize

            _optimize_tasks[cache_key] = schedule_optimize(table)
        except Exception:
            logger.warning(
                "storage/factory: falha ao agendar otimização periódica de %r",
                collection,
                exc_info=True,
            )
    return table


def _build_cohere_embeddings() -> Any:
    """``VectoraCohereEmbeddings`` (nativo) se a key Cohere estiver configurada."""
    try:
        from backend.llm.cohere.client import CohereClient
        from backend.llm.cohere.embeddings import VectoraCohereEmbeddings
        from backend.settings import settings as _s

        key = _s.get_cohere_api_key()
        model = _s.embedding_model
        if not key or not model:
            return None

        return VectoraCohereEmbeddings(model=model, client=CohereClient(key))
    except Exception:
        return None


def _build_voyage_embeddings() -> Any:
    """``VectoraVoyageEmbeddings`` (nativo) se a key VoyageAI estiver configurada."""
    try:
        from backend.llm.voyage.client import VoyageClient
        from backend.llm.voyage.embeddings import VectoraVoyageEmbeddings
        from backend.settings import settings as _s

        key = _s.voyage_api_key
        model = _s.voyage_embedding_model
        if not key or not model:
            return None

        return VectoraVoyageEmbeddings(model=model, client=VoyageClient(key))
    except Exception:
        return None


def _build_ollama_embeddings(model_override: str | None = None) -> Any:
    """``OllamaEmbeddings`` nativo (``POST /api/embed``) se
    ``ollama_embedding_model`` (ou ``model_override``, vindo de
    ``rag_settings.embed_model`` em runtime) estiver configurado."""
    try:
        from backend.llm.ollama.client import OllamaClient
        from backend.llm.ollama.embeddings import OllamaEmbeddings
        from backend.settings import settings as _s

        model = model_override or _s.ollama_embedding_model
        if not model:
            return None

        return OllamaEmbeddings(
            model=model,
            client=OllamaClient(
                base_url=_s.ollama_base_url or "http://127.0.0.1:11434"
            ),
        )
    except Exception:
        return None


def _build_openrouter_embeddings(model_override: str | None = None) -> Any:
    """``OpenRouterEmbeddings`` nativo — expõe `input_type` (modelos
    assimétricos precisam saber se o texto é consulta ou documento) e
    `usage.cost`, que o `OpenAIEmbeddings` com base_url trocado descartava.
    ``model_override`` vem de ``rag_settings.embed_model`` em runtime."""
    try:
        from backend.llm.openrouter.client import OpenRouterClient
        from backend.llm.openrouter.embeddings import OpenRouterEmbeddings
        from backend.settings import settings as _s

        key = _s.openrouter_api_key
        model = model_override or _s.openrouter_embedding_model
        if not key or not model:
            return None

        return OpenRouterEmbeddings(
            model=model,
            client=OpenRouterClient(api_key=key),
        )
    except Exception:
        return None


def _build_lc_embeddings() -> Any:
    """Embeddings com preferência configurável + fallback Cohere↔Voyage↔local.

    Ordem de resolução:
    1. ``rag_settings.embed_provider`` em runtime (aba de memória do
       workbench, ``PATCH /rag/settings``), se != "auto" e buildável — mesmo
       padrão que ``_build_reranker()`` já usa pra ``rerank_provider``. Sem
       isso a escolha do usuário na UI era só persistida, nunca lida aqui.
    2. ``settings.embedding_provider`` (env var ``EMBEDDING_PROVIDER``),
       se "auto"/vazio em runtime — respeita a escolha estática de quem não
       usa a UI (VPS/CLI).
    3. Cohere + Voyage juntos → ``FallbackEmbeddings`` (troca automática em
       quota esgotada, em runtime).
    4. Só um dos dois → esse.
    5. Nenhum dos dois → Ollama, depois OpenRouter (embeddings locais/gateway,
       sem custo de API hospedada — mas sem rerank: Cohere/Voyage-only).
    6. Nada configurado → None (modo sem embeddings).
    """
    from backend.settings import settings as _s
    from backend.workspace.runtime_settings import runtime_settings

    rag = runtime_settings.rag_settings
    runtime_pref = str(rag.get("embed_provider", "auto"))
    runtime_model = str(rag.get("embed_model") or "") or None

    builders: dict[str, Any] = {
        "cohere": _build_cohere_embeddings,
        "voyage": _build_voyage_embeddings,
        "ollama": lambda: _build_ollama_embeddings(runtime_model),
        "openrouter": lambda: _build_openrouter_embeddings(runtime_model),
    }
    preference = (
        runtime_pref if runtime_pref in builders else (_s.embedding_provider or "")
    )
    if preference in builders:
        preferred = builders[preference]()
        if preferred is not None:
            return preferred
        logger.warning(
            "storage/factory: embedding_provider=%r configurado mas sem "
            "credencial/modelo — caindo no fallback padrão",
            preference,
        )

    cohere = _build_cohere_embeddings()
    voyage = _build_voyage_embeddings()
    if cohere is not None and voyage is not None:
        from backend.llm.fallback_embeddings import FallbackEmbeddings

        return FallbackEmbeddings(
            cohere,
            voyage,
            primary_id=f"cohere:{_s.embedding_model}",
            secondary_id=f"voyage:{_s.voyage_embedding_model}",
        )
    if cohere or voyage:
        return cohere or voyage

    return _build_ollama_embeddings() or _build_openrouter_embeddings()


# ---------------------------------------------------------------------------
# Guard de dimensão — troca de embedding_provider sem reindex quebraria
# similarity search silenciosamente (vetores de dimensões diferentes na
# mesma coleção). Probe leve (uma chamada embed) na primeira construção do
# VectorStore por processo/coleção, comparado contra a dimensão persistida.
# ---------------------------------------------------------------------------


class EmbeddingDimensionMismatchError(Exception):
    """A coleção foi indexada com um embedding de dimensão diferente da
    configurada agora — reindexação necessária antes de buscar/inserir."""


async def _embedding_meta_db() -> Any:
    from backend.api.handlers.threads import _get_db as _threads_db

    return await _threads_db()


async def _ensure_embedding_meta_table(db: Any) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS embedding_index_meta (
            collection TEXT PRIMARY KEY,
            provider   TEXT NOT NULL,
            dimension  INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    await db.commit()


async def _check_embedding_dimension(
    collection: str, dimension: int, provider: str = "unknown"
) -> None:
    """Levanta ``EmbeddingDimensionMismatchError`` se a coleção já foi
    indexada com uma dimensão diferente da passada. Na primeira vez (sem
    registro), persiste a dimensão atual.

    Chamado no momento do upsert (``embedding/background.py``), com a
    dimensão do vetor já calculado — evita uma chamada de embedding extra só
    para sondar a dimensão.
    """
    from datetime import UTC, datetime

    db = await _embedding_meta_db()
    await _ensure_embedding_meta_table(db)
    async with db.execute(
        "SELECT provider, dimension FROM embedding_index_meta WHERE collection = ?",
        (collection,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        await db.execute(
            "INSERT INTO embedding_index_meta (collection, provider, dimension, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (collection, provider, dimension, datetime.now(UTC).isoformat()),
        )
        await db.commit()
        return

    existing_provider, existing_dimension = row
    if existing_dimension != dimension:
        msg = (
            f"Coleção {collection!r} foi indexada com embeddings de "
            f"{existing_dimension} dimensões (provider {existing_provider!r}), "
            f"mas o embedding configurado agora tem {dimension} dimensões "
            f"(provider {provider!r}). Reindexe a coleção (apague e reconstrua) "
            "antes de continuar, ou volte ao provider de embedding anterior."
        )
        raise EmbeddingDimensionMismatchError(msg)


# ---------------------------------------------------------------------------
# Asyncpg pool (F7 — complete mode)
# ---------------------------------------------------------------------------


async def _ensure_postgres_schema(pool: Any) -> None:
    """Aplica migrations Postgres pendentes via PostgresMigrationRunner."""
    from backend.storage.migrations.postgres_runner import PostgresMigrationRunner

    try:
        async with pool.acquire() as conn:
            runner = PostgresMigrationRunner(conn)
            await runner.upgrade()
        logger.info("storage/factory: schema Postgres garantido via migrations")
    except Exception as exc:
        logger.warning("storage/factory: erro ao garantir schema Postgres: %s", exc)


async def get_pg_pool(dsn: str | None = None) -> Any:
    """Retorna (ou cria) o pool asyncpg compartilhado do modo complete.

    O pool é um singleton por processo — criado na primeira chamada e
    reutilizado em todas as chamadas subsequentes. Para fechar explicitamente
    (ex: teardown de testes) use ``close_pg_pool()``.

    Args:
        dsn: DSN asyncpg (``postgresql://user:pass@host/db``). None usa
             ``settings.postgres_dsn``. Deve ser asyncpg nativo (não
             ``postgresql+asyncpg://`` do SQLAlchemy).

    Returns:
        ``asyncpg.Pool`` configurado com min_size=2, max_size=20.

    Raises:
        RuntimeError: Se nenhum DSN estiver configurado.
    """
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool

    import asyncpg

    from backend.rbac.subscription import require_pro
    from backend.settings import settings as _s

    require_pro()

    effective_dsn = dsn or _s.postgres_dsn
    if not effective_dsn:
        msg = (
            "postgres_dsn não configurado. "
            "Defina POSTGRES_DSN no .env ou settings.json para usar o modo complete."
        )
        raise RuntimeError(msg)

    # asyncpg espera postgresql:// — normaliza se vier com +asyncpg do SQLAlchemy
    normalized = effective_dsn.replace("postgresql+asyncpg://", "postgresql://")

    _pg_pool = await asyncpg.create_pool(normalized, min_size=2, max_size=20)
    await _ensure_postgres_schema(_pg_pool)
    logger.debug("storage/factory: asyncpg pool criado dsn=%s", normalized[:30] + "…")
    return _pg_pool


async def close_pg_pool() -> None:
    """Fecha o pool asyncpg gracefully. Usado no shutdown do servidor e em testes."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
        logger.debug("storage/factory: asyncpg pool fechado")


# ---------------------------------------------------------------------------
# Health check agregado
# ---------------------------------------------------------------------------


async def storage_health() -> dict[str, Any]:
    """Retorna o status de saúde de todos os backends configurados.

    Usado pelo endpoint ``GET /admin/storage`` (F10) e pelo CLI
    ``vectora storage info`` (F11).

    Returns:
        ``{"checkpointer": {...}, "store": {...}, "lancedb": {...}, ...}``
    """
    result: dict[str, Any] = {}

    # Checkpointer — testa se o arquivo .db é acessível
    try:
        from backend.settings import settings as _s

        db_path = _s.db_dsn
        if db_path:
            import aiosqlite

            async with aiosqlite.connect(db_path) as conn:
                await conn.execute("SELECT 1")
            result["checkpointer"] = {"ok": True, "error": None, "internal": True}
        else:
            result["checkpointer"] = {
                "ok": False,
                "error": "db_dsn não configurado",
                "internal": True,
            }
    except Exception as exc:
        result["checkpointer"] = {"ok": False, "error": str(exc), "internal": True}

    # Store — verifica se o VectoraStore foi criado e a conexão está ativa
    try:
        store = await get_store()
        result["store"] = {"ok": store is not None, "error": None, "internal": True}
    except Exception as exc:
        result["store"] = {"ok": False, "error": str(exc), "internal": True}

    # LanceDB / Qdrant (Vector Store Backend)
    try:
        from backend.storage.vectorstore.qdrant_backend import QdrantBackend

        vs_backend = await get_vector_store_backend()
        vs_health: VectorStoreHealth = await vs_backend.health()
        health_dict = vs_health.model_dump(exclude_none=True)
        if "error" not in health_dict:
            health_dict["error"] = None
        if isinstance(vs_backend, QdrantBackend):
            result["qdrant"] = health_dict
            result["lancedb"] = {
                "ok": None,
                "error": "modo complete ativo (usando Qdrant)",
            }
        else:
            result["lancedb"] = health_dict
            result["qdrant"] = {"ok": None, "error": "não configurado (modo lite)"}
    except Exception as exc:
        result["lancedb"] = {"ok": False, "error": str(exc), "internal": True}
        result["qdrant"] = {"ok": False, "error": str(exc)}

    # Postgres — testado apenas no modo complete
    try:
        from backend.services.license import get_effective_storage_mode
        from backend.settings import settings as _s

        if get_effective_storage_mode() == "complete" and _s.postgres_dsn:
            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            result["postgres"] = {"ok": True, "error": None}
        else:
            result["postgres"] = {"ok": None, "error": "não configurado (modo lite)"}
    except Exception as exc:
        result["postgres"] = {"ok": False, "error": str(exc)}

    # Redis — testado apenas no modo complete
    try:
        from backend.services.license import get_effective_storage_mode
        from backend.settings import settings as _s

        if get_effective_storage_mode() == "complete" and _s.redis_url:
            import redis.asyncio as aredis

            client = aredis.from_url(_s.redis_url)
            try:
                await client.ping()
                result["redis"] = {"ok": True, "error": None}
            finally:
                await client.aclose()
        else:
            result["redis"] = {"ok": None, "error": "não configurado (modo lite)"}
    except Exception as exc:
        result["redis"] = {"ok": False, "error": str(exc)}

    # Config — resumo do modo de armazenamento e backends configurados
    # (sem expor segredos: apenas booleanos de "configurado").
    from backend.settings import settings as _s
    from backend.workspace.runtime_settings import runtime_settings

    result["config"] = {
        "storage_mode": runtime_settings.storage_mode,
        "postgres_configured": bool(_s.postgres_dsn),
        "redis_configured": bool(_s.redis_url),
        "qdrant_configured": bool(_s.qdrant_url),
    }

    return result


# ---------------------------------------------------------------------------
# Reset (usado em testes)
# ---------------------------------------------------------------------------


def _reset_singletons() -> None:
    """Limpa os singletons. Para uso exclusivo em testes.

    Inclui o cache de conexão LanceDB (`backend.storage.lancedb.connection.
    _default_cache`) — mesmo padrão dos outros singletons de processo
    (`_worker_lock`/`_queue_lock`, ver `tests/conftest.py::
    _reset_async_singleton_locks`): se a conexão travar de verdade dentro
    de `async with self._lock:` (`LanceDBConnectionCache.connect()`), esse
    `asyncio.Lock()` nunca é liberado e todo teste seguinte que reusar o
    mesmo cache trava esperando o lock, silenciosamente."""
    global _store, _pg_pool, _vector_store_backend
    _store = None
    _pg_pool = None
    _vector_store_backend = None
    _vector_stores.clear()
    for task in _optimize_tasks.values():
        task.cancel()
    _optimize_tasks.clear()

    from backend.storage.lancedb import connection as _lancedb_connection

    _lancedb_connection._default_cache = None
