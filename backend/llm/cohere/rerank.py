"""Rerank nativo do Cohere — ``POST /v2/rerank``.

Mesma interface dos rerankers já usados (``compress_documents``), pra encaixar
no ``FallbackReranker`` que alterna Cohere↔Voyage sem mudança.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.llm.cohere.client import CohereClient
from backend.vtypes.documents import Document

logger = logging.getLogger(__name__)


class VectoraCohereRerank:
    def __init__(
        self,
        model: str,
        client: CohereClient,
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
            return []

        resultados = await self.client.rerank(
            query=query,
            documents=[d.page_content for d in docs],
            model=self.model,
            top_n=min(self.top_n, len(docs)),
        )

        if not resultados:
            # Degrada para a ordem original — zerar é pior que não reordenar.
            logger.warning(
                "cohere: /v2/rerank devolveu results vazio — mantendo a ordem original"
            )
            return docs

        saida: list[Document] = []
        for item in resultados:
            indice = item.get("index")
            if not isinstance(indice, int) or not (0 <= indice < len(docs)):
                logger.warning(
                    "cohere: /v2/rerank devolveu index fora do intervalo",
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
