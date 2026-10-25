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
import lancedb.db
import pyarrow as pa

from backend.storage.lancedb.index import create_fts_index
from backend.storage.vectorstore.base import (
    VectorHit,
    VectorRow,
    VectorStoreHealth,
)

logger = logging.getLogger(__name__)


def _parse_metadata(raw: object) -> dict[str, Any]:
    """Executa a operação parse metadata."""
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
        """Inicializa o estado da instância."""
        self._path = str(lancedb_dir)
        self._write_semaphore = write_semaphore or asyncio.Semaphore(1)
        self._legacy_checked: set[str] = set()

    async def _db(self) -> lancedb.db.AsyncConnection:
        """Executa a operação db."""
        return await lancedb.connect_async(self._path)

    async def _list_table_names(self, db: lancedb.db.AsyncConnection) -> list[str]:
        """Compatibilidade entre versões do cliente LanceDB."""
        try:
            response = await db.list_tables()
            return [str(name) for name in response.tables]
        except Exception:
            return [str(name) for name in await db.table_names()]

    async def search(
        self, collection: str, query_vector: list[float], limit: int
    ) -> list[VectorHit]:
        """Executa a operação search."""
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
        """Executa a operação search text."""
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

    async def _repair_legacy_duplicates(
        self,
        table: lancedb.table.AsyncTable,
        incoming_ids: set[str],
        docs: list[dict[str, Any]],
    ) -> None:
        """Remove duplicate legacy IDs and stage retained rows for the merge.

        Legacy tables can contain more than one row for an ID because older
        writers did not enforce uniqueness.  The repair is deliberately kept
        separate from the normal write path: it runs once per collection and
        reads the full table only when duplicate IDs are actually found.
        """
        id_frame = await table.query().select(["id"]).to_pandas()
        if "id" not in id_frame.columns:
            return
        duplicated_ids = (
            id_frame[id_frame["id"].duplicated(keep=False)]["id"]
            .drop_duplicates()
            .tolist()
        )
        if not duplicated_ids:
            return

        existing = await table.to_pandas()
        for raw_id in duplicated_ids:
            row_id = str(raw_id)
            escaped_id = row_id.replace("'", "''")
            # A failed delete is allowed to abort the whole upsert.  Continuing
            # would make the merge operate on an unknown mixture of old rows.
            await table.delete(f"id = '{escaped_id}'")
            if row_id not in incoming_ids:
                retained = existing[existing["id"] == raw_id].iloc[-1]
                docs.append(
                    {
                        "id": row_id,
                        "vector": list(retained["vector"]),
                        "text": str(retained.get("text", "")),
                        "metadata": str(retained.get("metadata", "{}") or "{}"),
                    }
                )

    async def upsert(self, collection: str, rows: list[VectorRow]) -> None:
        """Executa a operação upsert."""
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
            # Repare dados legados uma única vez por coleção/instância. A
            # primeira leitura busca somente IDs; linhas completas só são
            # carregadas quando há duplicatas para reparar antes do merge.
            # Idempotência: merge_insert por 'id' atualiza os existentes e insere os novos,
            # eliminando registros duplicados.
            if collection not in self._legacy_checked:
                await self._repair_legacy_duplicates(table, set(deduped), docs)
            await (
                table.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(docs)
            )
            # Mark only after both the repair and the write succeeded.  A
            # failed merge must be retried instead of silently skipping the
            # legacy repair on the next call.
            self._legacy_checked.add(collection)

    async def list_rows(self, collection: str) -> list[VectorRow]:
        """Executa a operação list rows."""
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
        """Executa a operação delete."""
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
        """Executa a operação purge."""
        db = await self._db()
        # coleção já não existe — purge é idempotente
        with contextlib.suppress(Exception):
            await db.drop_table(collection)

    async def list_collections(self) -> list[str]:
        """Executa a operação list collections."""
        db = await self._db()
        try:
            return await self._list_table_names(db)
        except Exception:
            logger.exception("LanceDBBackend.list_collections: falha ao listar tabelas")
            return []

    async def count(self, collection: str) -> int | None:
        """Executa a operação count."""
        db = await self._db()
        try:
            table = await db.open_table(collection)
            return await table.count_rows()
        except Exception:
            return None

    async def health(self) -> VectorStoreHealth:
        """Verifica se o LanceDB está acessível e lista tabelas."""
        try:
            db = await self._db()
            tables = await self._list_table_names(db)
            return VectorStoreHealth(ok=True, error=None, tables=tables, internal=True)
        except Exception as exc:
            return VectorStoreHealth(ok=False, error=str(exc), internal=True)
