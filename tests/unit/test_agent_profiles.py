"""Perfis de agente customizados — preset reutilizável de instrução/escopo
de tools/modelo/budget que uma task do Kanban pode referenciar em vez de
rodar sempre com a personalidade genérica do orchestrator.
"""

from __future__ import annotations

import pytest

from backend.services import agent_profiles as ap


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Banco SQLite isolado com o schema real da tabela aplicado."""
    from typing import Any

    import aiosqlite

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


class TestCreateProfile:
    async def test_cria_perfil_com_defaults(self, db):
        profile = await ap.create_profile("user-1", "QA Bot")

        assert profile.name == "QA Bot"
        assert profile.status == "active"
        assert profile.tool_scope == []
        assert profile.budget_cents is None

    async def test_cria_perfil_com_tool_scope_valido(self, db):
        profile = await ap.create_profile(
            "user-1", "Reader", tool_scope=["file_read", "grep"]
        )

        assert profile.tool_scope == ["file_read", "grep"]

    async def test_nome_vazio_rejeitado(self, db):
        with pytest.raises(ValueError, match="name"):
            await ap.create_profile("user-1", "  ")

    async def test_budget_negativo_rejeitado(self, db):
        with pytest.raises(ValueError, match="budget_cents"):
            await ap.create_profile("user-1", "X", budget_cents=-1)

    async def test_tool_scope_com_tool_inexistente_rejeitado(self, db):
        with pytest.raises(ValueError, match="tool_scope"):
            await ap.create_profile("user-1", "X", tool_scope=["tool_que_nao_existe"])

    async def test_status_invalido_rejeitado(self, db):
        with pytest.raises(ValueError, match="status"):
            await ap.create_profile("user-1", "X", status="deleted")


class TestListProfiles:
    async def test_lista_so_do_usuario(self, db):
        await ap.create_profile("user-1", "A")
        await ap.create_profile("user-2", "B")

        result = await ap.list_profiles("user-1")

        assert len(result) == 1
        assert result[0].name == "A"

    async def test_usuario_sem_perfis_retorna_lista_vazia(self, db):
        result = await ap.list_profiles("user-sem-perfis")
        assert result == []


class TestUpdateProfile:
    async def test_atualiza_campos(self, db):
        created = await ap.create_profile("user-1", "Original")

        updated = await ap.update_profile(created.id, name="Renomeado", title="T")

        assert updated is not None
        assert updated.name == "Renomeado"
        assert updated.title == "T"

    async def test_perfil_inexistente_retorna_none(self, db):
        result = await ap.update_profile("nao-existe", name="X")
        assert result is None

    async def test_atualizacao_invalida_nao_aplica(self, db):
        """Erro/borda: budget negativo na atualização é rejeitado, perfil
        original não muda."""
        created = await ap.create_profile("user-1", "Original")

        with pytest.raises(ValueError, match="budget_cents"):
            await ap.update_profile(created.id, budget_cents=-5)

        unchanged = await ap.get_profile(created.id)
        assert unchanged is not None
        assert unchanged.budget_cents is None


class TestDeleteProfile:
    async def test_apaga_perfil_existente(self, db):
        created = await ap.create_profile("user-1", "X")

        deleted = await ap.delete_profile(created.id)

        assert deleted is True
        assert await ap.get_profile(created.id) is None

    async def test_perfil_inexistente_retorna_false(self, db):
        deleted = await ap.delete_profile("nao-existe")
        assert deleted is False
