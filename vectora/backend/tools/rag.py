"""RAG tools: embedding assíncrono, busca vetorial e ingestão de documentos."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from backend.embedding.queue import get_embedding_queue
from backend.services.text import text_service
from backend.settings import settings
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.documents import Document

if TYPE_CHECKING:
    from backend.llm.systemone.harness import SystemOneHarness

try:
    import lancedb
except ImportError:
    lancedb = None

logger = logging.getLogger(__name__)


def _is_cohere_quota_error(err: str) -> bool:
    """Retorna True se o erro indica quota/rate-limit do Cohere."""
    err_lower = err.lower()
    return (
        "429" in err
        or "too many requests" in err_lower
        or "rate limit" in err_lower
        or "quota" in err_lower
        or "you are using a trial key" in err_lower
        or "monthly active users" in err_lower
    )


def _build_cohere_reranker() -> Any:
    """``VectoraCohereRerank`` (nativo) se a key Cohere estiver configurada."""
    try:
        from backend.llm.cohere.client import CohereClient
        from backend.llm.cohere.rerank import VectoraCohereRerank

        key = settings.get_cohere_api_key()
        if not key:
            return None
        return VectoraCohereRerank(
            model=settings.reranker_model,
            client=CohereClient(key),
            top_n=int(_rag_runtime().get("reranker_top_k", settings.reranker_top_k)),
        )
    except Exception:
        logger.warning("rag: falha ao montar reranker Cohere", exc_info=True)
        return None


def _build_voyage_reranker() -> Any:
    """``VectoraVoyageRerank`` (nativo) se a key Voyage estiver configurada."""
    try:
        from backend.llm.voyage.client import VoyageClient
        from backend.llm.voyage.rerank import VectoraVoyageRerank

        key = settings.voyage_api_key
        if not key:
            return None
        return VectoraVoyageRerank(
            model=settings.voyage_rerank_model,
            client=VoyageClient(key),
            top_k=int(_rag_runtime().get("reranker_top_k", settings.reranker_top_k)),
        )
    except Exception:
        logger.warning("rag: falha ao montar reranker Voyage", exc_info=True)
        return None


def _build_openrouter_reranker() -> Any:
    """``OpenRouterRerank`` se a key e o modelo estão configurados, senão None.

    O modelo vem de ``configured_gateway_model`` (escolha da UI ganha do env),
    mesmo caminho das demais capacidades de gateway.
    """
    try:
        from backend.llm.openrouter.client import OpenRouterClient
        from backend.llm.openrouter.rerank import OpenRouterRerank
        from backend.settings import configured_gateway_model

        key = settings.openrouter_api_key
        model = configured_gateway_model("openrouter", "rerank")
        if not key or not model:
            return None
        return OpenRouterRerank(
            model=model,
            client=OpenRouterClient(api_key=key),
            top_n=int(_rag_runtime().get("reranker_top_k", settings.reranker_top_k)),
        )
    except Exception:
        logger.warning("rag: falha ao montar reranker OpenRouter", exc_info=True)
        return None


class SystemOneRerank:
    """Reranker opcional baseado em Decision Models via `SystemOneHarness`.

    Preserva metadados, IDs estáveis e permissões dos candidatos filtrados,
    aplica desempate determinístico e degrada para o ranking original em falha.
    """

    def __init__(
        self,
        harness: SystemOneHarness,
        *,
        top_n: int = 5,
        workspace_id: str = "default",
    ) -> None:
        self.harness = harness
        self.top_n = max(1, int(top_n))
        self.workspace_id = workspace_id

    async def acompress_documents(
        self, documents: Any, query: str, callbacks: Any = None
    ) -> list[Document]:
        docs = list(documents)
        if not docs:
            return []
        limit = min(self.top_n, len(docs))
        candidates = [
            {
                "id": str(
                    doc.metadata.get("id")
                    or doc.metadata.get("chunk_id")
                    or doc.metadata.get("source")
                    or f"doc_{idx}"
                ),
                "snippet": doc.page_content[:2000],
                "original_index": idx,
                "doc": doc,
            }
            for idx, doc in enumerate(docs)
        ]
        try:
            ranked = await self.harness.rerank_candidates(
                workspace_id=self.workspace_id,
                query=query,
                candidates=candidates,
                top_k=limit,
            )
            if not ranked:
                return docs[:limit]
            return [item["doc"] for item in ranked[:limit] if "doc" in item] or docs[
                :limit
            ]
        except Exception:
            logger.warning(
                "rag: falha no reranking SystemOne — preservando ranking original",
                exc_info=True,
            )
            return docs[:limit]

    def compress_documents(
        self, documents: Any, query: str, callbacks: Any = None
    ) -> list[Document]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.acompress_documents(documents, query, callbacks))
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return cast(
                "list[Document]",
                pool.submit(
                    asyncio.run, self.acompress_documents(documents, query, callbacks)
                ).result(),
            )


def _build_systemone_reranker(
    workspace_id: str = "default",
) -> SystemOneRerank | None:
    """Constrói `SystemOneRerank` se os Decision Models estiverem habilitados."""
    try:
        from backend.tools.systemone_tools import _build_default_harness

        if not settings.systemone_enabled:
            return None
        harness = _build_default_harness()
        top_k = int(_rag_runtime().get("reranker_top_k", settings.reranker_top_k))
        return SystemOneRerank(harness, top_n=top_k, workspace_id=workspace_id)
    except Exception:
        logger.warning("rag: falha ao montar reranker SystemOne", exc_info=True)
        return None


def _rag_runtime() -> dict[str, Any]:
    """Settings de RAG configurados em runtime (aba de memória)."""
    from backend.workspace.runtime_settings import runtime_settings

    return runtime_settings.rag_settings


def _build_reranker(workspace_id: str = "default") -> Any:
    """Reranker com fallback Cohere↔Voyage por quota.

    Honra os settings de runtime: ``reranker_enabled`` (off → None, sem rerank) e
    ``rerank_provider`` ("auto" usa ``settings.reranker_type``; "cohere"/"voyage"
    força). ``settings.reranker_type`` define o primário; o outro vira secundário.
    Com ambos disponíveis devolve ``FallbackReranker``; com só um, esse; sem
    nenhum, ``None`` — o caller cai para os resultados sem rerank.
    """
    rag = _rag_runtime()
    if not rag.get("reranker_enabled", True):
        return None
    pref = str(rag.get("rerank_provider", "auto"))
    rtype = (
        pref
        if pref in ("cohere", "voyage", "openrouter", "systemone")
        else settings.reranker_type
    )
    if rtype == "systemone":
        return _build_systemone_reranker(workspace_id=workspace_id)
    if rtype == "openrouter":
        primary = _build_openrouter_reranker()
        if primary is None:
            return None
        secondary = _build_cohere_reranker()
        from backend.settings import configured_gateway_model

        primary_id = f"openrouter:{configured_gateway_model('openrouter', 'rerank')}"
        secondary_id = f"cohere:{settings.reranker_model}"
    elif rtype == "voyage":
        primary = _build_voyage_reranker()
        if primary is None:
            return None
        secondary = _build_cohere_reranker()
        primary_id = f"voyage:{settings.voyage_rerank_model}"
        secondary_id = f"cohere:{settings.reranker_model}"
    elif rtype == "cohere":
        primary = _build_cohere_reranker()
        if primary is None:
            return None
        secondary = _build_voyage_reranker()
        primary_id = f"cohere:{settings.reranker_model}"
        secondary_id = f"voyage:{settings.voyage_rerank_model}"
    else:
        return None

    if secondary is None:
        return primary

    from backend.llm.fallback_reranker import FallbackReranker

    return FallbackReranker(
        primary,
        secondary,
        primary_id=primary_id,
        secondary_id=secondary_id,
    )


@vtool(
    extras=ToolExtras(
        render_hint="queue_badge",
        category="rag",
        destructive=False,
        icon="layers",
    )
)
async def embedding(
    text: str, collection: str = "articles", metadata: dict[str, Any] | None = None
) -> str:
    """Enfileira documento para embedding assíncrono (fire-and-forget).

    Em vez de bloquear esperando Cohere (1-2 segundos por chunk), enfileira o documento
    imediatamente. Um background worker processa a fila e indexa em LanceDB.

    Args:
        text: Texto do documento a indexar
        collection: Nome da coleção LanceDB (articles, wiki, api_docs, knowledge_base)
        metadata: Metadados opcionais (source, author, timestamp, etc)

    Returns:
        JSON com status fire_and_forget + queue_id, ou error
    """
    if not settings.embedding_queue_enabled:
        logger.error("embedding called but queue not enabled")
        return json.dumps(
            {"status": "error", "error": "Embedding queue not configured."}
        )

    _t0 = time.perf_counter()
    try:
        queue = await get_embedding_queue(settings.embedding_queue_dsn)
        queue_id = await queue.enqueue(text, collection, metadata)

        logger.info(
            "embedding_enqueued",
            extra={
                "queue_id": queue_id,
                "collection": collection,
                "text_length": len(text),
            },
        )
        try:
            from backend.persistence.tracer import tracer as _tr

            async with _tr.span("embedding_tool", "enqueue") as _s:
                _s.set(queue_id=queue_id, collection=collection, text_len=len(text))
        except Exception:
            pass

        return json.dumps(
            {
                "status": "fire_and_forget",
                "queue_id": queue_id,
                "collection": collection,
                "message": "Document enqueued for async embedding and indexing.",
            }
        )

    except Exception as e:
        err = str(e)
        logger.exception(
            "embedding_enqueue_failed",
            extra={"collection": collection, "text_length": len(text)},
        )
        try:
            from backend.persistence.tracer import tracer as _tr

            _tr.record_sync(
                "embedding_tool",
                "enqueue",
                time.perf_counter() - _t0,
                {"collection": collection},
                status="error",
            )
        except Exception:
            pass
        if _is_cohere_quota_error(err):
            return json.dumps(
                {
                    "status": "quota_error",
                    "error": (
                        "**Cohere: quota/rate limit atingido.**\n"
                        "Aguarde alguns minutos ou verifique seu plano em dashboard.cohere.com."
                    ),
                    "collection": collection,
                }
            )
        return json.dumps(
            {
                "status": "error",
                "error": err or "Failed to enqueue embedding",
                "collection": collection,
            }
        )


def _resolve_search_collections(workspace_id: str | None) -> list[str]:
    """Coleções LanceDB a varrer numa busca sem `collection` explícito.

    Workspace com buckets ativos (`backend.services.rag_buckets`) varre só
    essas tabelas — indexar a Godot e 10 libs TypeScript não faz uma busca
    sobre o jogo Godot varrer as libs junto. Sem `workspace_id` ou sem
    nenhum bucket ainda cadastrado (inclusive todo dado indexado antes dos
    buckets existirem), cai na tabela legada `"articles"` — mesmo
    comportamento de sempre, sem perda de dado nem migração física."""
    if not workspace_id:
        return ["articles"]
    from backend.services import rag_buckets
    from backend.workspace.runtime_settings import runtime_settings

    bucket_ids = rag_buckets.get_active_bucket_ids(runtime_settings, workspace_id)
    if not bucket_ids:
        return ["articles"]
    return [f"bucket_{bid}" for bid in bucket_ids]


async def _search_one_collection(
    backend: Any, collection: str, query_vector: Any, limit: int
) -> list[dict[str, Any]]:
    """Busca vetorial numa única coleção via `VectorStoreBackend` — LanceDB
    (lite) ou Qdrant (complete), conforme `storage_mode`. Coleção
    ausente/timeout devolve lista vazia (backend nunca lança) — chamador
    decide o que fazer com zero resultados dentro de um fan-out."""
    hits = await backend.search(collection, query_vector, limit)
    return [
        {
            "id": hit.id,
            "score": hit.score,
            "content": hit.content,
            "metadata": hit.metadata,
            "collection": hit.collection,
        }
        for hit in hits
    ]


async def _search_one_collection_text(
    backend: Any, collection: str, query: str, limit: int
) -> list[dict[str, Any]]:
    """Busca lexical (BM25-like) numa única coleção — mesmo shape de
    `_search_one_collection`, pra `_reciprocal_rank_fusion` tratar as duas
    listas de forma uniforme."""
    hits = await backend.search_text(collection, query, limit)
    return [
        {
            "id": hit.id,
            "score": hit.score,
            "content": hit.content,
            "metadata": hit.metadata,
            "collection": hit.collection,
        }
        for hit in hits
    ]


_RRF_K = 60


def _reciprocal_rank_fusion(
    vector_results: list[dict[str, Any]],
    text_results: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion — funde busca vetorial (distância, menor
    melhor) com busca textual (relevância, maior melhor) sem precisar
    normalizar as duas escalas incompatíveis: só a ORDEM de cada lista de
    entrada importa. `k=60` é a constante padrão da literatura de RRF
    (Cormack et al.), amortecendo o peso de posições extremas.

    Ambas as listas já devem vir ordenadas da melhor pra pior posição
    (rank 1 = melhor)."""
    scores: dict[str, float] = {}
    docs: dict[str, dict[str, Any]] = {}

    for rank, r in enumerate(vector_results, start=1):
        key = f"{r['collection']}::{r['id']}"
        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        docs.setdefault(key, r)

    for rank, r in enumerate(text_results, start=1):
        key = f"{r['collection']}::{r['id']}"
        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        docs.setdefault(key, r)

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    fused: list[dict[str, Any]] = []
    for key, score in ordered[:limit]:
        doc = dict(docs[key])
        doc["score"] = score
        fused.append(doc)
    return fused


