"""Índice de busca unificado sobre fatos, skills e buckets RAG.

Cada storage físico permanece exatamente onde está — fatos no BaseStore
(``backend/tools/memory.py``), skills como ``SKILL.md`` em disco
(``backend/workspace/skills.py``), buckets RAG no catálogo do workspace
(``backend/services/rag_buckets.py``) — este módulo só combina os três atrás
de uma única busca, consumida pela Memory Tab (``GET
/workspaces/{id}/memory/search``). É unificação de **índice**, não de
storage: skills continuam sendo arquivo em disco porque
``create_deep_agent(skills=[...])`` exige isso (CLAUDE.md §17).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

MemoryHitType = Literal["fact", "skill", "rag_bucket"]

ALL_MEMORY_HIT_TYPES: frozenset[MemoryHitType] = frozenset(
    {"fact", "skill", "rag_bucket"}
)


@dataclass(slots=True)
class MemoryIndexHit:
    type: MemoryHitType
    id: str
    title: str
    snippet: str
    score: float | None = None


async def _search_facts(query: str, user_id: str, limit: int) -> list[MemoryIndexHit]:
    try:
        from backend.services.agent_factory import get_store

        store = await get_store()
        ns = ("user", user_id, "memories")
        items = await store.asearch(ns, query=query, limit=limit)
        hits = [
            MemoryIndexHit(
                type="fact",
                id=item.key,
                title=item.key,
                snippet=str(item.value.get("content", "")),
                score=getattr(item, "score", None),
            )
            for item in items
        ]
        # Sem embedding configurado, `asearch` ignora `query` e devolve tudo
        # (mesmo sinal de `has_scores` que `search_memory` tool usa em
        # backend/tools/memory.py) — sem esse fallback, a busca de fato
        # ficaria inconsistente com skill/bucket, que sempre fazem substring
        # match.
        has_scores = any(h.score is not None for h in hits)
        q = query.strip().lower()
        if not has_scores and q:
            hits = [h for h in hits if q in h.title.lower() or q in h.snippet.lower()]
        return hits[:limit]
    except Exception:
        logger.exception("memory_index: busca de fatos falhou (user_id=%s)", user_id)
        return []


def _search_skills(query: str, user_id: str, limit: int) -> list[MemoryIndexHit]:
    try:
        from backend.workspace.skills import list_skills

        q = query.strip().lower()
        hits: list[MemoryIndexHit] = []
        for skill in list_skills(user_id):
            haystack = f"{skill.name}\n{skill.description}".lower()
            if q and q not in haystack:
                continue
            hits.append(
                MemoryIndexHit(
                    type="skill",
                    id=skill.id,
                    title=skill.name,
                    snippet=skill.description,
                )
            )
        return hits[:limit]
    except Exception:
        logger.exception("memory_index: busca de skills falhou (user_id=%s)", user_id)
        return []


def _search_rag_buckets(
    query: str, workspace_id: str | None, limit: int
) -> list[MemoryIndexHit]:
    if not workspace_id:
        return []
    try:
        from backend.services import rag_buckets
        from backend.workspace.runtime_settings import runtime_settings

        q = query.strip().lower()
        hits: list[MemoryIndexHit] = []
        for bucket in rag_buckets.list_buckets(runtime_settings, workspace_id):
            haystack = f"{bucket.name}\n{bucket.description_md}".lower()
            if q and q not in haystack:
                continue
            hits.append(
                MemoryIndexHit(
                    type="rag_bucket",
                    id=bucket.id,
                    title=bucket.name,
                    snippet=bucket.description_md,
                )
            )
        return hits[:limit]
    except Exception:
        logger.exception(
            "memory_index: busca de buckets falhou (workspace_id=%s)",
            workspace_id,
        )
        return []


async def search_unified_memory(
    query: str,
    user_id: str,
    workspace_id: str | None = None,
    types: frozenset[MemoryHitType] | None = None,
    limit: int = 20,
) -> list[MemoryIndexHit]:
    """Busca combinada em fatos, skills e buckets RAG.

    Cada tipo é buscado isoladamente — uma falha num storage (ex.: skills
    com ``index.json`` corrompido) não derruba a busca dos outros dois.
    """
    wanted = types or ALL_MEMORY_HIT_TYPES
    hits: list[MemoryIndexHit] = []

    if "fact" in wanted:
        hits.extend(await _search_facts(query, user_id, limit))
    if "skill" in wanted:
        hits.extend(_search_skills(query, user_id, limit))
    if "rag_bucket" in wanted:
        hits.extend(_search_rag_buckets(query, workspace_id, limit))

    return hits[:limit]
