"""Pipeline de ingestão/indexação RAG de ponta a ponta.

Fila assíncrona (``queue.py``) → worker de embeddings (``background.py``) →
invalidação de cache (``cache_embeddings.py``/``cache_sync.py``) → curadoria
de conhecimento pós-ingestão (``curator.py``). ``rag_ingest.py`` é o ponto de
entrada usado pelo handler HTTP de ingestão de pasta.

Lazy imports (``__getattr__``) — mesmo padrão de ``backend.mcp`` — evitam
puxar LanceDB/Cohere no import do pacote quando só um submódulo específico é
necessário.
"""

from __future__ import annotations

__all__ = [
    "BackgroundEmbeddingWorker",
    "curate_workspace_knowledge",
    "get_embedding_queue",
]


def __getattr__(name: str) -> object:
    if name == "get_embedding_queue":
        from backend.embedding.queue import get_embedding_queue

        return get_embedding_queue
    if name == "BackgroundEmbeddingWorker":
        from backend.embedding.background import BackgroundEmbeddingWorker

        return BackgroundEmbeddingWorker
    if name == "curate_workspace_knowledge":
        from backend.embedding.curator import curate_workspace_knowledge

        return curate_workspace_knowledge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
