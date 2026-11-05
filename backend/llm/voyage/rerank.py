"""Rerank nativo da Voyage AI — ``POST /rerank``.

Mesma interface dos rerankers já usados (``compress_documents``), pra encaixar
no ``FallbackReranker`` que alterna Cohere↔Voyage sem mudança.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.llm.voyage.client import VoyageClient
from backend.vtypes.documents import Document

logger = logging.getLogger(__name__)


class VectoraVoyageRerank:
    def __init__(
        self,
        model: str,
        client: VoyageClient,
        *,
        top_k: int = 5,
    ) -> None:
        self.model = model
        self.client = client
        self.top_k = top_k

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
            top_k=min(self.top_k, len(docs)),
        )

        if not resultados:
            logger.warning(
                "voyage: /rerank devolveu data vazio — mantendo a ordem original"
            )
            return docs

        saida: list[Document] = []
        for item in resultados:
            indice = item.get("index")
            if not isinstance(indice, int) or not (0 <= indice < len(docs)):
                logger.warning(
                    "voyage: /rerank devolveu index fora do intervalo",
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