@vtool(
    extras=ToolExtras(
        render_hint="search_results",
        category="rag",
        destructive=False,
        icon="database",
    )
)
async def vector_search(
    ctx: ToolContext,
    query: str,
    collection: str | None = None,
    limit: int = 5,
) -> str:
    """Busca o banco de dados vetorial LanceDB por documentos similares.

    LanceDB é file-based — nenhum container ou servidor é necessário.

    Args:
        query: String da consulta de busca
        collection: Nome de uma tabela LanceDB específica — quando omitido,
            busca nos buckets ativos do workspace (`manage_retriever`/aba
            Memória controlam quais estão ativos); passar um nome força
            busca só naquela tabela, ignorando os buckets ativos.
        limit: Número máximo de resultados a retornar (por coleção, quando
            varrendo múltiplos buckets — os resultados finais são
            reordenados por score e cortados no mesmo `limit`)

    Returns:
        JSON com documentos e scores de similaridade
    """
    try:
        if lancedb is None:
            return "LanceDB dependency missing."

        from backend.storage.factory import _build_lc_embeddings

        embeddings_model = _build_lc_embeddings()
        if embeddings_model is None:
            logger.error("vector_search called but no embedding provider configured")
            return json.dumps(
                {"status": "failed", "error": "no embedding provider configured"}
            )

        collections = (
            [collection]
            if collection
            else _resolve_search_collections(ctx.workspace_id or None)
        )

        from backend.storage.factory import get_vector_store_backend

        backend = await get_vector_store_backend()

        # embed_query usa input_type="search_query" (Cohere v3 é assimétrico:
        # os documentos são indexados com embed_documents → "search_document").
        # Não trocar por embed_documents aqui. `_build_lc_embeddings()` já
        # honra o fallback multi-provider (Cohere↔Voyage↔Ollama↔OpenRouter) —
        # antes a busca era hardcoded pra Cohere sem fallback, diferente da
        # indexação, que já tinha esse fallback (assimetria corrigida aqui).
        from backend.embedding.cache_embeddings import embed_query_cached

        query_vector = await embed_query_cached(
            query, settings.embedding_model, embeddings_model.embed_query
        )

        fanned_out = [
            await _search_one_collection(backend, coll, query_vector, limit)
            for coll in collections
        ]
        vector_results = sorted(
            (r for batch in fanned_out for r in batch), key=lambda r: r["score"]
        )

        if settings.rag_hybrid_enabled:
            text_fanned_out = [
                await _search_one_collection_text(
                    backend, coll, query, settings.rag_hybrid_fetch_limit
                )
                for coll in collections
            ]
            text_results = sorted(
                (r for batch in text_fanned_out for r in batch),
                key=lambda r: r["score"],
                reverse=True,
            )
            results = _reciprocal_rank_fusion(vector_results, text_results, limit)
        else:
            results = vector_results[:limit]

        if not results:
            return json.dumps(
                {
                    "status": "no_results",
                    "message": f"Nenhum resultado em {collections}",
                }
            )

        # Reranking opcional — melhora precisão (Cohere ou VoyageAI)
        reranker = _build_reranker(workspace_id=ctx.workspace_id or "default")
        if results and reranker is not None:
            try:
                docs_to_rerank = [
                    Document(page_content=str(r["content"]), metadata=r["metadata"])
                    for r in results
                ]
                acompress = getattr(reranker, "acompress_documents", None)
                if callable(acompress):
                    reranked_docs = await acompress(docs_to_rerank, query)
                else:
                    reranked_docs = await asyncio.to_thread(
                        reranker.compress_documents, docs_to_rerank, query
                    )
                results = [
                    {
                        "content": doc.page_content,
                        "metadata": doc.metadata,
                        "relevance_score": doc.metadata.get("relevance_score"),
                    }
                    for doc in reranked_docs
                ]
                logger.info("vector_search_reranked", extra={"top_k": len(results)})
            except Exception as rerank_err:
                logger.warning(f"Reranking failed, using raw results: {rerank_err}")
                for r in results:
                    r["reranking_status"] = "unavailable"

        logger.info(
            "vector_search completed",
            extra={
                "query": query,
                "collections": collections,
                "result_count": len(results),
            },
        )
        try:
            from backend.persistence.tracer import tracer as _tr

            async with _tr.span("vector_search_tool", "search") as _s:
                _s.set(
                    collections=collections,
                    n_results=len(results),
                    query_len=len(query),
                )
        except Exception:
            pass

        return json.dumps(
            {
                "status": "success",
                "results": results,
                "query": query,
                "collections": collections,
            }
        )

    except Exception as e:
        err = str(e)
        logger.exception("vector_search_failed", extra={"query": query})
        if _is_cohere_quota_error(err):
            return json.dumps(
                {
                    "status": "quota_error",
                    "error": (
                        "**Cohere: quota/rate limit atingido.**\n"
                        "Aguarde alguns minutos ou verifique seu plano em dashboard.cohere.com."
                    ),
                }
            )
        return json.dumps({"status": "failed", "error": err or "Vector search failed"})


