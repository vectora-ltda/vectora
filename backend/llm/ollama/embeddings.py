"""Embeddings do Ollama — ``POST /api/embed``.

Encaixa na interface ``Embeddings`` nativa (``backend.llm.base``), então o
pipeline de RAG consome sem mudança.
"""

from __future__ import annotations

import asyncio
import logging

from backend.llm.base import Embeddings
from backend.llm.ollama.client import OllamaClient, OllamaResponseError

logger = logging.getLogger(__name__)


class OllamaEmbeddings(Embeddings):
    def __init__(self, model: str, client: OllamaClient) -> None:
        self.model = model
        self.client = client

    async def _embed(self, textos: list[str]) -> list[list[float]]:
        if not textos:
            return []

        resposta = await self.client.post_json(
            "/api/embed", {"model": self.model, "input": textos}
        )
        vetores = resposta.get("embeddings")
        if not isinstance(vetores, list) or not vetores:
            msg = (
                "Ollama devolveu `embeddings` vazio em /api/embed — a ingestão "
                "para aqui em vez de gravar vetores nulos no índice"
            )
            raise OllamaResponseError(msg)
        if len(vetores) != len(textos):
            # Associar na ordem com quantidades diferentes deixaria chunk sem
            # embedding e deslocaria os demais — corrupção silenciosa.
            msg = (
                f"Ollama devolveu {len(vetores)} embeddings para {len(textos)} "
                "textos — quantidade divergente"
            )
            raise OllamaResponseError(msg)
        return vetores

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(list(texts))

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._embed([text]))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return asyncio.run(self.aembed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        return asyncio.run(self.aembed_query(text))
