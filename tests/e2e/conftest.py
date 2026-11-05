"""Fixtures compartilhadas por tests/e2e/ — especificamente os testes live
(marker `live`) que conversam com um backend real via HTTP.

Reaproveita ``spawned_backend`` de ``tests/conftest.py`` (visível aqui por
estar na raiz da árvore de fixtures) em vez de duplicar o subprocesso.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def live_backend(spawned_backend: tuple[str, int]) -> str:
    """``spawned_backend`` já configurado em modo local (sem auth obrigatória).

    ``VECTORA_HOME`` isola o ``backend.db`` (checkpoints do chat — deriva de
    ``settings.vectora_home``, ver ``backend/settings.py``), mas **não**
    isola ``~/.vectora/checkpoints.db`` (preferências de runtime/auth —
    ``backend/workspace/runtime_settings.py`` hardcoda ``Path.home()``,
    bug real documentado à parte). Nesta máquina de desenvolvimento essa
    instância real já roda em modo local (``auth_required=False``
    persistido de uso real anterior) — checamos via ``GET /settings/flags``
    (rota pública) e só chamamos ``POST /auth/setup-local`` se de fato
    ainda for necessário, para não sobrescrever ``local_user_name``/
    ``company`` reais do usuário à toa.
    """
    base_url, _port = spawned_backend
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        flags = (await client.get("/settings/flags")).json()
        if flags.get("auth_required"):
            resp = await client.post(
                "/auth/setup-local", json={"name": "Local User", "company": ""}
            )
            resp.raise_for_status()
    return base_url