_DOC_EXTRACTORS: dict[str, Any] = {}


def _extract_text_for_ingest(file_path: Path) -> str:
    """Despacha por extensão pra extrair texto real de PDF/DOCX/XLSX antes
    de cair no `read_text()` puro — sem isso, esses formatos viravam lixo
    binário decodificado como UTF-8 (`errors="ignore"`), indexado no RAG
    sem nenhum valor. Os 3 extratores (`context_graph/detect.py`) já são
    defensivos por conta própria (devolvem `""` em qualquer falha, nunca
    propagam) — um PDF corrompido produz zero chunks pro arquivo, não
    derruba o resto do diretório."""
    if not _DOC_EXTRACTORS:
        from backend.context_graph.detect import (
            docx_to_markdown,
            extract_pdf_text,
            xlsx_to_markdown,
        )

        _DOC_EXTRACTORS[".pdf"] = extract_pdf_text
        _DOC_EXTRACTORS[".docx"] = docx_to_markdown
        _DOC_EXTRACTORS[".xlsx"] = xlsx_to_markdown

    extrator = _DOC_EXTRACTORS.get(file_path.suffix.lower())
    if extrator is not None:
        return extrator(file_path)
    return file_path.read_text(encoding="utf-8", errors="ignore")


@vtool(
    extras=ToolExtras(
        render_hint="queue_progress",
        category="rag",
        destructive=False,
        icon="upload",
    )
)
async def ingest_docs(
    ctx: ToolContext,
    directory_path: str,
    collection: str = "articles",
    glob_pattern: str = "**/*.py",
) -> str:
    """Indexa um diretório inteiro de arquivos no banco vetorial (LanceDB).

    Use quando o usuário pedir para:
    - "fazer embedding de uma pasta" / "indexar uma pasta"
    - "adicionar arquivos ao RAG" / "rag add <pasta>"
    - Indexar código-fonte Python, documentação ou qualquer conjunto de arquivos

    NUNCA chamar via terminal — esta é uma tool nativa, não um comando de shell.
    Respeita automaticamente .gitignore e .vectoraignore — __pycache__, .venv,
    node_modules e demais entradas de ambos os arquivos são ignorados.
    Crie um .vectoraignore na raiz do projeto para controle granular do RAG
    (mesmo formato do .gitignore, ex: "tests/fixtures/**", "*.generated.py").

    Args:
        directory_path: Caminho da pasta (ex: ".", "src/agents", "docs/")
        collection: Coleção LanceDB de destino (default: "articles")
        glob_pattern: Filtro de arquivos:
            - "**/*.py"  → arquivos Python (default — projetos Python)
            - "**/*.md"  → Markdown / documentação
            - "**/*"     → todos os arquivos de texto

    Returns:
        JSON com total_files, total_chunks, indexed (enfileirados), failed
    """
    from backend.services.ignore import load_ignore_spec, walk_files
    from backend.tools.fs import _confine

    # `directory_path` vem direto do modelo (tool call) — usa a mesma defesa
    # forte de path traversal de `file_read`/`file_write`/etc, não a checagem
    # leve de `is_safe_file_path` (pensada pra paths vindos de UI interna,
    # não de input arbitrário de LLM).
    path, err = _confine(directory_path, ctx)
    if path is None:
        return err
    if not path.is_dir():
        return f"Not a directory: {directory_path}"

    # Carrega specs combinadas (.gitignore + .vectoraignore) uma vez para todo o diretório
    spec = load_ignore_spec(path)

    # walk_files poda __pycache__/.venv/node_modules e dirs do gitignore
    # DURANTE o walk — rglob puro varria essas árvores inteiras antes de
    # filtrar, congelando a tool em repositórios grandes. Cada dir podado
    # (subárvore inteira) e cada arquivo batido pelo glob mas ignorado pelo
    # spec entram em skipped_ignored.
    files_to_ingest, skipped_ignored = walk_files(path, glob_pattern, spec)

    # Filtro por tipo de arquivo dos settings de RAG (code/document/paper).
    # Vazio = todos. Permite "RAG só de código" enquanto o Context Graph cuida
    # dos markdowns (e vice-versa).
    ingest_types = {str(t) for t in _rag_runtime().get("ingest_file_types", [])}
    if ingest_types:
        from backend.context_graph.detect import classify_file

        filtered: list[Any] = []
        for fp in files_to_ingest:
            ftype = classify_file(fp)
            if ftype is not None and str(ftype) in ingest_types:
                filtered.append(fp)
        skipped_ignored += len(files_to_ingest) - len(filtered)
        files_to_ingest = filtered

    if not files_to_ingest:
        return json.dumps(
            {
                "status": "no_files",
                "message": f"Nenhum arquivo encontrado em '{directory_path}' com padrão '{glob_pattern}' (após filtrar .gitignore, .vectoraignore)",
                "skipped_ignored": skipped_ignored,
            }
        )

    # Splitter vem do TextService — fonte única de verdade para chunking.
    # Encoding, chunk_size e chunk_overlap são definidos em Settings e
    # compartilhados com o token_counter do trim_messages em engine.py.
    success_count = 0
    fail_count = 0
    total_chunks = 0

    for file_path in files_to_ingest:
        try:
            text = _extract_text_for_ingest(file_path)
        except Exception as e:
            logger.warning(
                "ingest_docs: falha ao ler arquivo",
                extra={"file": str(file_path), "error": str(e)},
            )
            fail_count += 1
            continue

        chunks = text_service.split(text)
        total_chunks += len(chunks)

        for chunk_text in chunks:
            # workspace_id injetado no metadata para isolamento por projeto
            chunk_metadata: dict[str, Any] = {
                "source": str(file_path),
                "source_dir": directory_path,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
            if ctx.workspace_id:
                chunk_metadata["workspace_id"] = ctx.workspace_id

            try:
                res = await embedding(
                    text=chunk_text,
                    collection=collection,
                    metadata=chunk_metadata,
                )
                data = json.loads(res) if isinstance(res, str) else res
                if isinstance(data, dict) and data.get("status") == "fire_and_forget":
                    success_count += 1
                elif isinstance(data, dict) and data.get("status") == "quota_error":
                    # Cohere quota hit — abort early and surface the error
                    logger.warning(
                        "ingest_docs: quota Cohere atingida, abortando",
                        extra={"file": str(file_path)},
                    )
                    return json.dumps(
                        {
                            "status": "quota_error",
                            "error": (
                                "**Cohere: quota/rate limit atingido.**\n"
                                "Aguarde alguns minutos ou verifique seu plano em dashboard.cohere.com."
                            ),
                            "indexed_before_error": success_count,
                            "collection": collection,
                        }
                    )
                else:
                    fail_count += 1
            except Exception as chunk_err:
                logger.warning(
                    "ingest_docs: falha ao enfileirar chunk",
                    extra={"file": str(file_path), "error": str(chunk_err)},
                )
                fail_count += 1

    logger.info(
        "ingest_docs_completed",
        extra={
            "collection": collection,
            "total_files": len(files_to_ingest),
            "total_chunks": total_chunks,
            "success": success_count,
            "fail": fail_count,
            "skipped_ignored": skipped_ignored,
        },
    )

    return json.dumps(
        {
            "status": "completed",
            "total_files": len(files_to_ingest),
            "total_chunks": total_chunks,
            "indexed": success_count,
            "failed": fail_count,
            "skipped_ignored": skipped_ignored,
            "collection": collection,
        }
    )


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="rag",
        destructive=True,
        icon="settings",
    )
)
async def manage_retriever(
    action: Literal["list", "delete", "purge"],
    collection: str = "web_cache",
    source: str | None = None,
) -> str:
    """Gerencia documentos indexados no RAG — listar, remover ou limpar.

    Use para CORRIGIR a base de conhecimento. O Vectora indexa conteúdo web
    automaticamente (após curadoria). Quando uma fonte se revela errada — por
    exemplo, o usuário fornece depois o repositório ou doc canônico — use esta
    tool para remover o que foi indexado por engano. Indexar é só metade do
    trabalho; poder desfazer é o que mantém o RAG confiável.

    Args:
        action:
            - "list": lista os documentos da coleção (source, title, score)
            - "delete": remove documentos cujo source/url/title contém `source`
            - "purge": apaga a coleção inteira — use com cuidado
        collection: coleção LanceDB alvo (default: "web_cache", o bucket web).
            Use "articles" para o bucket de docs curados pelo usuário.
        source: para action="delete", trecho da URL/source/título a remover
            (ex: "godot-gameplay-systems"). Obrigatório quando action="delete".

    Returns:
        JSON com o resultado da operação
    """
    if action == "delete" and not source:
        return json.dumps(
            {
                "status": "error",
                "error": "Param 'source' é obrigatório para action='delete'.",
            }
        )

    from backend.storage.factory import get_vector_store_backend

    try:
        backend = await get_vector_store_backend()
    except Exception as e:
        return json.dumps(
            {"status": "error", "error": f"Falha ao conectar ao vector store: {e}"}
        )

    # purge — apaga a coleção inteira
    if action == "purge":
        try:
            await backend.purge(collection)
            logger.info("manage_retriever_purge", extra={"collection": collection})
            return json.dumps({"status": "purged", "collection": collection})
        except Exception as e:
            return json.dumps(
                {"status": "error", "error": f"Falha ao apagar '{collection}': {e}"}
            )

    # list / delete — lê todos os documentos da coleção
    try:
        async with asyncio.timeout(15):
            rows = await backend.list_rows(collection)
    except Exception as e:
        return json.dumps(
            {"status": "error", "error": f"Falha ao ler '{collection}': {e}"}
        )

    if not rows:
        return json.dumps(
            {
                "status": "no_results",
                "message": f"Coleção '{collection}' não encontrada ou vazia",
            }
        )

    if action == "list":
        items = [
            {
                "id": row.id,
                "source": row.metadata.get("source") or row.metadata.get("url", ""),
                "title": row.metadata.get("title", ""),
                "origin": row.metadata.get("origin", ""),
                "relevance_score": row.metadata.get("relevance_score"),
                "indexed_at": row.metadata.get("indexed_at", ""),
            }
            for row in rows
        ]
        return json.dumps(
            {
                "status": "success",
                "collection": collection,
                "count": len(items),
                "documents": items,
            }
        )

    # action == "delete" — 'source' já validado no início da função.
    needle = (source or "").lower()

    def _meta_matches(m: dict[str, Any]) -> bool:
        return (
            needle in str(m.get("source", "")).lower()
            or needle in str(m.get("url", "")).lower()
            or needle in str(m.get("title", "")).lower()
        )

    matched = [row.id for row in rows if _meta_matches(row.metadata)]
    if not matched:
        return json.dumps(
            {
                "status": "no_match",
                "collection": collection,
                "message": f"Nenhum documento com source contendo '{source}'",
            }
        )

    try:
        deleted = await backend.delete(collection, matched)
        logger.info(
            "manage_retriever_delete",
            extra={"collection": collection, "deleted": deleted},
        )
        return json.dumps(
            {
                "status": "deleted",
                "collection": collection,
                "deleted": deleted,
                "ids": matched,
            }
        )
    except Exception as e:
        return json.dumps({"status": "error", "error": f"Falha ao deletar: {e}"})
