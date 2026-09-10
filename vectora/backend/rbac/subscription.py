"""Gating de features por tier de assinatura (free/pro).

Modelo: uso solo local Ã© sempre `free` (sem conta, sem `VECTORA_TOKEN`).
Capacidades de time â€” chat web multi-usuÃ¡rio, convites, SSO/SAML, storage
escalÃ¡vel (Postgres/Qdrant/Redis), tasks disparadas por webhook â€” exigem
`tier=pro`, que sÃ³ existe com um `VECTORA_TOKEN` vÃ¡lido validado contra o
Supabase (ver `backend/services/license.py`).

`get_current_tier` lÃª o cache local (escrito no boot pelo launcher / pelo loop
de revalidaÃ§Ã£o de 6h em `backend/api/server.py`) â€” nunca faz chamada de rede
por request. `require_pro` Ã© uma dependency do FastAPI: usa 402 Payment
Required (nÃ£o 403) porque a causa Ã© "falta pagar", nÃ£o "sem permissÃ£o".
"""

from __future__ import annotations

import json
import os
import sqlite3

from fastapi import HTTPException

from backend.services.license import LicenseTier, read_cached_status
from backend.settings import settings

UPGRADE_URL = "https://vectora.company/pricing"


def _tier_from_env(user_id: str) -> LicenseTier | None:
    raw = os.getenv("VECTORA_USER_ENTITLEMENTS", "").strip()
    if not raw:
        return None
    try:
        tier = json.loads(raw).get(user_id)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    return tier if tier in {"free", "pro"} else ("free" if tier is None else None)


def _tier_from_store(user_id: str) -> LicenseTier | None:
    database = settings.db_file
    if database is None:
        return None
    try:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT tier FROM vectora_user_entitlements WHERE user_id = ?",
                (user_id,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return "free"
        return None
    except OSError:
        return None
    return "free" if row is None else (row[0] if row[0] in {"free", "pro"} else None)


def get_current_tier(user_id: str | None = None) -> LicenseTier | None:
    """Resolve o tier do usuário autenticado, com isolamento por identidade."""
    if os.getenv("VECTORA_LICENSE_BYPASS", "").strip() == "1" and user_id in {
        None,
        "local",
    }:
        return "pro"
    if user_id in {None, "local"}:
        info = read_cached_status()
        return info.tier if info is not None else "free"
    env_tier = _tier_from_env(user_id)
    return env_tier if env_tier is not None else _tier_from_store(user_id)


def require_pro() -> None:
    """Levanta 402 se a instalaÃ§Ã£o nÃ£o Ã© `pro`. Chame no inÃ­cio do handler,
    mesmo padrÃ£o de ``require_admin`` em ``backend/api/handlers/admin.py``.
    """
    if get_current_tier() != "pro":
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Esta funcionalidade requer o plano Pro.",
                "upgrade_url": UPGRADE_URL,
            },
        )
