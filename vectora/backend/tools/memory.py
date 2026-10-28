"""Memory tools: persistência de memórias entre sessões.

Acessa o store persistente via ``_get_store(ctx)`` — sempre via
``ctx.store``, injetado pelo motor de execução nativo antes do turno
(``backend/api/handlers/chat.py`` popula ``run_ctx.store``).

As memórias são isoladas por usuário no namespace ``("user", <user_id>, "memories")``,
garantindo isolamento entre usuários e compatibilidade com o StoreBackend do
CompositeBackend.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from backend.persistence.native.store import is_store_value_expired
from backend.storage.protocols import StoreBackend
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

#: Categorias de fato — mesmo vocabulário usado por
#: `backend/scheduling/memory_consolidation.py` pra manter uma única
#: taxonomia em todo o sistema de memória. Validado automaticamente pelo
#: schema Pydantic que `vtool` gera a partir do type hint — valor fora do
#: Literal nunca chega ao corpo da função (rejeitado na camada de parsing
#: da tool, ver `tests/unit/test_memory.py`).
FactCategory = Literal["gotcha", "decision", "preference", "rule"]
_MAX_MEMORY_SEARCH = 100_000


# ---------------------------------------------------------------------------
# Helpers de contexto
# ---------------------------------------------------------------------------


def _user_id_from_ctx(ctx: ToolContext) -> str:
    """Extrai user_id limpo do contexto de execução.

    Retorna o user_id em formato simples (sem prefixo) para uso como
    componente do namespace do store. Prioridade:
    1. user_id explícito (diferente do default "local")
    2. workspace_id (memórias seguem o projeto)
    3. thread_id (fallback por sessão)
    4. "local" (modo offline/anônimo)
    """
    if ctx.user_id and ctx.user_id != "local":
        return ctx.user_id
    if ctx.workspace_id:
        return f"workspace_{ctx.workspace_id}"
    if ctx.thread_id:
        return f"session_{ctx.thread_id}"
    return "local"


def _memory_namespace(ctx: ToolContext) -> tuple[str, ...]:
    """Retorna o namespace autorizado do store para as memórias.

    A identidade do usuário sozinha não é suficiente em modo multi-tenant:
    o mesmo usuário pode pertencer a mais de um workspace/organização. Os
    escopos presentes no contexto passam a fazer parte da chave física, sem
    alterar o namespace legado do modo local e dos consumidores antigos.
    """
    user_id = _user_id_from_ctx(ctx)
    namespace: list[str] = ["user", user_id]
    if ctx.org_id:
        namespace.extend(("org", ctx.org_id))
    if ctx.workspace_id:
        namespace.extend(("workspace", ctx.workspace_id))
    namespace.append("memories")
    return tuple(namespace)


def _memory_namespace_candidates(ctx: ToolContext) -> list[tuple[str, ...]]:
    """Return scoped storage first, followed by the legacy user namespace.

    Existing installations predate org/workspace-qualified keys.  Reads and
    destructive operations must see those records during the migration rather
    than silently presenting an empty memory store.
    """
    scoped = _memory_namespace(ctx)
    legacy = ("user", _user_id_from_ctx(ctx), "memories")
    return [scoped] if scoped == legacy else [scoped, legacy]


_is_memory_expired = is_store_value_expired


async def list_fact_contents(user_id: str) -> list[str]:
    """Conteúdo de todos os fatos salvos de um usuário — usado por
    `backend/services/remember_trigger.py` pra deduplicar propostas do
    Remember (`dedupe_fact_drafts`) antes de sugerir de novo um fato já
    aprovado. Acessor direto (sem passar por `ToolContext`/`vtool`, que o
    caller — fora do turno do agente — não tem).

    Usa `backend.services.agent_factory.get_store()` (mesma instância
    injetada no grafo do agente via `create_deep_agent(store=...)`), não
    `_get_store(ctx)` — o caller roda fire-and-forget depois do turno já
    terminado, sem um `ToolContext` populado nem contextvar do grafo
    disponíveis."""
    from backend.services.agent_factory import get_store as get_agent_store

    store = await get_agent_store()
    ctx = ToolContext(user_id=user_id)
    items: list[Any] = []
    seen: set[str] = set()
    for ns in _memory_namespace_candidates(ctx):
        for item in await store.asearch(ns):
            if item.key not in seen:
                seen.add(item.key)
                items.append(item)
    return [
        item.value.get("content", "")
        for item in items
        if not _is_memory_expired(item.value)
    ]


async def _search_unexpired(
    store: StoreBackend,
    namespace: tuple[str, ...],
    *,
    query: str,
    limit: int,
    initial_scan_limit: int | None = None,
) -> list[Any]:
    """Busca candidatos até reunir ``limit`` itens válidos.

    Backends nativos filtram expiração antes da paginação. Implementações
    externas podem paginar primeiro; nesse caso aumentamos a janela até ela
    não estar mais cheia ou até o limite de segurança, evitando que registros
    expirados escondam memórias válidas.
    """
    requested = int(limit)
    if requested <= 0:
        return []
    wanted = min(requested, _MAX_MEMORY_SEARCH)
    # Search tools start with a large page so expired records cannot consume
    # the requested result count. Context hydration may opt into its smaller
    # bounded page because it has a strict per-turn budget.
    first_page = 1_000 if initial_scan_limit is None else max(1, initial_scan_limit)
    scan_limit = min(_MAX_MEMORY_SEARCH, max(wanted, first_page))
    valid: list[Any] = []
    while scan_limit <= _MAX_MEMORY_SEARCH:
        if query:
            candidates = await store.asearch(namespace, query=query, limit=scan_limit)
        else:
            candidates = await store.asearch(namespace, limit=scan_limit)
        valid = [item for item in candidates if not _is_memory_expired(item.value)]
        if len(valid) >= wanted or len(candidates) < scan_limit:
            return valid[:wanted]
        if scan_limit >= _MAX_MEMORY_SEARCH:
            return valid[:wanted]
        next_limit = min(_MAX_MEMORY_SEARCH, scan_limit * 2)
        if next_limit == scan_limit:
            return valid[:wanted]
        scan_limit = next_limit
    return valid[:wanted]


def _get_store(ctx: ToolContext) -> Any:
    """Obtém o store persistente da execução atual — via ``ctx.store``.

    O motor de execução nativo sempre popula ``ctx.store`` antes do turno
    (``backend/api/handlers/chat.py``), então este é o único caminho.

    Raises:
        RuntimeError: Se chamado fora do motor nativo (``ctx.store`` nulo).
    """
    if ctx.store is not None:
        return ctx.store
    msg = (
        "store não injetado no ToolContext — a tool de memória só é "
        "executável dentro do motor nativo, que popula ctx.store antes do turno"
    )
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="memory",
        destructive=False,
        icon="bookmark",
    )
)
async def save_memory(
    ctx: ToolContext,
    key: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    ttl_days: int | None = None,
    category: FactCategory | None = None,
) -> str:
    """Salva uma memória persistente para uso em futuras conversas da mesma sessão.

    As memórias são isoladas por usuário para evitar poluição entre projetos distintos.
    Armazenadas no store persistente com suporte a busca semântica via Cohere
    quando configurado.

    Args:
        key: Chave única da memória (ex: 'user_preferences', 'project_context')
        content: Conteúdo da memória (string com informações a recordar)
        metadata: Metadados adicionais
        ttl_days: Dias até expiração; memórias expiradas deixam de ser retornadas
        category: Categoria do fato (gotcha/decision/preference/rule) — opcional,
            None quando não classificado

    Returns:
        JSON com status saved/failed
    """
    try:
        store = _get_store(ctx)
        ns = _memory_namespace(ctx)
        now = datetime.now(UTC).isoformat()

        if ttl_days is not None and ttl_days <= 0:
            return json.dumps(
                {"status": "failed", "error": "ttl_days deve ser positivo", "key": key}
            )

        extra = ctx._extra if isinstance(ctx._extra, dict) else {}
        persisted_metadata = dict(metadata or {})
        # A tool caller cannot promote untrusted content by supplying a
        # metadata field. The server-owned provenance marker is authoritative.
        persisted_metadata["trust_level"] = (
            "untrusted" if extra.get("untrusted_content") else "trusted"
        )
        value: dict[str, Any] = {
            "key": key,
            "content": content,
            "metadata": persisted_metadata,
            "updated_at": now,
            "category": category,
        }
        if ttl_days is not None:
            value["ttl_days"] = ttl_days
            value["valid_until"] = (
                datetime.now(UTC) + timedelta(days=ttl_days)
            ).isoformat()

        await store.aput(ns, key, value)

        logger.info(
            "memory_saved key=%s ns=%s ttl_days=%s category=%s",
            key,
            ns,
            ttl_days,
            category,
        )
        return json.dumps(
            {
                "status": "saved",
                "key": key,
                "namespace": list(ns),
                "expires_in_days": ttl_days,
            }
        )

    except Exception as exc:
        logger.exception("save_memory_failed key=%s", key)
        return json.dumps({"status": "failed", "error": str(exc), "key": key})


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="memory",
        destructive=False,
        icon="bookmark-check",
    )
)
async def get_memory(ctx: ToolContext, key: str | None = None) -> str:
    """Recupera memórias persistentes salvas.

    Retorna apenas memórias do namespace do usuário/workspace atual.

    Args:
        key: Chave específica. Se None, retorna todas as memórias do namespace.

    Returns:
        JSON com conteúdo da memória ou lista de memórias
    """
    try:
        store = _get_store(ctx)
        namespaces = _memory_namespace_candidates(ctx)

        if key is not None:
            item = None
            for namespace in namespaces:
                item = await store.aget(namespace, key)
                if item is not None:
                    break
            if item is None:
                logger.warning("memory_not_found key=%s ns=%s", key, namespaces[0])
                return json.dumps({"status": "not_found", "key": key})

            val = item.value
            if _is_memory_expired(val):
                for ns in namespaces:
                    await store.adelete(ns, key)
                return json.dumps({"status": "not_found", "key": key})
            logger.debug("memory_retrieved key=%s", key)
            return json.dumps(
                {
                    "status": "found",
                    "key": key,
                    "content": val.get("content", ""),
                    "metadata": val.get("metadata", {}),
                    "updated_at": val.get("updated_at"),
                    "category": val.get("category"),
                }
            )

        # Sem key: lista todas as memórias do namespace
        items = []
        seen: set[str] = set()
        for ns in namespaces:
            for item in await store.asearch(ns):
                if item.key not in seen:
                    seen.add(item.key)
                    items.append(item)
        items = [item for item in items if not _is_memory_expired(item.value)]
        memories = [
            {
                "key": item.key,
                "content": item.value.get("content", ""),
                "metadata": item.value.get("metadata", {}),
                "updated_at": item.value.get("updated_at"),
                "category": item.value.get("category"),
            }
            for item in items
        ]
        logger.debug(
            "all_memories_retrieved count=%d ns=%s", len(memories), namespaces[0]
        )
        return json.dumps(
            {
                "status": "success",
                "count": len(memories),
                "memories": memories,
            }
        )

    except Exception as exc:
        logger.exception("get_memory_failed key=%s", key)
        return json.dumps({"status": "failed", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        render_hint="search_results",
        category="memory",
        destructive=False,
        icon="search-check",
    )
)
async def search_memory(ctx: ToolContext, query: str, limit: int = 5) -> str:
    """Busca semântica em memórias — encontra memórias relevantes por similaridade.

    Diferente de ``get_memory`` (busca por chave exata), ``search_memory`` usa
    o índice vetorial do store persistente (Cohere embed quando configurado)
    para encontrar memórias semanticamente relacionadas à query.

    Args:
        query: Texto da busca semântica (ex: "preferências de código", "arquitetura")
        limit: Máximo de memórias a retornar (padrão: 5)

    Returns:
        JSON com lista de memórias ordenadas por relevância semântica
    """
    try:
        store = _get_store(ctx)
        items: list[Any] = []
        seen: set[str] = set()
        for ns in _memory_namespace_candidates(ctx):
            for item in await _search_unexpired(store, ns, query=query, limit=limit):
                if item.key not in seen:
                    seen.add(item.key)
                    items.append(item)
        items = items[:limit]
        has_scores = any(getattr(item, "score", None) is not None for item in items)

        memories = [
            {
                "key": item.key,
                "content": item.value.get("content", ""),
                "updated_at": item.value.get("updated_at"),
                **({"score": item.score} if has_scores else {}),
            }
            for item in items
        ]
        logger.debug(
            "search_memory query=%r count=%d semantic=%s",
            query,
            len(memories),
            has_scores,
        )
        return json.dumps(
            {
                "status": "success",
                "semantic": has_scores,
                "query": query,
                "count": len(memories),
                "memories": memories,
            }
        )

    except Exception as exc:
        logger.exception("search_memory_failed query=%r", query)
        return json.dumps({"status": "failed", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="memory",
        destructive=True,
        icon="bookmark-x",
    )
)
async def delete_memory(ctx: ToolContext, key: str) -> str:
    """Deleta uma memória persistente do namespace do usuário.

    Args:
        key: Chave da memória a deletar

    Returns:
        JSON com status deleted/not_found/failed
    """
    try:
        store = _get_store(ctx)
        namespaces = _memory_namespace_candidates(ctx)
        found_ns: tuple[str, ...] | None = None
        item = None
        for ns in namespaces:
            item = await store.aget(ns, key)
            if item is not None:
                found_ns = ns
                break
        if item is None:
            logger.warning("memory_not_found_for_deletion key=%s", key)
            return json.dumps({"status": "not_found", "key": key})

        for ns in namespaces:
            await store.adelete(ns, key)
        logger.info("memory_deleted key=%s ns=%s", key, found_ns)
        return json.dumps({"status": "deleted", "key": key})

    except Exception as exc:
        logger.exception("delete_memory_failed key=%s", key)
        return json.dumps({"status": "failed", "error": str(exc), "key": key})
