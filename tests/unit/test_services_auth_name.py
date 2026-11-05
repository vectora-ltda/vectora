"""Tests para o campo ``name`` em src/services/auth.py.

Cobre:
- ``signup(..., name=...)`` aceita UTF-8 livre, sanitiza e limita.
- ``update_profile(user_id, name=...)`` atualiza e devolve o User novo.
- ``_row_to_user`` tolera bancos antigos sem a coluna (migration idempotente).
- O default de ``name`` é string vazia — comportamento pré-existente
  do signup sem name continua intacto.
"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

# ---------------------------------------------------------------------------
# Fixture — banco isolado por teste (mesmo padrão de test_services_auth.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_auth_name.db")

    import backend.rbac.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_db_conn", None)
    monkeypatch.setattr(
        auth_mod, "_get_secret", lambda: "test-secret-key-name-tests-abcdef"
    )

    async def _patched_get_db():
        if auth_mod._db_conn is not None:
            return auth_mod._db_conn
        conn = await aiosqlite.connect(db_file)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await auth_mod._ensure_schema(conn)
        auth_mod._db_conn = conn
        return conn

    monkeypatch.setattr(auth_mod, "_get_db", _patched_get_db)
    yield

    async def _close():
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    asyncio.run(_close())


# ---------------------------------------------------------------------------
# signup — aceita e sanitiza name
# ---------------------------------------------------------------------------


class TestSignupWithName:
    @pytest.mark.asyncio
    async def test_name_defaults_to_empty(self):
        """Backward-compat: chamadas antigas sem name continuam funcionando."""
        from backend.rbac.auth import signup

        user, _, _ = await signup("noname@example.com", "senhasegura1234")
        assert user.name == ""

    @pytest.mark.asyncio
    async def test_accepts_simple_name(self):
        from backend.rbac.auth import signup

        user, _, _ = await signup(
            "bruno@example.com", "senhasegura1234", name="Bruno Soares"
        )
        assert user.name == "Bruno Soares"

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        from backend.rbac.auth import signup

        user, _, _ = await signup(
            "trim@example.com", "senhasegura1234", name="  Bruno  "
        )
        assert user.name == "Bruno"

    @pytest.mark.asyncio
    async def test_collapses_internal_whitespace(self):
        """Espaços/tabs internos múltiplos viram um espaço só."""
        from backend.rbac.auth import signup

        user, _, _ = await signup(
            "spaces@example.com",
            "senhasegura1234",
            name="Bruno    de   Souza\t Soares",
        )
        assert user.name == "Bruno de Souza Soares"

    @pytest.mark.asyncio
    async def test_caps_at_100_chars(self):
        """Nome muito longo é truncado em 100 caracteres."""
        from backend.rbac.auth import signup

        huge = "A" * 500
        user, _, _ = await signup("huge@example.com", "senhasegura1234", name=huge)
        assert len(user.name) == 100
        assert user.name == "A" * 100

    @pytest.mark.asyncio
    async def test_accepts_utf8_accents_and_special_chars(self):
        """Acentos, ç, apóstrofo, espaços, kanji — tudo entra cru."""
        from backend.rbac.auth import signup

        cases = [
            ("a@x.com", "João D'Ávila"),
            ("b@x.com", "Maria José"),
            ("c@x.com", "François"),
            ("d@x.com", "Iñaki"),
            ("e@x.com", "山田太郎"),
        ]
        for email, name in cases:
            user, _, _ = await signup(email, "senhasegura1234", name=name)
            assert user.name == name, f"falhou para {name!r}"

    @pytest.mark.asyncio
    async def test_name_persists_across_signin(self):
        """Após signup, signin devolve o mesmo name persistido."""
        from backend.rbac.auth import signin, signup

        await signup("persist@example.com", "senhasegura1234", name="Bruno Soares")
        user, _, _ = await signin("persist@example.com", "senhasegura1234")
        assert user.name == "Bruno Soares"


# ---------------------------------------------------------------------------
# update_profile — edição via PATCH /auth/me
# ---------------------------------------------------------------------------


class TestUpdateProfile:
    @pytest.mark.asyncio
    async def test_updates_name(self):
        from backend.rbac.auth import signup, update_profile

        user, _, _ = await signup("upd@example.com", "senhasegura1234", name="Velho")
        updated = await update_profile(user.id, name="Novo Nome")
        assert updated.name == "Novo Nome"
        assert updated.id == user.id
        assert updated.email == user.email

    @pytest.mark.asyncio
    async def test_sanitizes_on_update(self):
        from backend.rbac.auth import signup, update_profile

        user, _, _ = await signup("upd2@example.com", "senhasegura1234")
        updated = await update_profile(user.id, name="   Bruno  Soares   ")
        assert updated.name == "Bruno Soares"

    @pytest.mark.asyncio
    async def test_can_clear_name(self):
        """Atualizar com string vazia → name fica vazio (não rejeita)."""
        from backend.rbac.auth import signup, update_profile

        user, _, _ = await signup(
            "clear@example.com", "senhasegura1234", name="Tinha Nome"
        )
        updated = await update_profile(user.id, name="")
        assert updated.name == ""

    @pytest.mark.asyncio
    async def test_caps_at_100_chars_on_update(self):
        from backend.rbac.auth import signup, update_profile

        user, _, _ = await signup("cap@example.com", "senhasegura1234")
        updated = await update_profile(user.id, name="B" * 500)
        assert len(updated.name) == 100

    @pytest.mark.asyncio
    async def test_raises_for_unknown_user(self):
        from backend.rbac.auth import update_profile

        with pytest.raises(ValueError):
            await update_profile("nope-uuid", name="Ninguém")

    @pytest.mark.asyncio
    async def test_persists_across_get_user_by_id(self):
        """Após update, get_user_by_id devolve o name novo (não cache stale)."""
        from backend.rbac.auth import get_user_by_id, signup, update_profile

        user, _, _ = await signup("fetch@example.com", "senhasegura1234", name="Antigo")
        await update_profile(user.id, name="Atual")
        fetched = await get_user_by_id(user.id)
        assert fetched is not None
        assert fetched.name == "Atual"


# ---------------------------------------------------------------------------
# Migration idempotente — _row_to_user tolera coluna ausente
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    @pytest.mark.asyncio
    async def test_alter_table_is_idempotent(self):
        """Rodar _ensure_schema duas vezes não quebra (ALTER ADD COLUMN x2)."""
        import backend.rbac.auth as auth_mod

        db = await auth_mod._get_db()
        # Primeira chamada já rodou via fixture; rodar de novo deve passar.
        await auth_mod._ensure_schema(db)
        await auth_mod._ensure_schema(db)

        # E ainda assim signup funciona com name.
        from backend.rbac.auth import signup

        user, _, _ = await signup("mig@example.com", "senhasegura1234", name="OK")
        assert user.name == "OK"
