"""Endpoints de gerenciamento do gateway local (ex-relay).

G1 — GET  /gateway/status  → estado seguro da conexão (connected, state, detail)
G2 — POST /gateway/revoke  → revoga o token no Worker e limpa ~/.vectora/gateway_token
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import aiohttp
from fastapi import APIRouter, HTTPException, Request

from backend.services.gateway.token import load_token
from backend.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gateway", tags=["gateway"])

_TOKEN_PATH = settings.vectora_home / "gateway_token"
_GATEWAY_BASE = os.environ.get("GATEWAY_URL", "https://gateway.vectora.chat")


def _require_auth(request: Request) -> None:
    if getattr(request.state, "user", None) is None:
        raise HTTPException(status_code=401, detail="Não autenticado")


def _gateway_token() -> str | None:
    return load_token(_TOKEN_PATH)


@router.get("/status")
async def gateway_status(request: Request) -> dict:
    """Estado do gateway, distinguindo "nunca conectou" (normal, nada errado)
    de "tentou e falhou" (problema real — gateway fora do ar ou mal
    configurado). Sem essa distinção, os dois casos mostravam a mesma
    mensagem "desconectado" pro usuário, que não conseguia saber se
    precisava agir ou se era só o estado inicial esperado.
    """
    _require_auth(request)
    token = _gateway_token()
    if not token:
        return {
            "connected": False,
            "state": "never_connected",
            "detail": None,
        }

    detail: str | None = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GATEWAY_BASE}/health/{token}",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    connected = bool(data.get("connected", False))
                else:
                    connected = False
                    detail = f"Gateway respondeu {resp.status}"
    except Exception as exc:
        connected = False
        detail = str(exc)

    return {
        "connected": connected,
        "state": "connected" if connected else "error",
        "detail": detail,
    }


@router.post("/revoke")
async def gateway_revoke(request: Request) -> dict:
    _require_auth(request)
    token = _gateway_token()
    if not token:
        raise HTTPException(status_code=404, detail="Nenhum token de gateway ativo")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{_GATEWAY_BASE}/gateway/session/{token}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status not in (200, 404):
                    logger.warning(
                        "gateway: revoke retornou %d para token %s", resp.status, token
                    )
    except Exception as exc:
        logger.warning("gateway: falha ao revogar token no Worker — %s", exc)

    _TOKEN_PATH.unlink(missing_ok=True)
    logger.info("gateway: token %s revogado e removido de %s", token, _TOKEN_PATH)
    return {"revoked": True}
