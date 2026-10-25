"""``VectoraStore`` — armazenamento persistente de memórias/skills do agente
sobre ``aiosqlite``, implementando ``backend/storage/protocols.py::StoreBackend``
(``aget``/``aput``/``adelete``/``asearch``/``health``) diretamente.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.persistence.telemetry import telemetry

if TYPE_CHECKING:
    from backend.storage.protocols import HealthResult
    from backend.storage.sqlite.pool import AsyncConnectionPool

_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS store_items (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    embedding TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_store_items_namespace ON store_items(namespace);
"""

_NS_SEP = "\x1e"


def _ns_to_str(namespace: tuple[str, ...]) -> str:
    """Executa a operação  ns to str."""
    return _NS_SEP.join(namespace)


def _ns_from_str(namespace: str) -> tuple[str, ...]:
    """Executa a operação  ns from str."""
    return tuple(namespace.split(_NS_SEP)) if namespace else ()


def is_store_value_expired(
    value: dict[str, Any], *, now: datetime | None = None
) -> bool:
    """Retorna se um item persistido passou do seu ``valid_until``."""
    valid_until = value.get("valid_until")
    if not isinstance(valid_until, str) or not valid_until:
        return False
    try:
        expires_at = datetime.fromisoformat(valid_until)
        # Older callers could persist ISO timestamps without an offset. Treat
        # those values as UTC so one malformed legacy record cannot abort a
        # namespace-wide read or search.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= (now or datetime.now(UTC))
    except (TypeError, ValueError):
        return False


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Executa a operação  cosine similarity."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


_FILTER_OPERATORS = {
    "$eq": lambda v, target: v == target,
    "$ne": lambda v, target: v != target,
    "$gt": lambda v, target: v is not None and v > target,
    "$gte": lambda v, target: v is not None and v >= target,
    "$lt": lambda v, target: v is not None and v < target,
    "$lte": lambda v, target: v is not None and v <= target,
}


def _matches_filter(value: dict[str, Any], filter_: dict[str, Any] | None) -> bool:
    """Executa a operação  matches filter."""
    if not filter_:
        return True
    for key, condition in filter_.items():
        actual = value.get(key)
        if isinstance(condition, dict):
            for op, target in condition.items():
                fn = _FILTER_OPERATORS.get(op)
                if fn is None or not fn(actual, target):
                    return False
        elif actual != condition:
            return False
    return True


# ---------------------------------------------------------------------------
# Item / SearchItem — shape de retorno de aget/asearch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """Um item persistido no store — retorno de ``aget``."""

    value: dict[str, Any]
    key: str
    namespace: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SearchItem(Item):
    """Um item retornado por ``asearch`` — ``score`` presente só quando a
    busca foi semântica (índice configurado e query não vazia)."""

    score: float | None = None


# ---------------------------------------------------------------------------
# get_text_at_path — extração de texto por dot-path num dict aninhado, usada
# pra indexar embeddings de campos específicos do value armazenado.
# ---------------------------------------------------------------------------


def tokenize_path(path: str) -> list[str]:
    """Quebra um path em tokens: campo simples (``a``), índice de lista
    (``[0]``, ``[*]``, ``[-1]``, com suporte a colchetes aninhados) ou
    seleção múltipla (``{a,b}``, com suporte a chaves aninhadas)."""
    if not path:
        return []

    tokens: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(path):
        char = path[i]

        if char == "[":
            if current:
                tokens.append("".join(current))
                current = []
            bracket_count = 1
            index_chars = ["["]
            i += 1
            while i < len(path) and bracket_count > 0:
                if path[i] == "[":
                    bracket_count += 1
                elif path[i] == "]":
                    bracket_count -= 1
                index_chars.append(path[i])
                i += 1
            tokens.append("".join(index_chars))
            continue

        if char == "{":
            if current:
                tokens.append("".join(current))
                current = []
            brace_count = 1
            field_chars = ["{"]
            i += 1
            while i < len(path) and brace_count > 0:
                if path[i] == "{":
                    brace_count += 1
                elif path[i] == "}":
                    brace_count -= 1
                field_chars.append(path[i])
                i += 1
            tokens.append("".join(field_chars))
            continue

        if char == ".":
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
        i += 1

    if current:
        tokens.append("".join(current))

    return tokens


