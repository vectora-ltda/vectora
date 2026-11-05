"""Construção do ``VectoraStore`` (memórias/skills, nativo aiosqlite) usado
pelo motor de conversa nativo.

``build_store()`` abre um pool aiosqlite dedicado e resolve o índice vetorial
via ``_build_lc_embeddings()`` (fallback Cohere↔Voyage↔Ollama↔OpenRouter).
``_resolve_workspace_root()`` resolve o diretório raiz de uma workspace pelo
registry, com fallback para o home do usuário.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.persistence.native.store import VectoraStore

logger = logging.getLogger(__name__)


async def build_store(embedding_model: str | None = None) -> VectoraStore:
    """Constrói o ``VectoraStore`` (nativo, aiosqlite) persistente com
    embeddings opcionais (Cohere↔Voyage↔Ollama↔OpenRouter, via
    ``_build_lc_embeddings()``).

    Abre um ``AsyncConnectionPool`` dedicado (mesmos PRAGMAs de hardening de
    ``backend/storage/sqlite/pool.py`` — WAL, busy_timeout, etc.) e cria
    ``VectoraStore(pool, index=index)``. Chama ``await store.setup()`` para
    criar as tabelas na primeira vez.

    Usado pelas tools nativas de memória/skill (``backend/tools/memory.py``)
    resolvidas via ``ToolContext``. O store persiste memórias do agente em
    SQLite (lite mode).

    Args:
        embedding_model: não usado atualmente — ``_build_lc_embeddings()``
            resolve o provider/modelo pelos settings globais e de runtime,
            sem aceitar override por chamada. Mantido na assinatura por
            compatibilidade; nenhum call-site real passa esse argumento hoje.

    Returns:
        ``VectoraStore`` pronto para ser consumido pelas tools nativas de
        memória/skill (``backend/tools/memory.py``) via ``ToolContext``.

    Nota:
        Usa um pool próprio (não o pool F1 do checkpointer) porque o Store
        mantém a conexão aberta pelo ciclo de vida do processo, e o F1
        é reservado para o checkpointer (acesso transacional curto).
    """
    from backend.persistence.native.store import VectoraStore
    from backend.storage.sqlite.pool import AsyncConnectionPool

    index = _build_index(embedding_model)

    try:
        from backend.settings import settings as _settings

        db_path = _settings.db_dsn or ""
    except Exception:
        db_path = ""

    if not db_path:
        import tempfile

        db_path = str(Path(tempfile.gettempdir()) / "vectora_store.db")
        logger.debug("backends: store db_path fallback para %s", db_path)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    pool = AsyncConnectionPool(db_path, min_size=1, max_size=4)
    await pool.open()

    store = VectoraStore(pool, index=index)
    await store.setup()

    logger.debug(
        "backends: VectoraStore criado path=%s index=%s",
        db_path,
        "com embeddings" if index else "none",
    )
    return store


def _build_index(embedding_model: str | None) -> Any:
    """Constrói IndexConfig do Store via ``_build_lc_embeddings()`` — honra o
    fallback multi-provider (Cohere↔Voyage↔Ollama↔OpenRouter) em vez de ficar
    hardcoded em Cohere. Retorna None se nenhum provider estiver configurado.

    ``dims=1024`` continua fixo: o índice do Store exige a dimensão na
    construção (síncrona), antes de qualquer chamada de rede pra sondar a
    dimensão real do provider resolvido. Guard de dimensão real
    (`_check_embedding_dimension`) só é viável no caminho async de
    escrita/leitura do vector store de RAG (`storage/factory.py`), não
    neste índice síncrono do Store.
    """
    try:
        from backend.storage.factory import _build_lc_embeddings

        _embeddings = _build_lc_embeddings()
        if _embeddings is None:
            return None

        async def _embed(texts: list[str]) -> list[list[float]]:
            return await _embeddings.aembed_documents(texts)

        return {"dims": 1024, "embed": _embed, "fields": ["content"]}
    except Exception:
        logger.debug(
            "backends: embedding indisponível — InMemoryStore sem índice vetorial",
            exc_info=True,
        )
        return None


def _resolve_workspace_root(workspace_id: str | None) -> Path:
    """Resolve o path raiz do filesystem backend para o workspace.

    Busca o workspace no registry; se não encontrar, usa o diretório
    de workspaces padrão (~/.vectora/workspaces/<workspace_id>).
    Fallback para o home do usuário se workspace_id for None/vazio.
    """
    if not workspace_id:
        return Path.home()

    try:
        from backend.workspace.workspace import workspace_registry

        ws = workspace_registry.get(workspace_id)
        if ws and ws.cwd:
            return Path(ws.cwd)
    except Exception:
        pass

    # Fallback: diretório padrão de workspaces
    from backend.settings import settings

    default_path = settings.vectora_home / "workspaces" / workspace_id
    if default_path.exists():
        return default_path

    return Path.home()
