"""Handler REST de perfis de agente customizados.

Endpoints (todos exigem autenticação, escopados ao usuário do request):
    GET    /agent-profiles          — lista perfis do usuário
    POST   /agent-profiles          — cria perfil
    PATCH  /agent-profiles/{id}     — atualiza perfil
    DELETE /agent-profiles/{id}     — remove perfil
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.services.agent_profiles import AgentProfile

router = APIRouter(prefix="/agent-profiles", tags=["agent-profiles"])


def _user_id(request: Request) -> str:
    """Extrai o user_id do request autenticado, ou 'local' em modo CLI —
    mesmo padrão de `workspaces.py::_user_id`."""
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return "local"


class AgentProfileOut(BaseModel):
    id: str
    name: str
    title: str
    icon: str
    color: str
    instruction_path: str | None
    tool_scope: list[str]
    model_override: str | None
    budget_cents: int | None
    status: str
    created_at: str | None
    updated_at: str | None


class CreateAgentProfileRequest(BaseModel):
    name: str
    title: str = ""
    icon: str = ""
    color: str = ""
    instruction_path: str | None = None
    tool_scope: list[str] = []
    model_override: str | None = None
    budget_cents: int | None = None
    status: str = "active"


class UpdateAgentProfileRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    icon: str | None = None
    color: str | None = None
    instruction_path: str | None = None
    tool_scope: list[str] | None = None
    model_override: str | None = None
    budget_cents: int | None = None
    status: str | None = None


def _to_out(p: AgentProfile) -> AgentProfileOut:
    return AgentProfileOut(
        id=p.id,
        name=p.name,
        title=p.title,
        icon=p.icon,
        color=p.color,
        instruction_path=p.instruction_path,
        tool_scope=p.tool_scope,
        model_override=p.model_override,
        budget_cents=p.budget_cents,
        status=p.status,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


async def _require_own_profile(profile_id: str, user_id: str) -> AgentProfile:
    from backend.services.agent_profiles import get_profile

    profile = await get_profile(profile_id)
    if profile is None or profile.user_id != user_id:
        raise HTTPException(status_code=404, detail="Perfil não encontrado")
    return profile


@router.get("", response_model=list[AgentProfileOut])
async def get_agent_profiles(request: Request) -> list[AgentProfileOut]:
    from backend.services.agent_profiles import list_profiles

    profiles = await list_profiles(_user_id(request))
    return [_to_out(p) for p in profiles]


@router.post("", response_model=AgentProfileOut, status_code=201)
async def post_agent_profile(
    request: Request, body: CreateAgentProfileRequest
) -> AgentProfileOut:
    from backend.services.agent_profiles import create_profile

    try:
        profile = await create_profile(
            _user_id(request),
            body.name,
            title=body.title,
            icon=body.icon,
            color=body.color,
            instruction_path=body.instruction_path,
            tool_scope=body.tool_scope,
            model_override=body.model_override,
            budget_cents=body.budget_cents,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_out(profile)


@router.patch("/{profile_id}", response_model=AgentProfileOut)
async def patch_agent_profile(
    request: Request, profile_id: str, body: UpdateAgentProfileRequest
) -> AgentProfileOut:
    from backend.services.agent_profiles import update_profile

    await _require_own_profile(profile_id, _user_id(request))

    changes: dict[str, Any] = {
        k: v for k, v in body.model_dump().items() if v is not None
    }
    try:
        updated = await update_profile(profile_id, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado")
    return _to_out(updated)


@router.delete("/{profile_id}", status_code=204)
async def delete_agent_profile(request: Request, profile_id: str) -> None:
    from backend.services.agent_profiles import delete_profile

    await _require_own_profile(profile_id, _user_id(request))
    await delete_profile(profile_id)
