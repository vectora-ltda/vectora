"""Handler de política de tools do usuário autenticado.

Endpoints (exigem auth via middleware):
    GET /tools/policy   — tools desabilitadas do usuário + lista de built-ins
    PUT /tools/policy   — define as tools desabilitadas do usuário

O controle administrativo (override por outro usuário) vive em
``handlers/admin.py`` (``/admin/users/{id}/tools``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.rbac import tool_policy

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolPolicyBody(BaseModel):
    disabled: list[str] = []


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return "local"


def _all_tool_names() -> list[str]:
    from backend.nodes.tools import ALL_TOOL_NAMES

    return sorted(ALL_TOOL_NAMES)


@router.get("/policy")
async def get_policy(request: Request) -> dict:
    """Política de tools do usuário atual."""
    uid = _user_id(request)
    from backend.nodes.tools import ALL_TOOL_SPECS

    schemas = {
        spec.name: {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.openai_schema()["function"]["parameters"],
            },
        }
        for spec in ALL_TOOL_SPECS
    }
    return {
        "disabled": tool_policy.get_disabled(uid),
        "available": _all_tool_names(),
        "schemas": schemas,
    }


@router.get("/usage")
async def get_usage(request: Request) -> dict:
    """Retorna somente contagens da identidade autenticada na janela de 7 dias."""
    from backend.services.tool_usage import aggregate_last_7d

    counts = await aggregate_last_7d(_user_id(request))
    return {
        "window_days": 7,
        "usage": {name: counts.get(name, 0) for name in _all_tool_names()},
    }


@router.put("/policy")
async def put_policy(request: Request, body: ToolPolicyBody) -> dict:
    """Define as tools desabilitadas do usuário atual."""
    valid = set(_all_tool_names())
    unknown = [n for n in body.disabled if n not in valid]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Tools desconhecidas: {sorted(unknown)}"
        )
    tool_policy.set_disabled(_user_id(request), body.disabled)
    return {"status": "ok", "disabled": tool_policy.get_disabled(_user_id(request))}
