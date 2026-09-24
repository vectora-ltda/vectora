"""Handler de skills — gestão de capacidades reutilizáveis por usuário.

Endpoints (todos exigem autenticação via middleware):
    GET    /skills                 — lista skills instaladas
    POST   /skills                 — instala skill publicada (body: {skill_id})
    DELETE /skills/{skill_id}      — remove skill
    POST   /skills/{skill_id}/verify — revalida SKILL.md (após edição manual)

O user_id vem de ``request.state.user`` (CLI/root → ``"local"``). Skills são
isoladas por usuário (cada um tem sua pasta ``~/.vectora/skills/<id>/``).

Instalações só aceitam identificadores presentes no catálogo validado. A
fonte efetiva permanece no servidor; o cliente nunca envia uma URL ou caminho
arbitrário para execução.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from backend.services import registry_client
from backend.services.importers import preview_skill_config
from backend.vtypes.skill import SkillCatalogEntry, SkillCatalogResponse
from backend.workspace.skills import (
    install_skill,
    list_skills,
    list_wellknown_catalog,
    remove_skill,
    verify_skill,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class ImportPreviewRequest(BaseModel):
    payload: object


class CatalogSkillInstallRequest(BaseModel):
    """Referência a uma skill publicada no catálogo agregado."""

    skill_id: str = Field(min_length=1)
    scope: Literal["user", "workspace", "project", "runtime"] = "user"
    target: str | None = None
    workspace_id: str | None = None
    confirm_unverified: bool = False


class SkillsListResponse(TypedDict):
    skills: list[dict[str, object]]
    total: int


class SkillResponse(TypedDict):
    status: str
    skill: dict[str, object]


class RemovalResponse(TypedDict):
    status: str
    id: str


def _authorized_target(
    request: Request,
    scope: str,
    target: str | None,
    workspace_id: str | None,
) -> str | None:
    """Resolve project/workspace paths only through an authorized workspace."""
    if scope == "user":
        return None
    from backend.api.handlers.workspaces import require_workspace_access

    ws_id = workspace_id or target
    if not ws_id:
        raise HTTPException(
            status_code=400, detail="workspace_id obrigatório para escopo não-usuário"
        )
    ws = require_workspace_access(ws_id, request)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado.")
    if scope == "project":
        return str(ws.cwd)
    if scope == "workspace":
        return str(ws.cwd)
    return target or getattr(request.state, "thread_id", None) or ws_id


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return "local"


@router.get("")
async def list_user_skills(
    request: Request,
    scope: Literal["user", "workspace", "project", "runtime"] = "user",
    target: str | None = None,
    workspace_id: str | None = None,
) -> SkillsListResponse:
    """Lista as skills instaladas para o usuário autenticado."""
    target = _authorized_target(request, scope, target, workspace_id)
    skills = list_skills(_user_id(request), scope, target)
    return {"skills": [s.model_dump() for s in skills], "total": len(skills)}


def _matches_skill_query(
    entry: SkillCatalogEntry,
    *,
    q: str | None,
    category: str | None,
    tags: str | None,
) -> bool:
    if q:
        needle = q.strip().lower()
        if needle:
            haystack = f"{entry.name} {entry.description}".lower()
            if needle not in haystack:
                return False
    if category and entry.category != category:
        return False
    if tags:
        if tags not in entry.tags:
            return False
    return True


def _validate_catalog_entries(
    entries: Sequence[object],
    catalog_source: Literal["remote", "enterprise"],
) -> list[SkillCatalogEntry]:
    validated: list[SkillCatalogEntry] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            logger.warning(
                "skills: entrada de catálogo não-objeto ignorada",
                extra={
                    "catalog_source": catalog_source,
                    "entry_type": type(entry).__name__,
                },
            )
            continue
        try:
            validated.append(
                SkillCatalogEntry.model_validate(
                    {**dict(entry), "catalog_source": catalog_source}
                )
            )
        except ValidationError as exc:
            logger.warning(
                "skills: entrada de catálogo inválida ignorada",
                extra={"catalog_source": catalog_source, "error": str(exc)},
            )
    return validated


@router.get("/catalog", response_model=SkillCatalogResponse)
async def get_skills_catalog(
    q: str | None = None, category: str | None = None, tags: str | None = None
) -> SkillCatalogResponse:
    """Catálogo de skills curadas do registry remoto (D1, `skills_catalog`) —
    distinto de `GET /skills` (que lista as já instaladas). Mescla o
    registry remoto, um registry enterprise opcional e o catálogo local
    ``~/.vectora/skills-wellknown``. `q`/`category`/`tags` filtram em memória
    sobre as fontes já cacheadas — não refazem a requisição remota a cada busca."""
    remote = _validate_catalog_entries(
        await registry_client.fetch_catalog("skills"), "remote"
    )
    enterprise = _validate_catalog_entries(
        await registry_client.fetch_enterprise_catalog("skills"), "enterprise"
    )
    local = await asyncio.to_thread(list_wellknown_catalog)
    by_id = {entry.id: entry for entry in enterprise}
    by_id.update({entry.id: entry for entry in remote})
    by_id.update({entry.id: entry for entry in local})
    entries = list(by_id.values())
    filtered = [
        e for e in entries if _matches_skill_query(e, q=q, category=category, tags=tags)
    ]
    return SkillCatalogResponse(entries=filtered, total=len(filtered))


@router.get("/catalog/status")
async def get_skills_catalog_status() -> registry_client.RegistryStatus:
    """Expõe configuração e disponibilidade do discovery de Skills."""
    return await registry_client.fetch_catalog_status("skills")


async def _resolve_catalog_skill(skill_id: str) -> SkillCatalogEntry | None:
    """Resolve um identificador nas fontes permitidas do catálogo."""
    remote_raw, enterprise_raw = await asyncio.gather(
        registry_client.fetch_catalog("skills"),
        registry_client.fetch_enterprise_catalog("skills"),
    )
    enterprise = _validate_catalog_entries(enterprise_raw, "enterprise")
    remote = _validate_catalog_entries(remote_raw, "remote")
    local = await asyncio.to_thread(list_wellknown_catalog)
    by_id = {entry.id: entry for entry in enterprise}
    by_id.update({entry.id: entry for entry in remote})
    by_id.update({entry.id: entry for entry in local})
    return by_id.get(skill_id.strip())


@router.post("")
async def install_user_skill(
    request: Request, body: CatalogSkillInstallRequest
) -> SkillResponse:
    """Instala uma skill referenciada por um item validado do catálogo."""
    try:
        entry = await _resolve_catalog_skill(body.skill_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail="Skill não encontrada no catálogo."
            )
        target = _authorized_target(request, body.scope, body.target, body.workspace_id)
        skill = install_skill(
            _user_id(request),
            entry.source,
            body.scope,
            target,
            confirm_unverified=body.confirm_unverified,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "skill": skill.model_dump()}


@router.delete("/{skill_id}")
async def delete_user_skill(
    request: Request,
    skill_id: str,
    scope: Literal["user", "workspace", "project", "runtime"] = "user",
    target: str | None = None,
    workspace_id: str | None = None,
) -> RemovalResponse:
    """Remove uma skill instalada."""
    target = _authorized_target(request, scope, target, workspace_id)
    removed = remove_skill(_user_id(request), skill_id, scope, target)
    if not removed:
        raise HTTPException(status_code=404, detail="Skill não encontrada.")
    return {"status": "removed", "id": skill_id}


@router.post("/{skill_id}/verify")
async def verify_user_skill(
    request: Request,
    skill_id: str,
    scope: Literal["user", "workspace", "project", "runtime"] = "user",
    target: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    """Revalida o SKILL.md da skill (útil após edição manual no disco)."""
    target = _authorized_target(request, scope, target, workspace_id)
    return verify_skill(_user_id(request), skill_id, scope, target)


@router.post("/import/preview")
async def preview_skill_import(req: ImportPreviewRequest) -> dict:
    """Return a non-executing preview of supported skill metadata."""
    return preview_skill_config(req.payload)
