"""Handler de skills — gestão de capacidades reutilizáveis por usuário.

Endpoints (todos exigem autenticação via middleware):
    GET    /skills                 — lista skills instaladas
    POST   /skills                 — instala skill (body: {source})
    DELETE /skills/{skill_id}      — remove skill
    POST   /skills/{skill_id}/verify — revalida SKILL.md (após edição manual)
    POST   /skills/publish         — publica no catálogo remoto

O user_id vem de ``request.state.user`` (CLI/root → ``"local"``). Skills são
isoladas por usuário (cada um tem sua pasta ``~/.vectora/skills/<id>/``).

Publicação exige um ``session_token`` de conta vectora.company — mesmo
``VECTORA_TOKEN`` já usado pelo license check (`backend.services.
license._get_token`), mesmo padrão de `backend/api/handlers/
memory_library.py::post_publish`.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.services import registry_client
from backend.services.registry_client import RegistryClientError
from backend.workspace.skills import (
    InstallSkillRequest,
    install_skill,
    list_skills,
    remove_skill,
    verify_skill,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class PublishSkillRequest(BaseModel):
    source: str
    name: str
    description: str
    category: str | None = None
    tags: list[str] = []


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
) -> dict:
    """Lista as skills instaladas para o usuário autenticado."""
    skills = list_skills(_user_id(request), scope, target)
    return {"skills": [s.model_dump() for s in skills], "total": len(skills)}


def _matches_skill_query(
    entry: dict, *, q: str | None, category: str | None, tags: str | None
) -> bool:
    if q:
        needle = q.strip().lower()
        if needle:
            haystack = f"{entry.get('name', '')} {entry.get('description', '')}".lower()
            if needle not in haystack:
                return False
    if category and entry.get("category") != category:
        return False
    if tags:
        entry_tags = entry.get("tags") or []
        if isinstance(entry_tags, str):
            entry_tags = [entry_tags]
        if tags not in entry_tags:
            return False
    return True


@router.get("/catalog")
async def get_skills_catalog(
    q: str | None = None, category: str | None = None, tags: str | None = None
) -> dict:
    """Catálogo de skills curadas do registry remoto (D1, `skills_catalog`) —
    distinto de `GET /skills` (que lista as já instaladas). Sem fallback
    hardcoded local: até hoje não existe skill oficial pré-curada, então
    catálogo vazio é um estado válido (registry fora do ar ou sem seed
    ainda), não erro. `q`/`category`/`tags` filtram em memória sobre o
    catálogo já cacheado por `registry_client` — não refazem a requisição
    remota a cada busca."""
    entries = await registry_client.fetch_catalog("skills")
    filtered = [
        e for e in entries if _matches_skill_query(e, q=q, category=category, tags=tags)
    ]
    return {"entries": filtered, "total": len(filtered)}


@router.post("")
async def install_user_skill(request: Request, body: InstallSkillRequest) -> dict:
    """Instala uma skill (git URL ou path local)."""
    try:
        skill = install_skill(_user_id(request), body.source, body.scope, body.target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "skill": skill.model_dump()}


@router.delete("/{skill_id}")
async def delete_user_skill(
    request: Request,
    skill_id: str,
    scope: Literal["user", "workspace", "project", "runtime"] = "user",
    target: str | None = None,
) -> dict:
    """Remove uma skill instalada."""
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
) -> dict:
    """Revalida o SKILL.md da skill (útil após edição manual no disco)."""
    return verify_skill(_user_id(request), skill_id, scope, target)


@router.post("/publish")
async def publish_user_skill(req: PublishSkillRequest) -> dict:
    """Publica `source` (URL git) no catálogo remoto de skills —
    `verified=false` até curadoria manual de admin."""
    from backend.services import license

    token = license._get_token()
    if not token:
        return {
            "status": "error",
            "error": "Nenhuma conta vectora.company conectada (VECTORA_TOKEN ausente).",
        }
    try:
        remote_id = await registry_client.publish_skill(
            req.name,
            req.description,
            req.source,
            category=req.category,
            tags=req.tags,
            session_token=token,
        )
    except RegistryClientError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "published", "skill_id": remote_id}
