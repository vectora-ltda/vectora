"""_ensure_infra (backend/services/agent_factory.py) — store Postgres (D1).

Antes desta migração, TODO store era SQLite mesmo em storage_mode="complete"
com Postgres configurado — Qdrant/Redis já eram reais nesse modo, mas o
estado do agente (memória) ficava preso no SQLite de qualquer forma. Agora
"complete" + postgres_dsn usa VectoraPostgresStore (nativo, asyncpg);
qualquer falha ao abrir Postgres degrada pro SQLite (nunca impede a sessão
de iniciar).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services import agent_factory


@pytest.fixture(autouse=True)
async def _reset_infra_singletons():
    agent_factory._store = None
    agent_factory._store_ctx = None
    agent_factory._session_store_pool = None
    agent_factory._session_store = None
    agent_factory._approval_gate = None
    yield
    # aclose() fecha de verdade o AsyncConnectionPool que _ensure_infra abre
    # contra tmp_path/sessions.db — zerar as globais direto (sem fechar)
    # vazava a conexão aiosqlite e a thread do worker a cada teste deste
    # arquivo, acumulando threads presas até travar a criação de conexão em
    # outro teste da suíte sob pressão de CI.
    await agent_factory.aclose()


@pytest.mark.asyncio
async def test_ensure_infra_uses_postgres_store_in_complete_mode(tmp_path):
    fake_pool = MagicMock()
    fake_store = MagicMock()
    fake_store.setup = AsyncMock()

    fake_settings = MagicMock(
        storage_mode="complete", postgres_dsn="postgresql://x/y", vectora_home=tmp_path
    )

    with (
        patch("backend.settings.settings", fake_settings),
        patch(
            "backend.services.license.get_effective_storage_mode",
            return_value="complete",
        ),
        patch("asyncpg.create_pool", new=AsyncMock(return_value=fake_pool)),
        patch(
            "backend.persistence.native.postgres_store.VectoraPostgresStore",
            return_value=fake_store,
        ),
        patch("backend.llm.backends._build_index", return_value=None),
    ):
        await agent_factory._ensure_infra()

    assert agent_factory._store is fake_store
    fake_store.setup.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_infra_falls_back_to_sqlite_when_postgres_fails(tmp_path):
    """Edge — DSN ruim/Postgres fora do ar não impede a sessão de iniciar."""
    fake_settings = MagicMock(
        storage_mode="complete",
        postgres_dsn="postgresql://bad/dsn",
        vectora_home=tmp_path,
    )
    fake_store = MagicMock()

    with (
        patch("backend.settings.settings", fake_settings),
        patch(
            "backend.services.license.get_effective_storage_mode",
            return_value="complete",
        ),
        patch(
            "asyncpg.create_pool",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ),
        patch(
            "backend.llm.backends.build_store", new=AsyncMock(return_value=fake_store)
        ),
    ):
        await agent_factory._ensure_infra()

    # Assert positivo: precisa ser de fato o store de fallback construído por
    # build_store, não só "qualquer coisa que não seja MagicMock" — esse
    # assert fraco deixaria passar até um objeto de tipo errado.
    assert agent_factory._store is fake_store
    assert agent_factory._store_ctx is None


@pytest.mark.asyncio
async def test_ensure_infra_uses_sqlite_in_lite_mode(tmp_path):
    """Modo lite (default) nunca tenta abrir Postgres."""
    fake_settings = MagicMock(
        storage_mode="lite", postgres_dsn=None, vectora_home=tmp_path
    )
    fake_store = MagicMock()

    with (
        patch("backend.settings.settings", fake_settings),
        patch(
            "backend.llm.backends.build_store", new=AsyncMock(return_value=fake_store)
        ),
    ):
        await agent_factory._ensure_infra()

    assert agent_factory._store is fake_store
    assert agent_factory._store_ctx is None
