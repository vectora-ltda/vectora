"""Estado e toggle do Vectora Connect por plataforma.

Endpoints:
    GET  /connect/status              — {platform: {configured, enabled, running}}
    POST /connect/{platform}/enabled  — liga/desliga e reconcilia na hora
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connect", tags=["connect"])

PLATFORMS = ("telegram", "discord", "slack", "email")


class SetEnabledRequest(BaseModel):
    enabled: bool


def _require_user(request: Request) -> None:
    if getattr(request.state, "user", None) is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")


@router.get("/status", response_model=dict)
async def get_status(request: Request) -> dict:
    _require_user(request)
    from backend.services.connect import manager

    credentialed = manager.credentialed_platforms()
    running = manager.running_platforms()
    return {
        platform: {
            "configured": platform in credentialed,
            "enabled": manager.is_enabled(platform),
            "running": platform in running,
        }
        for platform in PLATFORMS
    }


@router.post("/{platform}/enabled")
async def set_platform_enabled(
    platform: str, body: SetEnabledRequest, request: Request
) -> dict:
    _require_user(request)
    if platform not in PLATFORMS:
        raise HTTPException(
            status_code=404, detail=f"Plataforma desconhecida: {platform!r}"
        )

    from backend.services.connect import manager

    manager.set_enabled(platform, body.enabled)
    try:
        await manager.sync_adapters()
    except Exception:
        logger.exception("connect: falha ao reconciliar adapters após toggle")

    return {"ok": True}
