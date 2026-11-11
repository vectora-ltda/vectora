"""Gate Services-hosted features by subscription tier.

Features executed by the local installation, including self-hosting,
operator-configured storage, invitations, and automations, remain available
on Free. The app uses `require_pro` only for flows that depend on Services,
such as Connect.

`get_current_tier` reads the local cache; `require_pro` returns HTTP 402 when
a Services-hosted feature requires Pro.
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
    if tier is None:
        return None
    return tier if tier in {"free", "pro"} else None


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
            return None
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
    """Raise HTTP 402 when a Services-hosted feature requires Pro."""
    if get_current_tier() != "pro":
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Esta funcionalidade requer o plano Pro.",
                "upgrade_url": UPGRADE_URL,
            },
        )
