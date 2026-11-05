"""FallbackReranker — rerank com fallback automático de provider por quota.

Envolve um reranker primário (Cohere) e um secundário (VoyageAI). Em erro de
quota no primário, troca para o secundário, registra ``record_switch`` e — quando
há callback manager ativo — emite ``model_switched`` para a UI avisar.

Duck-typed sobre ``compress_documents``/``acompress_documents`` (a interface usada
pelo caller de RAG), não exige herdar ``BaseDocumentCompressor``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class FallbackReranker:
    """Reranker que cai do primário para o secundário em quota esgotada."""

    def __init__(
        self,
        primary: Any,
        secondary: Any,
        *,
        primary_id: str,
        secondary_id: str,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.primary_id = primary_id
        self.secondary_id = secondary_id

    def compress_documents(
        self, documents: Any, query: str, callbacks: Any = None
    ) -> Any:
        from backend.llm.provider_fallback import is_quota_error, record_switch

        try:
            return self.primary.compress_documents(documents, query, callbacks)
        except Exception as exc:
            if not is_quota_error(exc):
                raise
            record_switch(self.primary_id, self.secondary_id)
            logger.warning(
                "reranker provider switch por quota",
                extra={"from": self.primary_id, "to": self.secondary_id},
            )
            return self.secondary.compress_documents(documents, query, callbacks)

    async def acompress_documents(
        self, documents: Any, query: str, callbacks: Any = None
    ) -> Any:
        from backend.llm.provider_fallback import (
            emit_model_switch_event,
            is_quota_error,
        )

        try:
            return await self.primary.acompress_documents(documents, query, callbacks)
        except Exception as exc:
            if not is_quota_error(exc):
                raise
            # emit_model_switch_event já chama record_switch — uma única
            # entrada na fila, não duas.
            await emit_model_switch_event(self.primary_id, self.secondary_id)
            logger.warning(
                "reranker provider switch por quota (async)",
                extra={"from": self.primary_id, "to": self.secondary_id},
            )
            return await self.secondary.acompress_documents(documents, query, callbacks)
