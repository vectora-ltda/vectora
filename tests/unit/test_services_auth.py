"""Testes unitários para src/services/auth.py.

Cobre:
- hash_password / verify_password
- create_access_token / decode_access_token
- signup: primeiro usuário vira root, segundo vira member, email duplicado
- signin: credenciais válidas e inválidas, audit log
- refresh_tokens: rotação, token revogado, token expirado
- signout: revoga refresh token
- get_user_by_id: encontrado e não encontrado
- change_password: senha atual errada, nova senha curta, revogação de refresh tokens
- env overrides: set / get / delete
- has_users: empty DB e após signup
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """Cada teste usa um banco SQLite temporário isolado."""
    db_file = str(tmp_path / "test_auth.db")

    # Resetar estado global do módulo auth antes de cada teste
    import backend.rbac.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_db_conn", None)
    monkeypatch.setattr(
        auth_mod, "_get_secret", lambda: "test-secret-key-for-unit-tests-xx"
    )

    # Apontar _get_db para o banco temporário
    from pathlib import Path

    import aiosqlite

    original_get_db = auth_mod._get_db

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
    # Fecha a conexão após o teste
    import asyncio

    async def _close():
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    asyncio.run(_close())


# ---------------------------------------------------------------------------
# Hash de senha
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        from backend.rbac.auth import hash_password

        h = hash_password("senhasegura123")
        assert h != "senhasegura123"
        assert len(h) > 20

    def test_verify_correct_password(self):
        from backend.rbac.auth import hash_password, verify_password

        h = hash_password("minhasenha456!")
        assert verify_password("minhasenha456!", h) is True

    def test_verify_wrong_password(self):
        from backend.rbac.auth import hash_password, verify_password

        h = hash_password("correta123456")
        assert verify_password("errada123456", h) is False

    def test_two_hashes_of_same_password_differ(self):
        from backend.rbac.auth import hash_password

        h1 = hash_password("mesmasenha123")
        h2 = hash_password("mesmasenha123")
        assert h1 != h2  # salt aleatório garante hashes distintos


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TestJWT:
    def test_create_and_decode_access_token(self):
        from backend.rbac.auth import User, create_access_token, decode_access_token

        user = User(
            id="u-1",
            email="test@example.com",
            role="member",
            created_at="2024-01-01T00:00:00+00:00",
        )
        token = create_access_token(user)
        payload = decode_access_token(token)

        assert payload["sub"] == "u-1"
        assert payload["email"] == "test@example.com"
        assert payload["role"] == "member"

    def test_tampered_token_raises(self):
        from jwt import PyJWTError as JWTError

        from backend.rbac.auth import User, create_access_token, decode_access_token

        user = User(
            id="u-1",
            email="x@x.com",
            role="viewer",
            created_at="2024-01-01T00:00:00+00:00",
        )
        token = create_access_token(user)
        tampered = token[:-4] + "XXXX"

        with pytest.raises(JWTError):
            decode_access_token(tampered)

    def test_expired_token_raises(self):
        import time
        from datetime import UTC, datetime, timedelta

        import jwt
        from jwt import PyJWTError as JWTError

        # Emite token já expirado
        payload = {
            "sub": "u-x",
            "email": "x@x.com",
            "role": "member",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) - timedelta(seconds=1),
        }
        expired_token = jwt.encode(
            payload, "test-secret-key-for-unit-tests-xx", algorithm="HS256"
        )

        from backend.rbac.auth import decode_access_token

        with pytest.raises(JWTError):
            decode_access_token(expired_token)


# ---------------------------------------------------------------------------
# has_users
# ---------------------------------------------------------------------------


class TestHasUsers:
    @pytest.mark.asyncio
    async def test_empty_db_has_no_users(self):
        from backend.rbac.auth import has_users

        assert await has_users() is False

    @pytest.mark.asyncio
    async def test_after_signup_has_users(self):
        from backend.rbac.auth import has_users, signup

        await signup("first@example.com", "senhasegura1234")
        assert await has_users() is True


# ---------------------------------------------------------------------------
# signup
# ---------------------------------------------------------------------------


class TestSignup:
    @pytest.mark.asyncio
    async def test_first_user_is_root(self):
        from backend.rbac.auth import signup

        user, access_token, refresh_token = await signup(
            "root@example.com", "senharootok1234"
        )
        assert user.role == "root"
        assert user.email == "root@example.com"
        assert access_token
        assert refresh_token

    @pytest.mark.asyncio
    async def test_second_user_is_member(self):
        from backend.rbac.auth import signup

        await signup("root@example.com", "senharootok1234")
        user, _, _ = await signup("member@example.com", "senhameter1234")
        assert user.role == "member"

    @pytest.mark.asyncio
    async def test_duplicate_email_raises(self):
        from backend.rbac.auth import signup

        await signup("dup@example.com", "senha123456789")
        with pytest.raises(ValueError, match="E-mail já cadastrado"):
            await signup("dup@example.com", "outrasenha123")

    @pytest.mark.asyncio
    async def test_short_password_raises(self):
        from backend.rbac.auth import signup

        with pytest.raises(ValueError, match="mínimo 8"):
            await signup("short@example.com", "curta")

    @pytest.mark.asyncio
    async def test_email_stored_lowercase(self):
        from backend.rbac.auth import signup

        user, _, _ = await signup("Upper@Example.COM", "senhasegura1234")
        assert user.email == "upper@example.com"


# ---------------------------------------------------------------------------
# provision_or_login_sso (SSO/OIDC)
# ---------------------------------------------------------------------------


class TestProvisionOrLoginSSO:
    @pytest.mark.asyncio
    async def test_usuario_novo_e_provisionado_como_root_primeiro_acesso(self):
        from backend.rbac.auth import get_user_by_id, provision_or_login_sso

        user, access_token, refresh_token = await provision_or_login_sso(
            "dev@example.com", name="Dev SSO"
        )

        assert user.role == "root"
        assert user.email == "dev@example.com"
        assert access_token
        assert refresh_token
        assert await get_user_by_id(user.id) is not None

    @pytest.mark.asyncio
    async def test_usuario_existente_faz_login_sem_criar_conta_duplicada(self):
        """Erro/borda inverso: a segunda chamada pro mesmo email nunca cria
        um segundo usuário — sempre resolve pro mesmo `id`."""
        from backend.rbac.auth import list_users, provision_or_login_sso

        primeiro, _, _ = await provision_or_login_sso("dev@example.com")
        segundo, access_token2, refresh_token2 = await provision_or_login_sso(
            "dev@example.com"
        )

        assert segundo.id == primeiro.id
        assert access_token2
        assert refresh_token2
        assert len(await list_users()) == 1

    @pytest.mark.asyncio
    async def test_email_e_normalizado_como_no_signup_local(self):
        from backend.rbac.auth import provision_or_login_sso

        user, _, _ = await provision_or_login_sso("Dev@Example.COM")
        assert user.email == "dev@example.com"


# ---------------------------------------------------------------------------
# signin
# ---------------------------------------------------------------------------


class TestSignin:
    @pytest.mark.asyncio
    async def test_valid_credentials(self):
        from backend.rbac.auth import signin, signup

        await signup("user@example.com", "senhasegura1234")
        user, access_token, refresh_token = await signin(
            "user@example.com", "senhasegura1234"
        )
        assert user.email == "user@example.com"
        assert access_token
        assert refresh_token

    @pytest.mark.asyncio
    async def test_wrong_password_raises(self):
        from backend.rbac.auth import signin, signup

        await signup("user2@example.com", "senhasegura1234")
        with pytest.raises(ValueError, match="Credenciais inválidas"):
            await signin("user2@example.com", "senhaerrada1234")

    @pytest.mark.asyncio
    async def test_unknown_email_raises(self):
        from backend.rbac.auth import signin

        with pytest.raises(ValueError, match="Credenciais inválidas"):
            await signin("ghost@example.com", "senhasegura1234")

    @pytest.mark.asyncio
    async def test_case_insensitive_email(self):
        from backend.rbac.auth import signin, signup

        await signup("case@example.com", "senhasegura1234")
        user, _, _ = await signin("CASE@EXAMPLE.COM", "senhasegura1234")
        assert user.email == "case@example.com"

    @pytest.mark.asyncio
    async def test_signin_updates_last_login_at(self):
        from backend.rbac.auth import signin, signup

        user_created, _, _ = await signup("login@example.com", "senhasegura1234")
        assert user_created.last_login_at is None
        user_signed, _, _ = await signin("login@example.com", "senhasegura1234")
        assert user_signed.last_login_at is not None


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------


class TestRefreshTokens:
    @pytest.mark.asyncio
    async def test_refresh_issues_new_pair(self):
        from backend.rbac.auth import decode_access_token, refresh_tokens, signup

        _, _old_access, old_refresh = await signup(
            "refresh@example.com", "senhasegura1234"
        )
        user, new_access, new_refresh = await refresh_tokens(old_refresh)

        assert user.email == "refresh@example.com"
        # Novo refresh token sempre difere (aleatoriedade)
        assert new_refresh != old_refresh
        # Novo access token é JWT válido para o mesmo usuário
        payload = decode_access_token(new_access)
        assert payload["email"] == "refresh@example.com"

    @pytest.mark.asyncio
    async def test_old_refresh_token_revoked_after_rotation(self):
        from backend.rbac.auth import refresh_tokens, signup

        _, _, refresh = await signup("rot@example.com", "senhasegura1234")
        await refresh_tokens(refresh)

        with pytest.raises(ValueError, match="revogado"):
            await refresh_tokens(refresh)

    @pytest.mark.asyncio
    async def test_invalid_refresh_token_raises(self):
        from backend.rbac.auth import refresh_tokens

        with pytest.raises(ValueError, match="inválido"):
            await refresh_tokens("token-invalido-qualquer")

    @pytest.mark.asyncio
    async def test_expired_refresh_token_raises(self):
        import hashlib
        from datetime import UTC, datetime, timedelta

        from backend.rbac.auth import _get_db, refresh_tokens, signup

        _, _, refresh = await signup("exp@example.com", "senhasegura1234")
        token_hash = hashlib.sha256(refresh.encode()).hexdigest()

        # Força expiração do token
        db = await _get_db()
        past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        await db.execute(
            "UPDATE refresh_tokens SET expires_at = ? WHERE token_hash = ?",
            (past, token_hash),
        )
        await db.commit()

        with pytest.raises(ValueError, match="expirado"):
            await refresh_tokens(refresh)


# ---------------------------------------------------------------------------
# signout
# ---------------------------------------------------------------------------


class TestSignout:
    @pytest.mark.asyncio
    async def test_signout_revokes_refresh_token(self):
        from backend.rbac.auth import refresh_tokens, signout, signup

        _, _, refresh = await signup("out@example.com", "senhasegura1234")
        await signout(refresh)

        with pytest.raises(ValueError, match="revogado"):
            await refresh_tokens(refresh)

    @pytest.mark.asyncio
    async def test_signout_with_invalid_token_does_not_raise(self):
        from backend.rbac.auth import signout

        # Signout com token inexistente não deve levantar exceção
        await signout("token-invalido-nao-deve-explodir")


# ---------------------------------------------------------------------------
# get_user_by_id
# ---------------------------------------------------------------------------


class TestGetUserById:
    @pytest.mark.asyncio
    async def test_returns_user(self):
        from backend.rbac.auth import get_user_by_id, signup

        created, _, _ = await signup("find@example.com", "senhasegura1234")
        found = await get_user_by_id(created.id)
        assert found is not None
        assert found.email == "find@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_id(self):
        from backend.rbac.auth import get_user_by_id

        result = await get_user_by_id("id-que-nao-existe")
        assert result is None


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------


class TestChangePassword:
    @pytest.mark.asyncio
    async def test_changes_password_successfully(self):
        from backend.rbac.auth import change_password, signin, signup

        user, _, _ = await signup("chpwd@example.com", "senhaantiga1234")
        await change_password(user.id, "senhaantiga1234", "senhanova5678!")

        # Nova senha funciona
        u2, _, _ = await signin("chpwd@example.com", "senhanova5678!")
        assert u2.email == "chpwd@example.com"

    @pytest.mark.asyncio
    async def test_wrong_old_password_raises(self):
        from backend.rbac.auth import change_password, signup

        user, _, _ = await signup("chpwd2@example.com", "senhaantiga1234")
        with pytest.raises(ValueError, match="Senha atual incorreta"):
            await change_password(user.id, "senhaerrada1234", "senhanova5678!")

    @pytest.mark.asyncio
    async def test_short_new_password_raises(self):
        from backend.rbac.auth import change_password, signup

        user, _, _ = await signup("chpwd3@example.com", "senhaantiga1234")
        with pytest.raises(ValueError, match="mínimo 8"):
            await change_password(user.id, "senhaantiga1234", "curta")

    @pytest.mark.asyncio
    async def test_change_password_revokes_existing_refresh_tokens(self):
        from backend.rbac.auth import change_password, refresh_tokens, signup

        user, _, refresh = await signup("chpwd4@example.com", "senhaantiga1234")
        await change_password(user.id, "senhaantiga1234", "senhanova5678!")

        with pytest.raises(ValueError, match="revogado"):
            await refresh_tokens(refresh)


# ---------------------------------------------------------------------------
# Env overrides
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    @pytest.mark.asyncio
    async def test_set_and_get_override(self):
        from backend.rbac.auth import get_env_overrides, set_env_override, signup

        user, _, _ = await signup("env@example.com", "senhasegura1234")
        await set_env_override(user.id, "GITHUB_TOKEN", "ghp_test123")

        overrides = await get_env_overrides(user.id)
        assert overrides["GITHUB_TOKEN"] == "ghp_test123"

    @pytest.mark.asyncio
    async def test_delete_override(self):
        from backend.rbac.auth import (
            delete_env_override,
            get_env_overrides,
            set_env_override,
            signup,
        )

        user, _, _ = await signup("env2@example.com", "senhasegura1234")
        await set_env_override(user.id, "MY_KEY", "my_value")
        await delete_env_override(user.id, "MY_KEY")

        overrides = await get_env_overrides(user.id)
        assert "MY_KEY" not in overrides

    @pytest.mark.asyncio
    async def test_override_does_not_affect_other_users(self):
        from backend.rbac.auth import get_env_overrides, set_env_override, signup

        user1, _, _ = await signup("u1@example.com", "senhasegura1234")
        user2, _, _ = await signup("u2@example.com", "senhasegura5678")
        await set_env_override(user1.id, "PRIVATE_KEY", "value1")

        overrides2 = await get_env_overrides(user2.id)
        assert "PRIVATE_KEY" not in overrides2

    @pytest.mark.asyncio
    async def test_empty_overrides_for_new_user(self):
        from backend.rbac.auth import get_env_overrides, signup

        user, _, _ = await signup("empty@example.com", "senhasegura1234")
        overrides = await get_env_overrides(user.id)
        assert overrides == {}

    @pytest.mark.asyncio
    async def test_source_oauth_marca_is_oauth_sourced(self) -> None:
        from backend.rbac.auth import (
            get_env_overrides,
            is_oauth_sourced,
            set_env_override,
            signup,
        )

        user, _, _ = await signup("oauth-src@example.com", "senhasegura1234")
        await set_env_override(user.id, "GITHUB_TOKEN", "ghp_via_oauth", source="oauth")

        overrides = await get_env_overrides(user.id)
        assert is_oauth_sourced(overrides, "GITHUB_TOKEN") is True

    @pytest.mark.asyncio
    async def test_source_manual_default_nao_marca_is_oauth_sourced(self) -> None:
        from backend.rbac.auth import (
            get_env_overrides,
            is_oauth_sourced,
            set_env_override,
            signup,
        )

        user, _, _ = await signup("manual-src@example.com", "senhasegura1234")
        await set_env_override(user.id, "GITHUB_TOKEN", "ghp_colado_a_mao")

        overrides = await get_env_overrides(user.id)
        assert is_oauth_sourced(overrides, "GITHUB_TOKEN") is False

    @pytest.mark.asyncio
    async def test_resetar_override_via_source_manual_apaga_marca_oauth_previa(
        self,
    ) -> None:
        """Erro/borda: se um usuário conectou via OAuth e depois cola um
        token manualmente por cima (mesma env var), a marca de proveniência
        OAuth não pode sobreviver — senão a UI continuaria mostrando
        "Conexão ativa (OAuth)" pra uma credencial que não é mais OAuth."""
        from backend.rbac.auth import (
            get_env_overrides,
            is_oauth_sourced,
            set_env_override,
            signup,
        )

        user, _, _ = await signup("oauth-then-manual@example.com", "senhasegura1234")
        await set_env_override(user.id, "GITHUB_TOKEN", "ghp_via_oauth", source="oauth")
        await set_env_override(user.id, "GITHUB_TOKEN", "ghp_colado_por_cima")

        overrides = await get_env_overrides(user.id)
        assert overrides["GITHUB_TOKEN"] == "ghp_colado_por_cima"
        assert is_oauth_sourced(overrides, "GITHUB_TOKEN") is False

    @pytest.mark.asyncio
    async def test_erro_borda_chave_reservada_de_proveniencia_oauth_e_rejeitada(
        self,
    ) -> None:
        """Uma escrita manual não pode forjar a chave-sombra de proveniência
        OAuth — senão, com um GITHUB_TOKEN qualquer já setado, is_oauth_sourced
        mentiria que veio de OAuth."""
        from backend.rbac.auth import set_env_override, signup

        user, _, _ = await signup("forja-oauth@example.com", "senhasegura1234")
        with pytest.raises(ValueError, match="chave reservada"):
            await set_env_override(user.id, "__oauth_source__:GITHUB_TOKEN", "1")

    @pytest.mark.asyncio
    async def test_delete_override_remove_tambem_a_marca_oauth(self) -> None:
        from backend.rbac.auth import (
            delete_env_override,
            get_env_overrides,
            is_oauth_sourced,
            set_env_override,
            signup,
        )

        user, _, _ = await signup("oauth-delete@example.com", "senhasegura1234")
        await set_env_override(user.id, "GITHUB_TOKEN", "ghp_via_oauth", source="oauth")
        await delete_env_override(user.id, "GITHUB_TOKEN")

        overrides = await get_env_overrides(user.id)
        assert "GITHUB_TOKEN" not in overrides
        assert is_oauth_sourced(overrides, "GITHUB_TOKEN") is False


class TestEnvOverridesLocalUser:
    """O usuário virtual "local" (modo sem conta) nunca tem linha em
    `users` — `get/set/delete_env_override` desviam pra `runtime_settings`
    em vez de fazer UPDATE/SELECT sem efeito."""

    @pytest.fixture(autouse=True)
    def _isolated_runtime_settings(self, tmp_path, monkeypatch):
        from backend.workspace import runtime_settings as rs_mod

        fresh = rs_mod.RuntimeSettings(path=tmp_path / "runtime.db")
        monkeypatch.setattr(rs_mod, "runtime_settings", fresh)
        return fresh

    @pytest.mark.asyncio
    async def test_set_and_get_override_local_user(self):
        from backend.rbac.auth import get_env_overrides, set_env_override

        await set_env_override("local", "GOOGLE_API_KEY", "AIza-local")

        overrides = await get_env_overrides("local")
        assert overrides["GOOGLE_API_KEY"] == "AIza-local"

    @pytest.mark.asyncio
    async def test_delete_override_local_user(self):
        from backend.rbac.auth import (
            delete_env_override,
            get_env_overrides,
            set_env_override,
        )

        await set_env_override("local", "MY_KEY", "value")
        await delete_env_override("local", "MY_KEY")

        overrides = await get_env_overrides("local")
        assert "MY_KEY" not in overrides

    @pytest.mark.asyncio
    async def test_empty_overrides_for_local_user_by_default(self):
        from backend.rbac.auth import get_env_overrides

        overrides = await get_env_overrides("local")
        assert overrides == {}

    @pytest.mark.asyncio
    async def test_real_account_user_still_uses_users_table_regression(self):
        """Regressão: um usuário real (id de conta company) continua
        usando a tabela `users`, não `runtime_settings`."""
        from backend.rbac.auth import get_env_overrides, set_env_override, signup

        user, _, _ = await signup("real-account@example.com", "senhasegura1234")
        await set_env_override(user.id, "REAL_KEY", "real-value")

        local_overrides = await get_env_overrides("local")
        assert "REAL_KEY" not in local_overrides

        real_overrides = await get_env_overrides(user.id)
        assert real_overrides["REAL_KEY"] == "real-value"


# ---------------------------------------------------------------------------
# Convites de signup
# ---------------------------------------------------------------------------


class TestInvites:
    @pytest.mark.asyncio
    async def test_create_and_validate_invite(self):
        from backend.rbac.auth import create_invite, signup, validate_invite

        root, _, _ = await signup("root@example.com", "senharootok1234")
        token, expires_at = await create_invite(root.id, role="member")
        assert token
        assert expires_at

        info = await validate_invite(token)
        assert info is not None
        assert info["role"] == "member"

    @pytest.mark.asyncio
    async def test_signup_with_invite_uses_invite_role(self):
        from backend.rbac.auth import create_invite, signup, validate_invite

        root, _, _ = await signup("root@example.com", "senharootok1234")
        token, _ = await create_invite(root.id, role="admin")
        info = await validate_invite(token)
        assert info is not None

        user, _, _ = await signup(
            "invited@example.com", "senhasegura1234", role=info["role"]
        )
        assert user.role == "admin"

    @pytest.mark.asyncio
    async def test_consume_invalidates_invite(self):
        from backend.rbac.auth import (
            consume_invite,
            create_invite,
            signup,
            validate_invite,
        )

        root, _, _ = await signup("root@example.com", "senharootok1234")
        token, _ = await create_invite(root.id, role="member")
        user, _, _ = await signup("m@example.com", "senhasegura1234", role="member")

        await consume_invite(token, user.id)
        assert await validate_invite(token) is None

    @pytest.mark.asyncio
    async def test_unknown_token_is_invalid(self):
        from backend.rbac.auth import validate_invite

        assert await validate_invite("token-inexistente") is None

    @pytest.mark.asyncio
    async def test_expired_invite_is_invalid(self):
        from datetime import UTC, datetime, timedelta

        from backend.rbac.auth import (
            _get_db,
            _hash_token,
            create_invite,
            signup,
            validate_invite,
        )

        root, _, _ = await signup("root@example.com", "senharootok1234")
        token, _ = await create_invite(root.id, role="member")

        db = await _get_db()
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        await db.execute(
            "UPDATE invites SET expires_at = ? WHERE token_hash = ?",
            (past, _hash_token(token)),
        )
        await db.commit()

        assert await validate_invite(token) is None

    @pytest.mark.asyncio
    async def test_list_and_revoke_invite(self):
        from backend.rbac.auth import (
            create_invite,
            list_invites,
            revoke_invite,
            signup,
        )

        root, _, _ = await signup("root@example.com", "senharootok1234")
        await create_invite(root.id, role="viewer")

        invites = await list_invites()
        assert len(invites) == 1
        token_hash = invites[0]["token_hash"]

        assert await revoke_invite(token_hash) is True
        assert await list_invites() == []

    @pytest.mark.asyncio
    async def test_first_user_ignores_invite_role(self):
        from backend.rbac.auth import signup

        # Sem usuários ainda: a role do convite é ignorada, o 1º vira root
        user, _, _ = await signup("first@example.com", "senhasegura1234", role="viewer")
        assert user.role == "root"


# ---------------------------------------------------------------------------
# _write_audit — redação de campos sensíveis
# ---------------------------------------------------------------------------


class TestAuditRedaction:
    @pytest.mark.asyncio
    async def test_campo_comum_passa_intacto(self):
        import json

        from backend.rbac.auth import _get_db, _write_audit

        db = await _get_db()
        await _write_audit(db, "u1", "test_action", metadata={"ip": "1.2.3.4"})

        async with db.execute(
            "SELECT metadata_json FROM audit WHERE action = ?", ("test_action",)
        ) as cur:
            row = await cur.fetchone()
        assert json.loads(row["metadata_json"]) == {"ip": "1.2.3.4"}

    @pytest.mark.asyncio
    async def test_campo_sensivel_sempre_redigido_mesmo_tentando_gravar(self):
        """Erro/borda: qualquer chave da denylist (case-insensitive) nunca
        chega ao banco com o valor real, mesmo que um call-site futuro
        passe por engano."""
        import json

        from backend.rbac.auth import _get_db, _write_audit

        db = await _get_db()
        await _write_audit(
            db,
            "u1",
            "test_action_sensivel",
            metadata={
                "password": "senha-real-123",
                "Token": "segredo-abc",
                "ip": "1.2.3.4",
            },
        )

        async with db.execute(
            "SELECT metadata_json FROM audit WHERE action = ?",
            ("test_action_sensivel",),
        ) as cur:
            row = await cur.fetchone()
        salvo = json.loads(row["metadata_json"])
        assert salvo["password"] == "[REDACTED]"
        assert salvo["Token"] == "[REDACTED]"
        assert salvo["ip"] == "1.2.3.4"


# ---------------------------------------------------------------------------
# request_password_reset / confirm_password_reset
# ---------------------------------------------------------------------------


class TestPasswordReset:
    @pytest.mark.asyncio
    async def test_fluxo_completo_feliz(self):
        from backend.rbac.auth import (
            confirm_password_reset,
            request_password_reset,
            signin,
            signup,
        )

        await signup("reset@example.com", "senhaoriginal123")

        token = await request_password_reset("reset@example.com")
        assert token is not None

        await confirm_password_reset(token, "senhanovasegura456")

        # Senha antiga não funciona mais; a nova sim.
        with pytest.raises(ValueError, match="Credenciais inválidas"):
            await signin("reset@example.com", "senhaoriginal123")
        user, _, _ = await signin("reset@example.com", "senhanovasegura456")
        assert user.email == "reset@example.com"

    @pytest.mark.asyncio
    async def test_email_inexistente_nao_lanca_e_devolve_none(self):
        """Erro/borda: email que não existe nunca revela isso via exceção
        — devolve None silenciosamente (evita enumeração de conta)."""
        from backend.rbac.auth import request_password_reset

        assert await request_password_reset("nao-existe@example.com") is None

    @pytest.mark.asyncio
    async def test_token_invalido_expirado_ou_reusado_e_rejeitado(self):
        """Erro/borda: token desconhecido, e um token real usado duas
        vezes — os dois levantam ValueError, nunca aplicam a senha."""
        from backend.rbac.auth import (
            confirm_password_reset,
            request_password_reset,
            signup,
        )

        with pytest.raises(ValueError, match="inválido"):
            await confirm_password_reset("token-que-nao-existe", "senhanova12345")

        await signup("reuso@example.com", "senhaoriginal123")
        token = await request_password_reset("reuso@example.com")
        assert token is not None

        await confirm_password_reset(token, "primeiratrocasegura1")
        with pytest.raises(ValueError, match="inválido"):
            await confirm_password_reset(token, "segundatrocasegura2")

    @pytest.mark.asyncio
    async def test_senha_curta_e_rejeitada(self):
        from backend.rbac.auth import (
            confirm_password_reset,
            request_password_reset,
            signup,
        )

        await signup("curta@example.com", "senhaoriginal123")
        token = await request_password_reset("curta@example.com")
        assert token is not None

        with pytest.raises(ValueError, match="mínimo 8"):
            await confirm_password_reset(token, "curta")

    @pytest.mark.asyncio
    async def test_reset_revoga_refresh_tokens_existentes(self):
        from backend.rbac.auth import (
            confirm_password_reset,
            refresh_tokens,
            request_password_reset,
            signup,
        )

        _, _, refresh = await signup("revoga@example.com", "senhaoriginal123")

        token = await request_password_reset("revoga@example.com")
        assert token is not None
        await confirm_password_reset(token, "senhanovasegura456")

        with pytest.raises(ValueError):
            await refresh_tokens(refresh)
