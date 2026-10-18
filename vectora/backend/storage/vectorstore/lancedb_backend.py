"""`VectorStoreBackend` sobre LanceDB — extrai a lógica nativa que já existia
espalhada em `tools/rag.py`/`embedding/background.py` (refactor de
organização; a lógica em si já era 100% cliente nativo `lancedb`)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, cast

import lancedb
import pyarrow as pa

from backend.storage.lancedb.index import create_fts_index
from backend.storage.vectorstore.base import VectorHit, VectorRow

logger = logging.getLogger(__name__)


def _parse_metadata(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return cast("dict[str, Any]", raw)
    try:
        parsed = json.loads(str(raw or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _row_vector(raw: Any) -> list[float]:
    """Extrai o vetor de uma linha do `DataFrame` (`row.get("vector")`).

    `pandas`/`pyarrow` devolvem a coluna de embedding como `numpy.ndarray`
    — `array or []` explode com "truth value of an array with more than
    one element is ambiguous" porque `or` avalia truthiness do operando,
    e `ndarray.__bool__` só aceita 0 ou 1 elemento. Linha sem vetor (coluna
    ausente) vem como escalar `NaN` (`float`), não `None`.
    """
    if raw is None or isinstance(raw, float):
        return []
    return list(raw)


class LanceDBBackend:
    """`storage_mode="lite"` — arquivo local, sem servidor."""

    def __init__(
        self, lancedb_dir: str, write_semaphore: asyncio.Semaphore | None = None
    ):
        self._path = str(lancedb_dir)
        self._write_semaphore = write_semaphore or asyncio.Semaphore(1)

    async def _db(self) -> Any:
        return await lancedb.connect_async(self._path)

    async def search(
        self, collection: str, query_vector: list[float], limit: int
    ) -> list[VectorHit]:
        db = await self._db()
        try:
            async with asyncio.timeout(10):
                table = await db.open_table(collection)
        except Exception:
            logger.debug("LanceDBBackend.search: coleção %s indisponível", collection)
            return []
        try:
            async with asyncio.timeout(10):
                results = await (
                    table.vector_search(query_vector).limit(limit).to_pandas()
                )
        except TimeoutError:
            logger.warning("LanceDBBackend.search: timeout na coleção %s", collection)
            return []
        return [
            VectorHit(
                id=str(row["id"]),
                score=float(row.get("_distance", 0.0)),
                content=row["text"],
                metadata=_parse_metadata(row.get("metadata", "{}")),
                collection=collection,
            )
            for _, row in results.iterrows()
        ]

    async def search_text(
        self, collection: str, query: str, limit: int
    ) -> list[VectorHit]:
        db = await self._db()
        try:
            async with asyncio.timeout(10):
                table = await db.open_table(collection)
        except Exception:
            logger.debug(
                "LanceDBBackend.search_text: coleção %s indisponível", collection
            )
            return []
        # Índice FTS nativo (tantivy) — criado sob demanda na primeira busca
        # textual da coleção; `replace=False` faz chamadas seguintes serem
        # no-op quando o índice já existe (nunca reconstrói à toa).
        async with asyncio.timeout(10):
            await create_fts_index(table, "text", replace=False)
        try:
            async with asyncio.timeout(10):
                fts_query = await table.search(query, query_type="fts")
                results = await fts_query.limit(limit).to_pandas()
        except Exception:
            logger.debug(
                "LanceDBBackend.search_text: FTS indisponível na coleção %s",
                collection,
                exc_info=True,
            )
            return []
        return [
            VectorHit(
                id=str(row["id"]),
                score=float(row.get("_score", 0.0)),
                content=row["text"],
                metadata=_parse_metadata(row.get("metadata", "{}")),
                collection=collection,
            )
            for _, row in results.iterrows()
        ]

    async def upsert(self, collection: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        db = await self._db()
        # Deduplica rows mantendo a última ocorrência de cada id
        deduped: dict[str, VectorRow] = {row.id: row for row in rows}
        unique_rows = list(deduped.values())
        dim = len(unique_rows[0].vector)
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
                pa.field("text", pa.string()),
                pa.field("metadata", pa.string()),
            ]
        )
        docs = [
            {
                "id": row.id,
                "vector": row.vector,
                "text": row.text,
                "metadata": json.dumps(row.metadata) if row.metadata else "{}",
            }
            for row in unique_rows
        ]

        # Abrir-ou-criar sob o mesmo semaphore: com várias tasks escrevendo no
        # mesmo collection, dois open_table podem falhar juntos e ambos
        # tentar create_table → "Table already exists". Serializar elimina a
        # corrida; se mesmo assim colidir, reabre.
        async with self._write_semaphore:
            try:
                table = await db.open_table(collection)
            except Exception:
                try:
                    table = await db.create_table(collection, schema=schema)
                    logger.info(
                        "LanceDBBackend: tabela criada",
                        extra={"collection": collection},
                    )
                except Exception:
                    table = await db.open_table(collection)
            # Idempotência: merge_insert por 'id' atualiza os existentes e insere os novos,
            # eliminando registros duplicados.
            await (
                table.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(docs)
            )

    async def list_rows(self, collection: str) -> list[VectorRow]:
        db = await self._db()
        try:
            async with asyncio.timeout(10):
                table = await db.open_table(collection)
        except Exception:
            return []
        try:
            async with asyncio.timeout(15):
                df = await table.to_pandas()
        except Exception:
            return []
        if "id" not in df.columns:
            return []
        return [
            VectorRow(
                id=str(row["id"]),
                vector=_row_vector(row.get("vector")),
                text=str(row.get("text", "")),
                metadata=_parse_metadata(row.get("metadata", "{}")),
            )
            for _, row in df.iterrows()
        ]

    async def delete(self, collection: str, ids: list[str]) -> int:
        if not ids:
            return 0
        db = await self._db()
        try:
            table = await db.open_table(collection)
        except Exception:
            return 0
        async with self._write_semaphore:
            before_count = await table.count_rows()
            escaped_ids = [i.replace("'", "''") for i in ids]
            id_list = ", ".join(f"'{i}'" for i in escaped_ids)
            await table.delete(f"id IN ({id_list})")
            after_count = await table.count_rows()
            return max(0, before_count - after_count)

    async def purge(self, collection: str) -> None:
        db = await self._db()
        # coleção já não existe — purge é idempotente
        with contextlib.suppress(Exception):
            await db.drop_table(collection)

    async def list_collections(self) -> list[str]:
        db = await self._db()
        try:
            names = (await db.list_tables()).tables
        except Exception:
            names = await db.table_names()
        return [str(name) for name in names]

    async def count(self, collection: str) -> int | None:
        db = await self._db()
        try:
            table = await db.open_table(collection)
            return await table.count_rows()
        except Exception:
            return None

    async def health(self) -> dict[str, Any]:
        """Verifica se o LanceDB está acessível e lista tabelas."""
        try:
            tables = await self.list_collections()
            return {"ok": True, "error": None, "tables": tables, "internal": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "internal": True}
