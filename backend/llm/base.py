"""``ChatClient`` — Protocol nativo que os 5 clients de chat de provider
implementam.

Sem ``bind_tools()`` produzindo uma instância nova imutável com as tools
"presas" — cada chamada de ``astream``/``agenerate`` recebe ``tools=``
diretamente como parâmetro.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.tools.registry import ToolSpec
    from backend.vtypes.message import VMessage, VMessageChunk


class Embeddings(Protocol):
    """Interface comum aos clients de embedding nativos (cohere/voyage/
    ollama/openrouter) e ao ``FallbackEmbeddings`` que orquestra fallback
    entre eles."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Vetoriza um lote de documentos (indexação)."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Vetoriza uma consulta de busca."""
        ...

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Versão assíncrona de ``embed_documents``."""
        ...

    async def aembed_query(self, text: str) -> list[float]:
        """Versão assíncrona de ``embed_query``."""
        ...


class ChatClient(Protocol):
    """Interface comum aos 5 clients de chat nativos (openai/anthropic/
    google/openrouter/ollama) e ao ``FallbackChatClient`` que orquestra
    fallback entre eles."""

    def astream(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[VMessageChunk]:
        """Streaming — cada implementação faz `yield` de fragmentos
        `VMessageChunk` conforme o provider entrega (delta de texto, tool
        call fragmentada ou completa, usage).

        Sem ``async`` na assinatura de propósito: as implementações são
        funções geradoras assíncronas (``async def ... yield ...``), que já
        devolvem um ``AsyncIterator`` na chamada síncrona — não um
        ``Coroutine`` que precisa ser `await`ado antes de iterar. Declarar
        ``async def`` aqui faria o Protocol descrever a assinatura errada
        (`Coroutine[..., AsyncIterator]`), quebrando a checagem de tipo de
        qualquer implementação real contra este Protocol."""
        ...

    async def agenerate(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> VMessage:
        """Não-streaming — devolve a mensagem completa do turno."""
        ...
