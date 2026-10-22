"""``VectoraPostgresStore`` — armazenamento persistente de memórias/skills do
agente sobre ``asyncpg``.

Mesma lógica de ``backend/persistence/native/store.py::VectoraStore``
(SQLite) — só o transporte SQL muda (``$1``/``$2``, ``JSONB``). Implementa
``backend/storage/protocols.py::StoreBackend`` diretamente.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.persistence.native.store import (
    Item,
    SearchItem,
    _cosine_similarity,
    _matches_filter,
    _ns_from_str,
    _ns_to_str,
    get_text_at_path,
    is_store_value_expired,
)

if TYPE_CHECKING:
    import asyncpg

    from backend.storage.protocols import HealthResult

_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS vectora_store_items (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    embedding JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_vectora_store_items_namespace
    ON vectora_store_items(namespace);
"""


class VectoraPostgresStore:
    """Store async sobre ``asyncpg.Pool``. Mesmo shape de ``index`` que
    ``VectoraStore`` aceita."""

    def __init__(
        self, pool: asyncpg.Pool, *, index: dict[str, Any] | None = None
    ) -> None:
        self._pool = pool
        self._index = index
        self._is_setup = False

    async def setup(self) -> None:
        if self._is_setup:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(_SETUP_SQL)
        self._is_setup = True

    async def aget(self, namespace: tuple[str, ...], key: str) -> Item | None:
        await self.setup()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value, created_at, updated_at FROM vectora_store_items "
                "WHERE namespace = $1 AND key = $2",
                _ns_to_str(namespace),
                key,
            )
        if row is None:
            return None
        value = row["value"]
        value = json.loads(value) if isinstance(value, str) else value
        if is_store_value_expired(value):
            return None
        return Item(
            value=value,
            key=key,
            namespace=namespace,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def aput(
        self, namespace: tuple[str, ...], key: str, value: dict[str, Any]
    ) -> None:
        await self.setup()
        namespace_str = _ns_to_str(namespace)
        async with self._pool.acquire() as conn:
            embedding_json = await self._embed_for_index(value)

            now = datetime.now(UTC)
            await conn.execute(
                "INSERT INTO vectora_store_items "
                "(namespace, key, value, embedding, created_at, updated_at) "
                "VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $5) "
                "ON CONFLICT (namespace, key) DO UPDATE SET "
                "value = EXCLUDED.value, embedding = EXCLUDED.embedding, "
                "updated_at = EXCLUDED.updated_at",
                namespace_str,
                key,
                json.dumps(value, ensure_ascii=False),
                embedding_json,
                now,
            )

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM vectora_store_items WHERE namespace = $1 AND key = $2",
                _ns_to_str(namespace),
                key,
            )

    async def asearch(
        self,
        namespace: tuple[str, ...],
        *,
        query: str | None = None,
        limit: int = 10,
        filter: dict[str, Any] | None = None,  # noqa: A002 — mesmo nome do protocolo
        offset: int = 0,
    ) -> list[SearchItem]:
        await self.setup()
        prefix = _ns_to_str(namespace)
        async with self._pool.acquire() as conn:
            if prefix:
                rows = await conn.fetch(
                    "SELECT namespace, key, value, embedding, created_at, updated_at "
                    "FROM vectora_store_items WHERE namespace = $1 OR namespace LIKE $2",
                    prefix,
                    f"{prefix}\x1e%",
                )
            else:
                rows = await conn.fetch(
                    "SELECT namespace, key, value, embedding, created_at, updated_at "
                    "FROM vectora_store_items"
                )

        query_vector = await self._embed_query(query)

        candidates: list[tuple[float | None, SearchItem]] = []
        for row in rows:
            value = row["value"]
            value = json.loads(value) if isinstance(value, str) else value
            if is_store_value_expired(value):
                continue
            if not _matches_filter(value, filter):
                continue
            embedding = row["embedding"]
            score: float | None = None
            if query_vector is not None and embedding:
                embedding_vec = (
                    json.loads(embedding) if isinstance(embedding, str) else embedding
                )
                score = _cosine_similarity(query_vector, embedding_vec)
            elif query_vector is not None:
                continue
            candidates.append(
                (
                    score,
                    SearchItem(
                        namespace=_ns_from_str(row["namespace"]),
                        key=row["key"],
                        value=value,
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        score=score,
                    ),
                )
            )

        if query_vector is not None:
            candidates.sort(key=lambda pair: pair[0] or 0.0, reverse=True)

        page = candidates[offset : offset + limit]
        return [item for _, item in page]

    async def health(self) -> HealthResult:
        from backend.storage.protocols import _err, _ok

        try:
            await self.setup()
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    async def _embed_for_index(self, value: dict[str, Any]) -> str | None:
        if self._index is None:
            return None
        fields = self._index.get("fields", ["$"])
        texts: list[str] = []
        for field in fields:
            texts.extend(get_text_at_path(value, field))
        if not texts:
            return None
        vectors = await self._index["embed"](texts)
        dims = self._index["dims"]
        avg = [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]
        return json.dumps(avg)

    async def _embed_query(self, query: str | None) -> list[float] | None:
        if not query or self._index is None:
            return None
        vectors = await self._index["embed"]([query])
        return vectors[0] if vectors else None
