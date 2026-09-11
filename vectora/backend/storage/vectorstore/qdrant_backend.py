"""`VectorStoreBackend` sobre Qdrant — cliente nativo `qdrant_client`.
Busca dense-only (cosine), mesma capacidade que o LanceDB já tem hoje
(nenhum dos dois faz híbrido dense+sparse) — paridade de comportamento
real entre os dois backends, não uma feature nova.

Timeout explícito em toda chamada de rede — lição do incidente do NATS
(`rustling-hatching-summit.md`): nunca uma chamada que trava pra sempre em
conexão degradada.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from qdrant_client import AsyncQdrantClient, models

from backend.storage.vectorstore.base import VectorHit, VectorRow

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10
_SCROLL_PAGE_SIZE = 256


def _point_id(collection: str, row_id: str) -> int | str:
    """Map arbitrary Vectora IDs to Qdrant's integer/UUID point-id contract."""
    try:
        UUID(row_id)
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"vectora:{collection}:{row_id}"))
    return row_id


class QdrantBackend:
    """`storage_mode="complete"` — servidor Qdrant real."""

    def __init__(self, url: str, api_key: str | None = None):
        self._url = url
        self._api_key = api_key
        self._client: AsyncQdrantClient | None = None

    def _get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                url=self._url, api_key=self._api_key, timeout=_TIMEOUT_S
            )
        return self._client

    async def _ensure_collection(self, collection: str, dim: int) -> None:
        client = self._get_client()
        async with asyncio.timeout(_TIMEOUT_S):
            exists = await client.collection_exists(collection)
        if exists:
            return
        async with asyncio.timeout(_TIMEOUT_S):
            await client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(
                    size=dim, distance=models.Distance.COSINE
                ),
            )
        logger.info("QdrantBackend: coleção criada", extra={"collection": collection})

    async def search(
        self, collection: str, query_vector: list[float], limit: int
    ) -> list[VectorHit]:
        client = self._get_client()
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                if not await client.collection_exists(collection):
                    return []
                response = await client.query_points(
                    collection_name=collection,
                    query=query_vector,
                    limit=limit,
                    with_payload=True,
                )
        except TimeoutError:
            logger.warning("QdrantBackend.search: timeout na coleção %s", collection)
            return []
        except Exception:
            logger.debug(
                "QdrantBackend.search: coleção %s indisponível",
                collection,
                exc_info=True,
            )
            return []

        hits: list[VectorHit] = []
        for point in response.points:
            payload = point.payload or {}
            # Qdrant devolve similaridade cosine (1.0 = idêntico, maior é
            # melhor) — convertida pra "distância" (menor é melhor) pra
            # bater com a convenção do LanceDB (`_distance`), que é quem o
            # chamador (`tools/rag.py::vector_search`) já espera pra ordenar.
            distance = 1.0 - float(point.score)
            hits.append(
                VectorHit(
                    id=str(payload.get("_vectora_id", point.id)),
                    score=distance,
                    content=str(payload.get("text", "")),
                    metadata=payload.get("metadata") or {},
                    collection=collection,
                )
            )
        return hits

    async def search_text(
        self, collection: str, query: str, limit: int
    ) -> list[VectorHit]:
        client = self._get_client()
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                if not await client.collection_exists(collection):
                    return []
        except Exception:
            logger.debug(
                "QdrantBackend.search_text: coleção %s indisponível", collection
            )
            return []

        # Índice de payload full-text nativo — criado sob demanda; já
        # existente é no-op (Qdrant não recria índice igual). Necessário
        # pra `MatchText` tokenizar em vez de comparar substring cru.
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                await client.create_payload_index(
                    collection_name=collection,
                    field_name="text",
                    field_schema=models.PayloadSchemaType.TEXT,
                )
        except Exception:
            logger.debug(
                "QdrantBackend.search_text: create_payload_index no-op/falhou para %s",
                collection,
            )

        try:
            async with asyncio.timeout(_TIMEOUT_S):
                points, _next_offset = await client.scroll(
                    collection_name=collection,
                    scroll_filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="text", match=models.MatchText(text=query)
                            )
                        ]
                    ),
                    limit=max(limit * 4, limit),
                    with_payload=True,
                )
        except TimeoutError:
            logger.warning(
                "QdrantBackend.search_text: timeout na coleção %s", collection
            )
            return []
        except Exception:
            logger.debug(
                "QdrantBackend.search_text: busca textual indisponível na coleção %s",
                collection,
                exc_info=True,
            )
            return []

        # MatchText só filtra (booleano) — Qdrant não devolve relevância
        # pra full-text. Rankeamos client-side por contagem de termos da
        # query presentes no texto, só pra dar uma ORDEM determinística
        # (a fusão RRF em tools/rag.py usa só a ordem, não o valor).
        query_terms = [t for t in query.lower().split() if t]

        def _term_overlap(text: str) -> int:
            lowered = text.lower()
            return sum(lowered.count(term) for term in query_terms)

        scored = [
            (point, _term_overlap(str((point.payload or {}).get("text", ""))))
            for point in points
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)

        hits: list[VectorHit] = []
        for point, overlap in scored[:limit]:
            payload = point.payload or {}
            hits.append(
                VectorHit(
                    id=str(payload.get("_vectora_id", point.id)),
                    score=float(overlap),
                    content=str(payload.get("text", "")),
                    metadata=payload.get("metadata") or {},
                    collection=collection,
                )
            )
        return hits

    async def upsert(self, collection: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        await self._ensure_collection(collection, len(rows[0].vector))
        client = self._get_client()
        points = [
            models.PointStruct(
                id=_point_id(collection, row.id),
                vector=row.vector,
                payload={
                    "text": row.text,
                    "metadata": row.metadata,
                    "_vectora_id": row.id,
                },
            )
            for row in rows
        ]
        async with asyncio.timeout(_TIMEOUT_S):
            await client.upsert(collection_name=collection, points=points)

    async def list_rows(self, collection: str) -> list[VectorRow]:
        client = self._get_client()
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                if not await client.collection_exists(collection):
                    return []
        except Exception:
            return []

        rows: list[VectorRow] = []
        offset: Any = None
        while True:
            async with asyncio.timeout(_TIMEOUT_S):
                points, next_offset = await client.scroll(
                    collection_name=collection,
                    limit=_SCROLL_PAGE_SIZE,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
            for point in points:
                payload = point.payload or {}
                # Config sempre usa vetor único (dense) sem nome — nunca
                # multi-vetor nomeado — então a forma sempre é list[float].
                raw_vector = point.vector
                vector: list[float] = raw_vector if isinstance(raw_vector, list) else []  # ty: ignore[invalid-assignment]
                rows.append(
                    VectorRow(
                        id=str(payload.get("_vectora_id", point.id)),
                        vector=vector,
                        text=str(payload.get("text", "")),
                        metadata=payload.get("metadata") or {},
                    )
                )
            if next_offset is None:
                break
            offset = next_offset
        return rows

    async def delete(self, collection: str, ids: list[str]) -> int:
        if not ids:
            return 0
        client = self._get_client()
        async with asyncio.timeout(_TIMEOUT_S):
            await client.delete(
                collection_name=collection,
                points_selector=models.PointIdsList(
                    points=[_point_id(collection, row_id) for row_id in ids]
                ),
            )
        return len(ids)

    async def purge(self, collection: str) -> None:
        client = self._get_client()
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                await client.delete_collection(collection)
        except Exception:
            pass  # coleção já não existe — purge é idempotente

    async def list_collections(self) -> list[str]:
        client = self._get_client()
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                response = await client.get_collections()
        except Exception:
            logger.warning("QdrantBackend.list_collections: falha", exc_info=True)
            return []
        return [c.name for c in response.collections]

    async def count(self, collection: str) -> int | None:
        client = self._get_client()
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                result = await client.count(collection_name=collection, exact=True)
            return result.count
        except Exception:
            return None
