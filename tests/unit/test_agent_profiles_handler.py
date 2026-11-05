"""Handler REST de perfis de agente customizados."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.handlers.agent_profiles import (
    CreateAgentProfileRequest,
    UpdateAgentProfileRequest,
    delete_agent_profile,
    get_agent_profiles,
    patch_agent_profile,
    post_agent_profile,
)


def _req(user_id: str | None = "user-1"):
    user = SimpleNamespace(id=user_id) if user_id else None
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from typing import Any

    import aiosqlite

    from backend.services import agent_profiles as ap

    caminho = tmp_path / "agent_profiles.db"
    conn: Any = await aiosqlite.connect(caminho)
    conn.row_factory = lambda c, r: dict(
        zip([col[0] for col in c.description], r, strict=False)
    )
    await conn.executescript(
        """
        CREATE TABLE vectora_agent_profiles (
            id                TEXT PRIMARY KEY,
            user_id           TEXT NOT NULL,
            name              TEXT NOT NULL,
            title             TEXT NOT NULL DEFAULT '',
            icon              TEXT NOT NULL DEFAULT '',
            color             TEXT NOT NULL DEFAULT '',
            instruction_path  TEXT,
            tool_scope        TEXT NOT NULL DEFAULT '[]',
            model_override    TEXT,
            budget_cents      INTEGER,
            status            TEXT NOT NULL DEFAULT 'active',
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    await conn.commit()

    async def _get_db():
        return conn

    monkeypatch.setattr(ap, "_get_db", _get_db)
    yield conn
    await conn.close()


class TestGetAgentProfiles:
    async def test_lista_perfis_do_usuario(self, db):
        await post_agent_profile(_req("user-1"), CreateAgentProfileRequest(name="A"))
        await post_agent_profile(_req("user-2"), CreateAgentProfileRequest(name="B"))

        result = await get_agent_profiles(_req("user-1"))

        assert len(result) == 1
        assert result[0].name == "A"


class TestPostAgentProfile:
    async def test_cria_perfil(self, db):
        created = await post_agent_profile(
            _req("user-1"), CreateAgentProfileRequest(name="QA Bot")
        )

        assert created.name == "QA Bot"
        assert created.status == "active"

    async def test_nome_invalido_vira_422(self, db):
        with pytest.raises(HTTPException) as exc_info:
            await post_agent_profile(
                _req("user-1"), CreateAgentProfileRequest(name=" ")
            )

        assert exc_info.value.status_code == 422


class TestPatchAgentProfile:
    async def test_atualiza_perfil_proprio(self, db):
        created = await post_agent_profile(
            _req("user-1"), CreateAgentProfileRequest(name="X")
        )

        updated = await patch_agent_profile(
            _req("user-1"), created.id, UpdateAgentProfileRequest(name="Y")
        )

        assert updated.name == "Y"

    async def test_atualizar_perfil_de_outro_usuario_vira_404(self, db):
        """Erro/borda: usuário B não pode nem enxergar (muito menos editar)
        um perfil de outro usuário."""
        created = await post_agent_profile(
            _req("user-1"), CreateAgentProfileRequest(name="X")
        )

        with pytest.raises(HTTPException) as exc_info:
            await patch_agent_profile(
                _req("user-2"), created.id, UpdateAgentProfileRequest(name="Y")
            )

        assert exc_info.value.status_code == 404


class TestDeleteAgentProfile:
    async def test_apaga_perfil_proprio(self, db):
        created = await post_agent_profile(
            _req("user-1"), CreateAgentProfileRequest(name="X")
        )

        await delete_agent_profile(_req("user-1"), created.id)

        result = await get_agent_profiles(_req("user-1"))
        assert result == []

    async def test_apagar_perfil_de_outro_usuario_vira_404(self, db):
        created = await post_agent_profile(
            _req("user-1"), CreateAgentProfileRequest(name="X")
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_agent_profile(_req("user-2"), created.id)

        assert exc_info.value.status_code == 404
