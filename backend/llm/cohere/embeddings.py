"""Embeddings nativos do Cohere — ``POST /v2/embed``.

Encaixa na interface ``Embeddings`` nativa (``backend.llm.base``), então o
pipeline de RAG (``storage/factory.py::_build_lc_embeddings``) consome sem
mudança.

``input_type`` é resolvido por chamada (``search_document``/
``search_query``) — a Embed API v2 exige isso por request.
"""

from __future__ import annotations

import asyncio

from backend.llm.base import Embeddings
from backend.llm.cohere.client import CohereClient


class VectoraCohereEmbeddings(Embeddings):
    def __init__(self, model: str, client: CohereClient) -> None:
        self.model = model
        self.client = client

    async def _embed(self, textos: list[str], input_type: str) -> list[list[float]]:
        return await self.client.embed(textos, model=self.model, input_type=input_type)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(list(texts), "search_document")

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "search_query"))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return asyncio.run(self.aembed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        return asyncio.run(self.aembed_query(text))
