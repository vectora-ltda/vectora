"""``FakeChatClient`` — implementação nativa do Protocol `ChatClient`
(`backend/llm/base.py`) pra testes, sem depender de nenhum provider real.
Generaliza o padrão `_ScriptedChatClient` que
`test_engine_conversation_loop.py`/`test_engine_subagents.py` reimplementam
localmente hoje.

Roteirizável por sequência de turnos: cada chamada a `astream`/`agenerate`
consome o próximo turno do script, na ordem — um turno é a lista de
`VMessageChunk` que aquela volta do loop deve produzir (pra `astream`) ou
diretamente uma `VMessage` completa (pra `agenerate`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.vtypes.message import ContentBlock, MessageRole, VMessage

if TYPE_CHECKING:
    from backend.persistence.native.store import Item, SearchItem
    from backend.storage.protocols import HealthResult
    from backend.tools.registry import ToolSpec
    from backend.vtypes.message import VMessageChunk


class FakeChatClient:
    """Scriptável por `turns` (streaming) e/ou `responses` (não-streaming).
    Registra cada chamada em `.calls` (mensagens + tools recebidas) pra
    asserção posterior sobre o que o motor de fato enviou ao "provider"."""

    def __init__(
        self,
        turns: list[list[VMessageChunk]] | None = None,
        responses: list[VMessage] | None = None,
    ) -> None:
        self._turns = turns or []
        self._responses = responses or []
        self.stream_calls = 0
        self.generate_calls = 0
        self.calls: list[dict[str, object]] = []

    async def astream(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[VMessageChunk]:
        self.calls.append(
            {"kind": "astream", "messages": list(messages), "tools": tools}
        )
        if self.stream_calls >= len(self._turns):
            msg = (
                f"FakeChatClient.astream chamado {self.stream_calls + 1}x, mas só "
                f"{len(self._turns)} turno(s) foram roteirizados — script "
                "insuficiente pro teste."
            )
            raise AssertionError(msg)
        turno = self._turns[self.stream_calls]
        self.stream_calls += 1
        for chunk in turno:
            yield chunk

    async def agenerate(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> VMessage:
        self.calls.append(
            {"kind": "agenerate", "messages": list(messages), "tools": tools}
        )
        if self.generate_calls >= len(self._responses):
            msg = (
                f"FakeChatClient.agenerate chamado {self.generate_calls + 1}x, mas "
                f"só {len(self._responses)} resposta(s) foram roteirizadas — "
                "script insuficiente pro teste."
            )
            raise AssertionError(msg)
        resposta = self._responses[self.generate_calls]
        self.generate_calls += 1
        return resposta


def text_chunk(text: str) -> VMessageChunk:
    """Atalho pra roteirizar um turno de texto puro (sem tool call)."""
    from backend.vtypes.message import VMessageChunk as _VMessageChunk

    return _VMessageChunk(delta_text=text)


def text_response(text: str, *, name: str | None = None) -> VMessage:
    """Atalho pra roteirizar uma resposta completa de `agenerate`."""
    return VMessage(
        role=MessageRole.ASSISTANT,
        content=[ContentBlock(kind="text", text=text)],
        finish_reason="stop",
        name=name,
    )


class NativeInMemoryStore:
    """Implementação nativa do Protocol `StoreBackend`
    (`backend/storage/protocols.py`) sobre um dict Python em memória, como
    fake de store nos testes — mesmo shape de retorno (`Item`/`SearchItem`
    com atributos `.value`/`.key`/`.score`) de
    `VectoraStore`/`VectoraPostgresStore`, sem persistência real.

    Aceita o mesmo `index` opcional (`{"dims", "embed", "fields"}`) — sem
    índice configurado, `asearch(query=...)` ignora `query` e devolve todos
    os itens do namespace (mesmo comportamento de `InMemoryStore` sem
    `index`), consistente com `VectoraStore`.
    """

    def __init__(self, *, index: dict[str, Any] | None = None) -> None:
        self._index = index
        self._data: dict[tuple[str, ...], dict[str, Item]] = {}

    async def aget(self, namespace: tuple[str, ...], key: str) -> Item | None:
        return self._data.get(namespace, {}).get(key)

    async def aput(
        self, namespace: tuple[str, ...], key: str, value: dict[str, Any]
    ) -> None:
        from backend.persistence.native.store import Item

        bucket = self._data.setdefault(namespace, {})
        existing = bucket.get(key)
        now = datetime.now(UTC)
        bucket[key] = Item(
            value=value,
            key=key,
            namespace=namespace,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self._data.get(namespace, {}).pop(key, None)

    async def asearch(
        self,
        namespace: tuple[str, ...],
        *,
        query: str | None = None,
        limit: int = 10,
        filter: dict[str, Any] | None = None,  # noqa: A002 — mesmo nome do protocolo
        offset: int = 0,
    ) -> list[SearchItem]:
        from backend.persistence.native.store import (
            SearchItem,
            _cosine_similarity,
            _matches_filter,
            get_text_at_path,
        )

        ns_prefix_len = len(namespace)
        items = [
            item
            for ns, bucket in self._data.items()
            for item in bucket.values()
            if ns[:ns_prefix_len] == namespace and _matches_filter(item.value, filter)
        ]

        index = self._index
        query_vector: list[float] | None = None
        if query and index is not None:
            vectors = await index["embed"]([query])
            query_vector = vectors[0] if vectors else None

        candidates: list[tuple[float | None, SearchItem]] = []
        for item in items:
            score: float | None = None
            if query_vector is not None and index is not None:
                fields = index["fields"]
                texts: list[str] = []
                for field in fields:
                    texts.extend(get_text_at_path(item.value, field))
                if not texts:
                    continue
                vectors = await index["embed"](texts)
                dims = index["dims"]
                avg = [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]
                score = _cosine_similarity(query_vector, avg)
            candidates.append(
                (
                    score,
                    SearchItem(
                        value=item.value,
                        key=item.key,
                        namespace=item.namespace,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                        score=score,
                    ),
                )
            )

        if query_vector is not None:
            candidates.sort(key=lambda pair: pair[0] or 0.0, reverse=True)

        page = candidates[offset : offset + limit]
        return [entry for _, entry in page]

    async def health(self) -> HealthResult:
        from backend.storage.protocols import _ok

        return _ok()
