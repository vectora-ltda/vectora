"""Handler REST de memórias do usuário.

Endpoints:
    GET    /memory                — lista memórias do usuário autenticado (paginado)
    GET    /memory/journey        — o que o agente aprendeu sobre o usuário
    GET    /memory/{key}          — lê uma memória específica
    PUT    /memory/{key}          — edita conteúdo de uma memória
    DELETE /memory/{key}          — deleta uma memória específica
    DELETE /memory                — limpa todas as memórias do usuário

Todos os endpoints exigem autenticação (injetada via middleware).
O user_id é extraído de ``request.state.user.id``.

Lê e escreve o mesmo store nativo usado pelas memory tools do agente
(``backend/tools/memory.py``), incluindo os escopos autenticados de
organização e workspace quando o middleware os disponibiliza — painel de
configurações e agente compartilham as mesmas memórias sem cruzar tenants.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.persistence.native.store import is_store_value_expired

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

# asearch/aput/aget vêm do protocolo StoreBackend (backend/storage/protocols.py)
# — sem tipo público estável para o retorno de asearch importar aqui sem
# acoplar à implementação concreta (VectoraStore/VectoraPostgresStore).
_LIST_ALL_LIMIT = 1000


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MemoryItem(BaseModel):
    key: str
    content: str
    metadata: dict[str, Any] = {}
    updated_at: str = ""


class ListMemoriesResponse(BaseModel):
    memories: list[MemoryItem]
    total: int


class CreateMemoryRequest(BaseModel):
    key: str
    content: str
    metadata: dict[str, Any] = {}


class CreateMemoryResponse(BaseModel):
    status: str
    key: str


class UpdateMemoryRequest(BaseModel):
    content: str
    metadata: dict[str, Any] = {}


class UpdateMemoryResponse(BaseModel):
    status: str
    key: str


class DeleteMemoryResponse(BaseModel):
    status: str
    key: str | None = None
    deleted: int | None = None


class JourneyFact(BaseModel):
    key: str
    content: str
    source: str = ""
    updated_at: str = ""


class JourneySkill(BaseModel):
    id: str
    name: str
    description: str
    installed_at: str = ""


class JourneyResponse(BaseModel):
    facts: list[JourneyFact]
    skills: list[JourneySkill]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_id(request: Request) -> str:
    """Extrai o user_id bruto do request autenticado.

    Em modo CLI (root local sem auth), usa ``"local"`` — mesmo default de
    ``backend/tools/memory.py::_user_id_from_config``.
    """
    user = getattr(request.state, "user", None)
    if user is not None and hasattr(user, "id"):
        return str(user.id)
    return "local"


def _namespace(request: Request) -> tuple[str, ...]:
    """Namespace do store — idêntico ao usado pelas memory tools do agente."""
    namespace: list[str] = ["user", _user_id(request)]
    org_id = str(getattr(request.state, "org_id", "") or "")
    workspace_id = str(getattr(request.state, "workspace_id", "") or "")
    if org_id:
        namespace.extend(("org", org_id))
    if workspace_id:
        namespace.extend(("workspace", workspace_id))
    namespace.append("memories")
    return tuple(namespace)


def _namespace_candidates(request: Request) -> list[tuple[str, ...]]:
    """Read scoped records first and retain legacy user-memory compatibility."""
    scoped = _namespace(request)
    legacy = ("user", _user_id(request), "memories")
    return [scoped] if scoped == legacy else [scoped, legacy]


async def _store() -> Any:
    """Executa a operação  store."""
    from backend.services.agent_factory import get_store

    return await get_store()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=CreateMemoryResponse, status_code=201)
async def create_memory(
    request: Request,
    body: CreateMemoryRequest,
) -> CreateMemoryResponse:
    """Cria uma nova memória manualmente (pelo painel de configurações)."""
    try:
        ns = _namespace(request)
        store = await _store()

        existing = await store.aget(ns, body.key)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Memória '{body.key}' já existe. Use PUT para editar.",
            )

        await store.aput(
            ns,
            body.key,
            {
                "key": body.key,
                "content": body.content,
                "metadata": body.metadata,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        return CreateMemoryResponse(status="created", key=body.key)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("create_memory failed: key=%s", body.key)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("", response_model=ListMemoriesResponse)
async def list_memories(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListMemoriesResponse:
    """Lista memórias do usuário autenticado, ordenadas por data de atualização."""
    try:
        store = await _store()
        items = []
        seen: set[str] = set()
        for ns in _namespace_candidates(request):
            for item in await store.asearch(ns, limit=_LIST_ALL_LIMIT):
                if item.key not in seen and not is_store_value_expired(item.value):
                    seen.add(item.key)
                    items.append(item)

        all_mems = [
            MemoryItem(
                key=item.key,
                content=item.value.get("content", ""),
                metadata=item.value.get("metadata") or {},
                updated_at=item.value.get("updated_at", ""),
            )
            for item in items
        ]
        all_mems.sort(key=lambda m: m.updated_at, reverse=True)

        page = all_mems[offset : offset + limit]
        return ListMemoriesResponse(
            memories=page,
            total=len(all_mems),
        )
    except Exception as exc:
        logger.exception("list_memories failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/journey", response_model=JourneyResponse)
async def get_journey(request: Request) -> JourneyResponse:
    """O que o Remember aprendeu sobre este usuário: fatos gravados com a
    tag ``user_model`` + skills geradas pelo learning loop.

    Precisa ficar declarado **antes** de ``/{key}`` — a rota dinâmica
    casaria com "journey" e devolveria 404 de memória inexistente.

    Falha de leitura degrada pra lista vazia em vez de 500: um painel de
    leitura vazio é aceitável, derrubar o Memory tab inteiro não.
    """
    facts: list[JourneyFact] = []
    skills: list[JourneySkill] = []

    try:
        store = await _store()
        items = []
        seen: set[str] = set()
        for ns in _namespace_candidates(request):
            for item in await store.asearch(ns, limit=_LIST_ALL_LIMIT):
                if item.key not in seen and not is_store_value_expired(item.value):
                    seen.add(item.key)
                    items.append(item)
        for item in items:
            metadata = item.value.get("metadata") or {}
            if metadata.get("tag") != "user_model":
                continue
            facts.append(
                JourneyFact(
                    key=item.key,
                    content=item.value.get("content", ""),
                    source=str(metadata.get("source", "")),
                    updated_at=item.value.get("updated_at", ""),
                )
            )
        facts.sort(key=lambda f: f.updated_at, reverse=True)
    except Exception:
        logger.exception("get_journey: falha ao ler fatos do store")

    try:
        from backend.workspace.skills import list_skills

        skills = [
            JourneySkill(
                id=s.id,
                name=s.name,
                description=s.description,
                installed_at=s.installed_at,
            )
            for s in list_skills(_user_id(request))
            if s.source == "learning-loop"
        ]
        skills.sort(key=lambda s: s.installed_at, reverse=True)
    except Exception:
        logger.exception("get_journey: falha ao listar skills aprendidas")

    return JourneyResponse(facts=facts, skills=skills)


@router.get("/{key}", response_model=MemoryItem)
async def get_memory_by_key(request: Request, key: str) -> MemoryItem:
    """Retorna uma memória específica pelo key."""
    try:
        store = await _store()
        item = None
        for ns in _namespace_candidates(request):
            candidate = await store.aget(ns, key)
            if candidate is not None and not is_store_value_expired(candidate.value):
                item = candidate
                break
        if item is None:
            raise HTTPException(
                status_code=404, detail=f"Memória '{key}' não encontrada"
            )
        return MemoryItem(
            key=key,
            content=item.value.get("content", ""),
            metadata=item.value.get("metadata") or {},
            updated_at=item.value.get("updated_at", ""),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get_memory_by_key failed: key=%s", key)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/{key}", response_model=UpdateMemoryResponse)
async def update_memory(
    request: Request,
    key: str,
    body: UpdateMemoryRequest,
) -> UpdateMemoryResponse:
    """Edita o conteúdo de uma memória existente."""
    try:
        ns = _namespace(request)
        store = await _store()

        existing = await store.aget(ns, key)
        if existing is None:
            raise HTTPException(
                status_code=404, detail=f"Memória '{key}' não encontrada"
            )

        await store.aput(
            ns,
            key,
            {
                "key": key,
                "content": body.content,
                "metadata": body.metadata or existing.value.get("metadata") or {},
                "updated_at": datetime.now(UTC).isoformat(),
                **{
                    field: existing.value[field]
                    for field in ("valid_until", "ttl_days", "category")
                    if field in existing.value
                },
            },
        )
        return UpdateMemoryResponse(status="updated", key=key)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("update_memory failed: key=%s", key)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{key}", response_model=DeleteMemoryResponse)
async def delete_memory_by_key(request: Request, key: str) -> DeleteMemoryResponse:
    """Deleta uma memória específica pelo key."""
    try:
        store = await _store()
        namespaces = _namespace_candidates(request)
        for ns in namespaces:
            if await store.aget(ns, key) is not None:
                break
        else:
            raise HTTPException(
                status_code=404, detail=f"Memória '{key}' não encontrada"
            )
        # Delete every candidate, including copies hidden by expiry filtering.
        for ns in namespaces:
            await store.adelete(ns, key)
        return DeleteMemoryResponse(status="deleted", key=key)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("delete_memory_by_key failed: key=%s", key)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("", response_model=DeleteMemoryResponse)
async def clear_all_memories(request: Request) -> DeleteMemoryResponse:
    """Limpa todas as memórias do usuário autenticado."""
    try:
        store = await _store()
        items = []
        for ns in _namespace_candidates(request):
            try:
                items.extend(
                    await store.asearch(ns, limit=_LIST_ALL_LIMIT, include_expired=True)
                )
            except TypeError:
                items.extend(await store.asearch(ns, limit=_LIST_ALL_LIMIT))
        count = 0
        for item in items:
            await store.adelete(item.namespace, item.key)
            count += 1
        logger.info("clear_all_memories: %d removidas", count)
        return DeleteMemoryResponse(status="cleared", deleted=count)
    except Exception as exc:
        logger.exception("clear_all_memories failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