def get_text_at_path(obj: Any, path: str | list[str]) -> list[str]:
    """Extrai texto de ``obj`` seguindo ``path``.

    Sintaxes suportadas:
        - path simples: ``"campo1.campo2"``
        - índice de lista: ``"[0]"``, ``"[*]"``, ``"[-1]"``
        - wildcard: ``"*"``
        - seleção múltipla: ``"{campo1,campo2}"`` (aceita paths aninhados,
          ``"{campo1,nested.campo2}"``)
        - ``"$"`` ou path vazio: serializa ``obj`` inteiro como JSON
    """
    if not path or path == "$":
        return [json.dumps(obj, sort_keys=True, ensure_ascii=False)]

    tokens = tokenize_path(path) if isinstance(path, str) else path
    return _extract_from_obj(obj, tokens, 0)


def _extract_from_obj(obj: Any, tokens: list[str], pos: int) -> list[str]:
    """Executa a operação  extract from obj."""
    if pos >= len(tokens):
        if isinstance(obj, str | int | float | bool):
            return [str(obj)]
        if obj is None:
            return []
        if isinstance(obj, list | dict):
            return [json.dumps(obj, sort_keys=True, ensure_ascii=False)]
        return []

    token = tokens[pos]
    results: list[str] = []

    if token.startswith("[") and token.endswith("]"):
        if not isinstance(obj, list):
            return []
        index = token[1:-1]
        if index == "*":
            for item in obj:
                results.extend(_extract_from_obj(item, tokens, pos + 1))
        else:
            try:
                idx = int(index)
            except ValueError:
                return []
            if idx < 0:
                idx = len(obj) + idx
            if 0 <= idx < len(obj):
                results.extend(_extract_from_obj(obj[idx], tokens, pos + 1))

    elif token.startswith("{") and token.endswith("}"):
        if not isinstance(obj, dict):
            return []
        for field in (f.strip() for f in token[1:-1].split(",")):
            nested_tokens = tokenize_path(field)
            if not nested_tokens:
                continue
            current: Any = obj
            for nested_token in nested_tokens:
                if isinstance(current, dict) and nested_token in current:
                    current = current[nested_token]
                else:
                    current = None
                    break
            if current is None:
                continue
            if isinstance(current, str | int | float | bool):
                results.append(str(current))
            elif isinstance(current, list | dict):
                results.append(json.dumps(current, sort_keys=True, ensure_ascii=False))

    elif token == "*":  # nosec B105 -- token de path (wildcard), não segredo
        if isinstance(obj, dict):
            for value in obj.values():
                results.extend(_extract_from_obj(value, tokens, pos + 1))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(_extract_from_obj(item, tokens, pos + 1))

    elif isinstance(obj, dict) and token in obj:
        results.extend(_extract_from_obj(obj[token], tokens, pos + 1))

    return results


# ---------------------------------------------------------------------------
# VectoraStore
# ---------------------------------------------------------------------------


