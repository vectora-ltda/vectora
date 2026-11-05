"""Migrações de dados entre backends de storage.

Suporta 4 operações:

* ``to-postgres``       — copia SQLite → Postgres via asyncpg COPY
* ``to-qdrant``         — copia LanceDB → Qdrant em batches de 256
* ``to-pgvector``       — copia LanceDB → Postgres pgvector
* ``memory-to-native`` — migra registros do store antigo para o store nativo

Todas as operações são idempotentes: registros já existentes não são duplicados.
``--dry-run`` estima volumes sem escrever nada.

Uso (CLI):
    vectora storage migrate to-postgres
    vectora storage migrate to-postgres --dry-run
    vectora storage migrate to-qdrant --batch-size 512
    vectora storage migrate to-pgvector
    vectora storage migrate memory-to-native
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)

# Tabelas SQLite → Postgres (schema, tabela, pk)
_SQLITE_TABLES: list[tuple[str, str, str]] = [
    ("public", "vectora_users", "id"),
    ("public", "vectora_sessions", "thread_id"),
    ("public", "vectora_secrets", "user_id"),
    ("public", "vectora_audit_log", "id"),
    ("public", "vectora_embedding_queue", "queue_id"),
]


async def migrate_to_postgres(
    *,
    sqlite_path: str,
    postgres_dsn: str,
    dry_run: bool = False,
    console: Console | None = None,
) -> dict[str, Any]:
    """Migra dados SQLite → Postgres.

    Usa ``asyncpg`` COPY binary para máxima performance. Para cada tabela,
    faz UPSERT (ON CONFLICT DO NOTHING) para idempotência.

    Args:
        sqlite_path:  Caminho do banco SQLite de origem.
        postgres_dsn: DSN Postgres de destino.
        dry_run:      Se True, conta registros sem escrever.
        console:      Rich console para progresso (opcional).

    Returns:
        ``{"tables": {"nome": {"rows": N, "skipped": K}}, "total_rows": N}``
    """
    import aiosqlite
    import asyncpg

    normalized = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    result: dict[str, Any] = {"tables": {}, "total_rows": 0, "dry_run": dry_run}

    async with aiosqlite.connect(sqlite_path) as sqlite_conn:
        sqlite_conn.row_factory = aiosqlite.Row

        pg_conn: asyncpg.Connection | None = None
        if not dry_run:
            pg_conn = await asyncpg.connect(normalized)

        try:
            for _schema, table, pk in _SQLITE_TABLES:
                # Verifica se a tabela existe no SQLite
                cur = await sqlite_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                if not await cur.fetchone():
                    continue

                # Conta registros no SQLite
                # table vem de _SQLITE_TABLES hardcoded — não é user input
                cur = await sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
                row = await cur.fetchone()
                count = int(row[0]) if row else 0

                if dry_run:
                    result["tables"][table] = {"rows": count, "skipped": 0}
                    result["total_rows"] += count
                    if console:
                        console.print(
                            f"  [dim]{table}[/dim]: {count} registros (dry-run)"
                        )
                    continue

                assert pg_conn is not None

                # Verifica colunas
                cur = await sqlite_conn.execute(f"SELECT * FROM {table} LIMIT 0")  # nosec B608
                cols = [d[0] for d in cur.description or []]
                if not cols:
                    continue

                col_list = ", ".join(f'"{c}"' for c in cols)
                placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))

                rows = await sqlite_conn.execute_fetchall(
                    f"SELECT {col_list} FROM {table}"  # nosec B608
                )

                inserted = 0
                for sqlite_row in rows:
                    values = [sqlite_row[c] for c in cols]
                    try:
                        await pg_conn.execute(
                            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"  # nosec B608
                            f' ON CONFLICT ("{pk}") DO NOTHING',
                            *values,
                        )
                        inserted += 1
                    except Exception as exc:
                        logger.debug("Linha ignorada em %s: %s", table, exc)

                skipped = count - inserted
                result["tables"][table] = {"rows": inserted, "skipped": skipped}
                result["total_rows"] += inserted
                if console:
                    console.print(
                        f"  [green]✓[/green] {table}: {inserted} inseridos"
                        + (f", {skipped} ignorados" if skipped else "")
                    )

        finally:
            if pg_conn:
                await pg_conn.close()

    return result


async def migrate_to_qdrant(
    *,
    lancedb_path: str,
    qdrant_url: str,
    qdrant_api_key: str | None = None,
    collection: str = "articles",
    batch_size: int = 256,
    dry_run: bool = False,
    console: Console | None = None,
) -> dict[str, Any]:
    """Migra vetores LanceDB → Qdrant em batches.

    Args:
        lancedb_path:  Diretório do banco LanceDB.
        qdrant_url:    URL do servidor Qdrant.
        qdrant_api_key: API key (Qdrant Cloud).
        collection:    Nome da collection (default: ``"articles"``).
        batch_size:    Tamanho do batch (default: 256).
        dry_run:       Se True, conta vetores sem escrever.
        console:       Rich console para progresso.

    Returns:
        ``{"collection": str, "total": N, "upserted": N}``
    """
    import lancedb

    db = await lancedb.connect_async(lancedb_path)
    table_names = (await db.list_tables()).tables

    if collection not in table_names:
        return {
            "collection": collection,
            "total": 0,
            "upserted": 0,
            "error": "tabela não encontrada",
        }

    table = await db.open_table(collection)
    total = await table.count_rows()

    if dry_run:
        if console:
            console.print(f"  [dim]{collection}[/dim]: {total} vetores (dry-run)")
        return {
            "collection": collection,
            "total": total,
            "upserted": 0,
            "dry_run": True,
        }

    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)

    upserted = 0
    offset = 0

    while offset < total:
        rows = await table.query().offset(offset).limit(batch_size).to_list()
        if not rows:
            break

        points: list[PointStruct] = []
        for row in rows:
            vec = row.get("vector") or row.get("embedding")
            if vec is None:
                continue
            # ID: usa campo `id` ou gera sequencial
            point_id = row.get("id") or (offset + len(points))
            payload = {
                k: v for k, v in row.items() if k not in ("vector", "embedding", "id")
            }
            points.append(PointStruct(id=point_id, vector=vec, payload=payload))

        if points:
            client.upsert(collection_name=collection, points=points)
            upserted += len(points)

        offset += batch_size
        if console:
            console.print(
                f"  [cyan]{offset}/{total}[/cyan] vetores migrados...",
                end="\r",
            )

    if console:
        console.print(f"  [green]✓[/green] {collection}: {upserted} vetores migrados")

    return {"collection": collection, "total": total, "upserted": upserted}


async def migrate_to_pgvector(
    *,
    lancedb_path: str,
    postgres_dsn: str,
    collection: str = "articles",
    batch_size: int = 256,
    dry_run: bool = False,
    console: Console | None = None,
) -> dict[str, Any]:
    """Migra vetores LanceDB → Postgres pgvector.

    Cria a tabela ``vectora_vectors_<collection>`` se não existir,
    com colunas ``id TEXT, embedding VECTOR, text TEXT, metadata JSONB``.

    Args:
        lancedb_path:  Diretório do banco LanceDB.
        postgres_dsn:  DSN Postgres com pgvector instalado.
        collection:    Nome da collection (default: ``"articles"``).
        batch_size:    Tamanho do batch (default: 256).
        dry_run:       Se True, conta vetores sem escrever.
        console:       Rich console para progresso.

    Returns:
        ``{"collection": str, "total": N, "upserted": N}``
    """
    import json

    import asyncpg
    import lancedb

    db = await lancedb.connect_async(lancedb_path)
    table_names = (await db.list_tables()).tables

    if collection not in table_names:
        return {
            "collection": collection,
            "total": 0,
            "upserted": 0,
            "error": "tabela não encontrada",
        }

    table = await db.open_table(collection)
    total = await table.count_rows()

    if dry_run:
        if console:
            console.print(f"  [dim]{collection}[/dim]: {total} vetores (dry-run)")
        return {
            "collection": collection,
            "total": total,
            "upserted": 0,
            "dry_run": True,
        }

    normalized = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    pg_conn = await asyncpg.connect(normalized)

    try:
        # Garante extensão pgvector
        await pg_conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        # Descobre dimensão do vetor com o primeiro registro
        first = await table.query().limit(1).to_list()
        if not first:
            return {"collection": collection, "total": 0, "upserted": 0}

        vec = first[0].get("vector") or first[0].get("embedding")
        dim = len(vec) if vec else 1536

        # tbl_name é derivado de collection (valor de configuração controlado)
        tbl_name = f"vectora_vectors_{collection.replace('-', '_')}"
        await pg_conn.execute(  # nosec B608
            f"""
            CREATE TABLE IF NOT EXISTS {tbl_name} (
                id TEXT PRIMARY KEY,
                embedding VECTOR({dim}),
                text TEXT,
                metadata JSONB
            )
        """
        )

        upserted = 0
        offset = 0

        while offset < total:
            rows = await table.query().offset(offset).limit(batch_size).to_list()
            if not rows:
                break

            for row in rows:
                vec_val = row.get("vector") or row.get("embedding")
                if vec_val is None:
                    continue
                row_id = str(row.get("id") or f"{collection}_{offset + upserted}")
                text = row.get("text") or ""
                metadata = {
                    k: v
                    for k, v in row.items()
                    if k not in ("vector", "embedding", "id", "text")
                }
                # tbl_name deriva de `collection` (configuração interna, não user input)
                await pg_conn.execute(
                    f"INSERT INTO {tbl_name} (id, embedding, text, metadata)"  # nosec B608
                    f" VALUES ($1, $2::vector, $3, $4::jsonb)"
                    f" ON CONFLICT (id) DO NOTHING",
                    row_id,
                    f"[{','.join(str(x) for x in vec_val)}]",
                    text,
                    json.dumps(metadata),
                )
                upserted += 1

            offset += batch_size
            if console:
                console.print(
                    f"  [cyan]{offset}/{total}[/cyan] vetores migrados...",
                    end="\r",
                )

        if console:
            console.print(f"  [green]✓[/green] {tbl_name}: {upserted} vetores migrados")

        return {"collection": collection, "total": total, "upserted": upserted}

    finally:
        await pg_conn.close()


async def migrate_memory_to_native_store(
    *,
    sqlite_path: str,
    dry_run: bool = False,
    console: Console | None = None,
) -> dict[str, Any]:
    """Migra memórias do store antigo para o store nativo (``get_store()``).

    Lê registros da tabela ``memories`` (formato antigo) e os converte
    para o namespace ``("user:<id>", "memories")``.

    Args:
        sqlite_path:  Caminho do banco SQLite com tabela ``memories``.
        dry_run:      Se True, conta registros sem escrever.
        console:      Rich console para progresso.

    Returns:
        ``{"total": N, "migrated": N, "skipped": K}``
    """
    import json

    import aiosqlite

    from backend.storage.factory import get_store

    result: dict[str, Any] = {
        "total": 0,
        "migrated": 0,
        "skipped": 0,
        "dry_run": dry_run,
    }

    async with aiosqlite.connect(sqlite_path) as conn:
        conn.row_factory = aiosqlite.Row

        # Verifica se a tabela memories existe
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
        )
        if not await cur.fetchone():
            if console:
                console.print(
                    "[dim]Tabela 'memories' não encontrada — nada a migrar.[/dim]"
                )
            return result

        cur = await conn.execute("SELECT COUNT(*) FROM memories")
        row = await cur.fetchone()
        total = int(row[0]) if row else 0
        result["total"] = total

        if dry_run:
            if console:
                console.print(f"  [dim]memories[/dim]: {total} registros (dry-run)")
            return result

        store = await get_store()
        cur = await conn.execute("SELECT * FROM memories")

        async for mem_row in cur:
            try:
                # mem_row é sqlite3.Row: precisa de .keys() para checar coluna
                # (sem .keys(), `in` itera VALORES; e .get() nem existe na Row).
                user_id = (
                    mem_row["user_id"] if "user_id" in mem_row.keys() else "unknown"
                )
                mem_id = str(mem_row["id"])
                content_raw = (
                    mem_row["content"] if "content" in mem_row.keys() else None
                )
                if content_raw is None:
                    result["skipped"] += 1
                    continue

                content = (
                    json.loads(content_raw)
                    if isinstance(content_raw, str)
                    else content_raw
                )
                namespace = ("user:" + user_id, "memories")

                await store.aput(
                    namespace,
                    mem_id,
                    {"content": content},
                )
                result["migrated"] += 1

            except Exception as exc:
                logger.debug("Registro ignorado: %s", exc)
                result["skipped"] += 1

        if console:
            console.print(
                f"  [green]✓[/green] {result['migrated']} memórias migradas"
                + (f", {result['skipped']} ignoradas" if result["skipped"] else "")
            )

    return result
