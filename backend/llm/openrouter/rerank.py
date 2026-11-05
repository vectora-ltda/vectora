"""Rerank nativo do OpenRouter — ``POST /rerank``.

O endpoint existe (``model``, ``query``, ``documents``, ``top_n`` →
``results[]`` com ``index``/``relevance_score``). O plano tinha fechado o
``reranker_type`` sem ele por uma afirmação minha errada de que o OpenRouter
seria só proxy de chat.

Mesma interface dos rerankers já usados (``compress_documents``), pra encaixar
no ``FallbackReranker`` que alterna Cohere↔Voyage sem mudança.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.llm.openrouter.client import OpenRouterClient
from backend.vtypes.documents import Document

logger = logging.getLogger(__name__)


class OpenRouterRerank:
    def __init__(
        self,
        model: str,
        client: OpenRouterClient,
        *,
        top_n: int = 5,
    ) -> None:
        self.model = model
        self.client = client
        self.top_n = top_n

    async def acompress_documents(
        self, documents: Any, query: str, callbacks: Any = None
    ) -> list[Document]:
        docs = list(documents)
        if not docs:
            # Chamar a API com lista vazia gasta crédito e não reordena nada.
            return []

        payload = {
            "model": self.model,
            "query": query,
            "documents": [d.page_content for d in docs],
            "top_n": min(self.top_n, len(docs)),
        }
        resposta = await self.client.post_json("/rerank", payload)
        resultados = resposta.get("results")

        if not isinstance(resultados, list) or not resultados:
            # Degrada para a ordem original: zerar os resultados é pior que
            # não reordenar — o usuário perderia respostas que a busca já
            # tinha encontrado.
            logger.warning(
                "openrouter: /rerank devolveu results vazio — mantendo a ordem original"
            )
            return docs

        saida: list[Document] = []
        for item in resultados:
            indice = item.get("index")
            if not isinstance(indice, int) or not (0 <= indice < len(docs)):
                # `index` fora do intervalo estouraria IndexError e derrubaria
                # a busca inteira por causa de um item malformado.
                logger.warning(
                    "openrouter: /rerank devolveu index fora do intervalo",
                    extra={"index": indice, "total": len(docs)},
                )
                continue
            doc = docs[indice]
            saida.append(
                Document(
                    page_content=doc.page_content,
                    metadata={
                        **doc.metadata,
                        "relevance_score": item.get("relevance_score"),
                    },
                )
            )
        return saida

    def compress_documents(
        self, documents: Any, query: str, callbacks: Any = None
    ) -> list[Document]:
        return asyncio.run(self.acompress_documents(documents, query, callbacks))
