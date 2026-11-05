"""Cache de embeddings ``hash(text + model) → vetor`` em Redis (TTL 24h).

Ativo apenas em modo complete com Redis acessível (``_redis_active()``). Em lite
o cache fica desativado e o embedding é recalculado a cada chamada — evita um
dicionário em memória sem limite de tamanho.

Uso::

    from backend.embedding.cache_embeddings import embed_query_cached
    vec = await embed_query_cached(query, settings.embedding_model,
                                   embeddings_model.embed_query)
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_TTL_SECONDS = 24 * 60 * 60  # 24h


def _key(text: str, model: str, *, kind: str) -> str:
    digest = hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()
    return f"emb:{kind}:{model}:{digest}"


def _redis_active() -> bool:
    from backend.settings import settings

    url = (settings.redis_url or "").strip()
    if settings.storage_mode != "complete" or not url:
        return False
    from backend.persistence.kv import redis_reachable

    return redis_reachable(url)


async def get_cached(
    text: str, model: str, *, kind: str = "query"
) -> list[float] | None:
    """Vetor em cache para ``text``/``model`` ou ``None`` (miss / cache inativo)."""
    if not _redis_active():
        return None
    from backend.persistence.kv import get_kv

    try:
        kv = await get_kv()
        raw = await kv.get(_key(text, model, kind=kind))
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def put_cached(
    text: str, model: str, vector: list[float], *, kind: str = "query"
) -> None:
    """Grava o vetor no cache (no-op se cache inativo). Falhas são ignoradas."""
    if not _redis_active():
        return
    from backend.persistence.kv import get_kv

    try:
        kv = await get_kv()
        await kv.set(
            _key(text, model, kind=kind), json.dumps(vector), ttl_s=_TTL_SECONDS
        )
    except Exception:
        logger.debug("cache_embeddings: falha ao gravar (ignorado)")


async def embed_query_cached(
    text: str,
    model: str,
    embed_fn: Callable[[str], list[float] | Awaitable[list[float]]],
) -> list[float]:
    """Embedding de ``text`` com cache Redis quando disponível.

    ``embed_fn`` pode ser síncrona ou assíncrona; só é chamada em caso de miss.
    """
    cached = await get_cached(text, model, kind="query")
    if cached is not None:
        logger.debug("cache_embeddings: hit (query)")
        return cached
    vector_or_awaitable = embed_fn(text)
    vector = (
        await vector_or_awaitable
        if inspect.isawaitable(vector_or_awaitable)
        else vector_or_awaitable
    )
    await put_cached(text, model, vector, kind="query")
    return vector
