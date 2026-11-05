"""Construção, cache e fallback de LLM/embeddings/reranker por provider.

Tudo relacionado a "como o Vectora fala com o provider de LLM": construção do
``VectoraStore`` de memórias/skills (``backends.py``), wrappers de fallback
entre providers em caso de quota/erro (``fallback_chat_client.py``,
``fallback_embeddings.py``, ``fallback_reranker.py``,
``provider_fallback.py``), e o cache auxiliar de LLM já bindado por usuário
(``llm_tools.py``).
"""

from __future__ import annotations