class VectoraStore:
    """Store async sobre ``AsyncConnectionPool`` (aiosqlite), com busca
    semântica opcional via ``index`` (mesmo shape aceito antes via
    ``AsyncSqliteStore``/``AsyncPostgresStore``:
    ``{"dims": int, "embed": Callable[[list[str]], Awaitable[list[list[float]]]], "fields": list[str]}``).

    Implementa ``backend/storage/protocols.py::StoreBackend`` diretamente.
    """

    def __init__(
        self, pool: AsyncConnectionPool, *, index: dict[str, Any] | None = None
    ) -> None:
        """Inicializa o estado da instância."""
        self._pool = pool
        self._index = index
        self._is_setup = False

    async def setup(self) -> None:
        """Executa a operação setup."""
        if self._is_setup:
            return
        async with self._pool.acquire() as conn:
            await conn.executescript(_SETUP_SQL)
            await conn.commit()
        self._is_setup = True

    async def aget(self, namespace: tuple[str, ...], key: str) -> Item | None:
        """Executa a operação aget."""
        started = time.perf_counter()
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT value, created_at, updated_at FROM store_items "
                "WHERE namespace = ? AND key = ?",
                (_ns_to_str(namespace), key),
            )
            row = await cur.fetchone()
        if row is None:
            await telemetry.record_storage_operation(
                backend="sqlite",
                operation="get",
                namespace=namespace,
                duration_ms=(time.perf_counter() - started) * 1000,
                result_count=0,
            )
            return None
        value_json, created_at, updated_at = row
        value = json.loads(value_json)
        if is_store_value_expired(value):
            await telemetry.record_storage_operation(
                backend="sqlite",
                operation="get",
                namespace=namespace,
                duration_ms=(time.perf_counter() - started) * 1000,
                result_count=0,
            )
            return None
        await telemetry.record_storage_operation(
            backend="sqlite",
            operation="get",
            namespace=namespace,
            duration_ms=(time.perf_counter() - started) * 1000,
            result_count=1,
        )
        return Item(
            value=value,
            key=key,
            namespace=namespace,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(updated_at),
        )

    async def aput(
        self, namespace: tuple[str, ...], key: str, value: dict[str, Any]
    ) -> None:
        """Executa a operação aput."""
        started = time.perf_counter()
        await self.setup()
        namespace_str = _ns_to_str(namespace)
        async with self._pool.acquire() as conn:
            embedding_json = await self._embed_for_index(value)

            now = datetime.now(UTC).isoformat()
            cur = await conn.execute(
                "SELECT created_at FROM store_items WHERE namespace = ? AND key = ?",
                (namespace_str, key),
            )
            existing = await cur.fetchone()
            created_at = existing[0] if existing else now

            await conn.execute(
                "INSERT OR REPLACE INTO store_items "
                "(namespace, key, value, embedding, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    namespace_str,
                    key,
                    json.dumps(value, ensure_ascii=False),
                    embedding_json,
                    created_at,
                    now,
                ),
            )
            await conn.commit()
        await telemetry.record_storage_operation(
            backend="sqlite",
            operation="put",
            namespace=namespace,
            duration_ms=(time.perf_counter() - started) * 1000,
            bytes_written=len(json.dumps(value, ensure_ascii=False).encode()),
        )

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        """Executa a operação adelete."""
        started = time.perf_counter()
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM store_items WHERE namespace = ? AND key = ?",
                (_ns_to_str(namespace), key),
            )
            await conn.commit()
        await telemetry.record_storage_operation(
            backend="sqlite",
            operation="delete",
            namespace=namespace,
            duration_ms=(time.perf_counter() - started) * 1000,
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
        """Executa a operação asearch."""
        started = time.perf_counter()
        await self.setup()
        prefix = _ns_to_str(namespace)
        async with self._pool.acquire() as conn:
            if prefix:
                cur = await conn.execute(
                    "SELECT namespace, key, value, embedding, created_at, updated_at "
                    "FROM store_items WHERE namespace = ? OR namespace LIKE ?",
                    (prefix, f"{prefix}{_NS_SEP}%"),
                )
            else:
                cur = await conn.execute(
                    "SELECT namespace, key, value, embedding, created_at, updated_at "
                    "FROM store_items"
                )
            rows = await cur.fetchall()

        query_vector = await self._embed_query(query)

        candidates: list[tuple[float | None, SearchItem]] = []
        for ns, key, value_json, embedding_json, created_at, updated_at in rows:
            value = json.loads(value_json)
            if is_store_value_expired(value):
                continue
            if not _matches_filter(value, filter):
                continue
            score: float | None = None
            if query_vector is not None and embedding_json:
                score = _cosine_similarity(query_vector, json.loads(embedding_json))
            elif query_vector is not None:
                continue  # query semântica pedida, item sem embedding — exclui
            candidates.append(
                (
                    score,
                    SearchItem(
                        namespace=_ns_from_str(ns),
                        key=key,
                        value=value,
                        created_at=datetime.fromisoformat(created_at),
                        updated_at=datetime.fromisoformat(updated_at),
                        score=score,
                    ),
                )
            )

        if query_vector is not None:
            candidates.sort(key=lambda pair: pair[0] or 0.0, reverse=True)

        page = candidates[offset : offset + limit]
        result = [item for _, item in page]
        await telemetry.record_storage_operation(
            backend="sqlite",
            operation="search",
            namespace=namespace,
            duration_ms=(time.perf_counter() - started) * 1000,
            result_count=len(result),
        )
        return result

    async def health(self) -> HealthResult:
        """Executa a operação health."""
        from backend.storage.protocols import _err, _ok

        try:
            await self.setup()
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    async def _embed_for_index(self, value: dict[str, Any]) -> str | None:
        """Calcula o embedding médio dos campos indexados de ``value``, ou
        ``None`` se não há índice configurado / nada extraído."""
        if self._index is None:
            return None
        fields = self._index.get("fields", ["$"])
        texts: list[str] = []
        for field in fields:
            texts.extend(get_text_at_path(value, field))
        if not texts:
            return None
        vectors = await self._index["embed"](texts)
        # Um item pode indexar múltiplos campos/trechos — guarda a média dos
        # vetores (aproximação simples, suficiente pro volume de memórias do
        # agente; não é um índice multi-vetor por chunk como o RAG de
        # documentos).
        dims = self._index["dims"]
        avg = [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]
        return json.dumps(avg)

    async def _embed_query(self, query: str | None) -> list[float] | None:
        """Executa a operação  embed query."""
        if not query or self._index is None:
            return None
        vectors = await self._index["embed"]([query])
        return vectors[0] if vectors else None
