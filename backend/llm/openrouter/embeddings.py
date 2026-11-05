"""Embeddings nativos do OpenRouter — ``POST /embeddings``.

Encaixa na interface ``Embeddings`` nativa (``backend.llm.base``), então o
pipeline de RAG (``storage/factory.py::_build_lc_embeddings``) consome sem
mudança.

Ganho sobre o caminho anterior (``OpenAIEmbeddings`` com ``base_url``
trocado): ``input_type`` (modelos assimétricos precisam saber se o texto é
consulta ou documento) e ``usage.cost``, que a API da OpenAI não tem.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.llm.base import Embeddings
from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError

logger = logging.getLogger(__name__)


class OpenRouterEmbeddings(Embeddings):
    def __init__(
        self,
        model: str,
        client: OpenRouterClient,
        *,
        dimensions: int | None = None,
    ) -> None:
        self.model = model
        self.client = client
        self.dimensions = dimensions
        #: Custo acumulado das chamadas desta instância (o OpenRouter devolve
        #: por requisição). Consumido pelo medidor de uso.
        self.total_cost = 0.0

    async def _embed(self, textos: list[str], input_type: str) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": textos,
            "input_type": input_type,
        }
        if self.dimensions:
            payload["dimensions"] = self.dimensions

        resposta = await self.client.post_json("/embeddings", payload)
        dados = resposta.get("data")
        if not isinstance(dados, list) or not dados:
            msg = (
                "OpenRouter devolveu `data` vazio em /embeddings — a ingestão "
                "para aqui em vez de gravar vetores nulos no índice"
            )
            raise OpenRouterResponseError(msg)

        # A API não garante a ordem de `data`; confiar na ordem de chegada
        # associaria o vetor ao chunk errado — corrupção silenciosa que só
        # aparece como busca ruim muito depois.
        por_indice: dict[int, list[float]] = {}
        for item in dados:
            vetor = item.get("embedding")
            if not isinstance(vetor, list):
                msg = "Item de /embeddings sem campo `embedding` utilizável"
                raise OpenRouterResponseError(msg)
            por_indice[int(item.get("index", len(por_indice)))] = vetor

        faltando = [i for i in range(len(textos)) if i not in por_indice]
        if faltando:
            msg = f"OpenRouter não devolveu embedding para os índices {faltando}"
            raise OpenRouterResponseError(msg)

        custo = (resposta.get("usage") or {}).get("cost")
        if custo:
            self.total_cost += float(custo)

        return [por_indice[i] for i in range(len(textos))]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(list(texts), "document")

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "query"))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return asyncio.run(self.aembed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        return asyncio.run(self.aembed_query(text))
