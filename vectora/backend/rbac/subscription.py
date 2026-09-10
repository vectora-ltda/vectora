"""Gating de features por tier de assinatura (free/pro).

Modelo: uso solo local é sempre `free` (sem conta, sem `VECTORA_TOKEN`).
Capacidades de time — chat web multi-usuário, convites, SSO/SAML, storage
escalável (Postgres/Qdrant/Redis), tasks disparadas por webhook — exigem
`tier=pro`, que só existe com um `VECTORA_TOKEN` válido validado contra o
Supabase (ver `backend/services/license.py`).

`get_current_tier` lê o cache local (escrito no boot pelo launcher / pelo loop
de revalidação de 6h em `backend/api/server.py`) — nunca faz chamada de rede
por request. `require_pro` é uma dependency do FastAPI: usa 402 Payment
Required (não 403) porque a causa é "falta pagar", não "sem permissão".
"""

from __future__ import annotations

import os

from fastapi import HTTPException

from backend.services.license import LicenseTier, read_cached_status

UPGRADE_URL = "https://vectora.company/pricing"


def get_current_tier(user_id: str | None = None) -> LicenseTier:
    """Tier atual da instalação. Sem cache (nunca validou) → `free`.

    ``VECTORA_LICENSE_BYPASS=1`` força `pro` direto (dev/CI) — mesmo escape
    hatch de ``license.py::validate_license_async``, sem depender de um cache
    pré-populado (esta função é síncrona e não valida remoto).
    """
    if os.getenv("VECTORA_LICENSE_BYPASS", "").strip() == "1":
        return "pro"
    info = read_cached_status()
    return info.tier if info is not None else "free"


def require_pro() -> None:
    """Levanta 402 se a instalação não é `pro`. Chame no início do handler,
    mesmo padrão de ``require_admin`` em ``backend/api/handlers/admin.py``.
    """
    if get_current_tier() != "pro":
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Esta funcionalidade requer o plano Pro.",
                "upgrade_url": UPGRADE_URL,
            },
        )
