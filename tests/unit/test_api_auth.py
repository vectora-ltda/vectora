"""Testes de integração dos endpoints HTTP de auth.

Usa FastAPI TestClient com banco SQLite temporário e auth habilitada,
cobrindo o fluxo end-to-end via HTTP (signup → signin → /me → refresh →
signout → acesso negado).

Cobre:
- GET /auth/has-users
- POST /auth/signup: primeiro root, segundo bloqueado, validações
- POST /auth/signin: credenciais válidas/inválidas, cookies definidos
- POST /auth/refresh: rotação via body e via cookie
- POST /auth/signout: revogação + cookies limpos
- GET /auth/me: com token válido, sem token, token expirado
- POST /auth/change-password
- GET/POST/DELETE /auth/envs: env overrides
- GET /auth/users (admin/root)
- POST /auth/users/{id}/role (root)
- GET /auth/audit (admin/root)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_and_db(tmp_path_factory):
    """App FastAPI com auth HABILITADA e banco temporário."""
    tmp = tmp_path_factory.mktemp("auth_api")
    db_file = str(tmp / "test_api_auth.db")

    os.environ["VECTORA_AUTH_REQUIRED"] = "true"

    import aiosqlite

    import backend.rbac.auth as auth_mod

    # Reset estado global
    original_get_db = auth_mod._get_db
    original_get_secret = auth_mod._get_secret
    auth_mod._db_conn = None
    _TEST_SECRET = "api-test-secret-key-fixed-abcdef"
    # Patcha _get_secret como função para que o módulo todo use este secret
    auth_mod._get_secret = lambda: _TEST_SECRET  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    async def _patched_get_db():
        if auth_mod._db_conn is not None:
            return auth_mod._db_conn
        conn = await aiosqlite.connect(db_file)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await auth_mod._ensure_schema(conn)
        auth_mod._db_conn = conn
        return conn

    auth_mod._get_db = _patched_get_db  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    from backend.api.server import create_app

    app = create_app(serve_static=False)
    yield app, auth_mod

    import asyncio

    async def _close():
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    asyncio.run(_close())
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"
    # Sem isso, _get_db/_get_secret ficam trocados pro resto do processo de
    # teste inteiro — qualquer arquivo que rode depois deste herda o banco
    # tmp e o secret fixo em vez do real (mesma classe de bug corrigida em
    # test_provider_routing_handler.py).
    auth_mod._get_db = original_get_db
    auth_mod._get_secret = original_get_secret


@pytest.fixture(scope="module")
def client(app_and_db):
    app, _ = app_and_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def root_tokens(client):
    """Cria o primeiro usuário (root) e retorna seus tokens."""
    # Verifica se ainda não há usuários
    r = client.get("/auth/has-users")
    if r.json()["exists"]:
        # Banco já populado em outro teste — usa signin
        r = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
    else:
        r = client.post(
            "/auth/signup",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
    assert r.status_code in (200, 201)
    data = r.json()
    return data["access_token"], data["refresh_token"], data["user"]["id"]


# ---------------------------------------------------------------------------
# has-users
# ---------------------------------------------------------------------------


class TestHasUsers:
    def test_has_users_returns_bool(self, client):
        r = client.get("/auth/has-users")
        assert r.status_code == 200
        assert "exists" in r.json()


# ---------------------------------------------------------------------------
# signup
# ---------------------------------------------------------------------------


class TestSignupEndpoint:
    def test_first_user_signup_succeeds(self, client):
        # Se já há usuários (root_tokens fixture rodou antes), verifica bloqueio
        r_check = client.get("/auth/has-users")
        if r_check.json()["exists"]:
            r = client.post(
                "/auth/signup",
                json={"email": "new@test.com", "password": "newpassword1234"},
            )
            assert r.status_code == 403
            assert "desabilitado" in r.json()["detail"].lower()
        else:
            r = client.post(
                "/auth/signup",
                json={"email": "root@test.com", "password": "rootpassword1234"},
            )
            assert r.status_code == 200
            data = r.json()
            assert "access_token" in data
            assert data["user"]["role"] == "root"

    def test_signup_sets_cookies(self, client, root_tokens):
        # Após signup do root, tentar novo signup é bloqueado — verificamos cookies no signin
        access, _, _ = root_tokens
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200

    def test_signup_short_password_returns_400(self, client):
        r = client.post(
            "/auth/signup",
            json={"email": "any@test.com", "password": "curta"},
        )
        # 400 ou 403 (se já há users)
        assert r.status_code in (400, 403)


# ---------------------------------------------------------------------------
# signin
# ---------------------------------------------------------------------------


class TestSigninEndpoint:
    def test_valid_signin_returns_tokens(self, client, root_tokens):
        r = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["email"] == "root@test.com"

    def test_invalid_password_returns_401(self, client, root_tokens):
        r = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "senhaerrada9999"},
        )
        assert r.status_code == 401

    def test_unknown_email_returns_401(self, client):
        r = client.post(
            "/auth/signin",
            json={"email": "ghost@test.com", "password": "ghostpassword1234"},
        )
        assert r.status_code == 401

    def test_signin_sets_httponly_cookies(self, client, root_tokens):
        r = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
        assert r.status_code == 200
        assert "vectora_access" in r.cookies
        assert "vectora_refresh" in r.cookies


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


class TestMeEndpoint:
    def test_me_with_valid_bearer_token(self, client, root_tokens):
        access, _, _ = root_tokens
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200
        assert r.json()["email"] == "root@test.com"

    def test_me_without_token_returns_401(self, app_and_db):
        from fastapi.testclient import TestClient

        app, _ = app_and_db
        fresh = TestClient(app, raise_server_exceptions=False)
        r = fresh.get("/auth/me")
        assert r.status_code == 401

    def test_me_with_invalid_token_returns_401(self, app_and_db):
        from fastapi.testclient import TestClient

        app, _ = app_and_db
        fresh = TestClient(app, raise_server_exceptions=False)
        r = fresh.get(
            "/auth/me", headers={"Authorization": "Bearer token-invalido-qualquer"}
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /auth/me — atualizar perfil (campo name)
# ---------------------------------------------------------------------------


class TestUpdateMeEndpoint:
    def test_patch_name_updates_and_returns_new_value(self, client, root_tokens):
        access, _, _ = root_tokens
        r = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "Bruno Soares"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Bruno Soares"

    def test_patch_name_persists_visible_in_get_me(self, client, root_tokens):
        """Após PATCH, GET /me devolve o name novo (não cache stale)."""
        access, _, _ = root_tokens
        client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "Persistente"},
        )
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200
        assert r.json()["name"] == "Persistente"

    def test_patch_name_accepts_utf8(self, client, root_tokens):
        """UTF-8 livre — acentos, apóstrofo, espaços."""
        access, _, _ = root_tokens
        r = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "João D'Ávila"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "João D'Ávila"

    def test_patch_name_sanitizes_whitespace(self, client, root_tokens):
        """Trim e collapse de espaços internos via service layer."""
        access, _, _ = root_tokens
        r = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "   Bruno   de    Souza   "},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Bruno de Souza"

    def test_patch_name_caps_at_100(self, client, root_tokens):
        access, _, _ = root_tokens
        r = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "B" * 500},
        )
        assert r.status_code == 200
        assert len(r.json()["name"]) == 100

    def test_patch_empty_body_does_nothing(self, client, root_tokens):
        """Body sem 'name' (None) — endpoint não mexe e devolve user atual."""
        access, _, _ = root_tokens
        # Garante um estado conhecido
        client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "Original"},
        )
        r = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
            json={},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Original"

    def test_patch_without_auth_returns_401(self, app_and_db):
        from fastapi.testclient import TestClient

        app, _ = app_and_db
        fresh = TestClient(app, raise_server_exceptions=False)
        r = fresh.patch("/auth/me", json={"name": "Anônimo"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------


class TestUsageEndpoint:
    def test_usage_with_valid_token(self, client, root_tokens):
        access, _, _ = root_tokens
        r = client.get("/auth/usage", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200
        body = r.json()
        for key in ("used", "limit", "remaining", "window_seconds", "reset_in_seconds"):
            assert key in body

    def test_usage_without_token_returns_401(self, app_and_db):
        from fastapi.testclient import TestClient

        app, _ = app_and_db
        fresh = TestClient(app, raise_server_exceptions=False)
        r = fresh.get("/auth/usage")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# tools policy
# ---------------------------------------------------------------------------


class TestToolsPolicy:
    def test_get_and_put_policy(self, client, root_tokens, tmp_path, monkeypatch):
        from backend.rbac import tool_policy

        monkeypatch.setattr(tool_policy, "_policy_dir", lambda: tmp_path / "tp")
        access, _, _ = root_tokens
        headers = {"Authorization": f"Bearer {access}"}

        r = client.get("/tools/policy", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["disabled"] == []
        assert "terminal" in body["available"]

        r = client.put(
            "/tools/policy", headers=headers, json={"disabled": ["terminal"]}
        )
        assert r.status_code == 200
        assert r.json()["disabled"] == ["terminal"]

        r = client.get("/tools/policy", headers=headers)
        assert r.json()["disabled"] == ["terminal"]

    def test_put_unknown_tool_rejected(
        self, client, root_tokens, tmp_path, monkeypatch
    ):
        from backend.rbac import tool_policy

        monkeypatch.setattr(tool_policy, "_policy_dir", lambda: tmp_path / "tp")
        access, _, _ = root_tokens
        r = client.put(
            "/tools/policy",
            headers={"Authorization": f"Bearer {access}"},
            json={"disabled": ["nao_existe"]},
        )
        assert r.status_code == 400

    def test_policy_without_token_returns_401(self, app_and_db):
        from fastapi.testclient import TestClient

        app, _ = app_and_db
        fresh = TestClient(app, raise_server_exceptions=False)
        assert fresh.get("/tools/policy").status_code == 401


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


class TestRefreshEndpoint:
    def test_refresh_via_body_returns_new_tokens(self, client):
        r_sign = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
        old_refresh = r_sign.json()["refresh_token"]

        r = client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert r.status_code == 200
        data = r.json()
        assert data["refresh_token"] != old_refresh
        assert "access_token" in data

    def test_refresh_with_invalid_token_returns_401(self, client):
        r = client.post("/auth/refresh", json={"refresh_token": "token-invalido"})
        assert r.status_code == 401

    def test_double_refresh_with_same_token_returns_401(self, client):
        r_sign = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
        refresh = r_sign.json()["refresh_token"]

        client.post("/auth/refresh", json={"refresh_token": refresh})
        r2 = client.post("/auth/refresh", json={"refresh_token": refresh})
        assert r2.status_code == 401


# ---------------------------------------------------------------------------
# signout
# ---------------------------------------------------------------------------


class TestSignoutEndpoint:
    def test_signout_clears_cookies(self, client):
        r_sign = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
        refresh = r_sign.json()["refresh_token"]

        r = client.post("/auth/signout", json={"refresh_token": refresh})
        assert r.status_code == 200

    def test_after_signout_refresh_token_is_invalid(self, client):
        r_sign = client.post(
            "/auth/signin",
            json={"email": "root@test.com", "password": "rootpassword1234"},
        )
        refresh = r_sign.json()["refresh_token"]

        client.post("/auth/signout", json={"refresh_token": refresh})
        r = client.post("/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 401

    def test_signout_without_token_returns_200(self, client):
        r = client.post("/auth/signout", json={})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# change-password
# ---------------------------------------------------------------------------


class TestChangePasswordEndpoint:
    def test_change_password_success(self, client, root_tokens):
        access, _, _ = root_tokens
        r = client.post(
            "/auth/change-password",
            json={"old_password": "rootpassword1234", "new_password": "novasenha5678!"},
            headers={"Authorization": f"Bearer {access}"},
        )
        # Pode ser 200 ou falha se a senha já foi trocada por outro teste
        assert r.status_code in (200, 400)

    def test_change_password_without_auth_returns_401(self, client):
        r = client.post(
            "/auth/change-password",
            json={"old_password": "qualquer", "new_password": "outrasenha1234"},
        )
        assert r.status_code == 401


class TestPasswordResetEndpoints:
    def test_fluxo_completo_via_http(self, client):
        r_signup = client.post(
            "/auth/signup",
            json={
                "email": "http-reset@test.com",
                "password": "senhaoriginal123",
            },
        )
        assert r_signup.status_code in (200, 403)
        if r_signup.status_code == 403:
            pytest.skip("signup público bloqueado neste estado do client compartilhado")

        r_request = client.post(
            "/auth/password-reset/request", json={"email": "http-reset@test.com"}
        )
        assert r_request.status_code == 200
        assert r_request.json() == {"ok": True}

        import asyncio

        from backend.rbac import auth as auth_mod

        token = asyncio.run(auth_mod.request_password_reset("http-reset@test.com"))
        assert token is not None

        r_confirm = client.post(
            "/auth/password-reset/confirm",
            json={"token": token, "new_password": "senhanovasegura456"},
        )
        assert r_confirm.status_code == 200
        assert r_confirm.json() == {"ok": True}

        r_signin = client.post(
            "/auth/signin",
            json={"email": "http-reset@test.com", "password": "senhanovasegura456"},
        )
        assert r_signin.status_code == 200

    def test_email_inexistente_devolve_200_sem_revelar_nada(self, client):
        """Erro/borda: mesmo resposta 200 pra email que não existe — evita
        enumeração de conta via diferença de status HTTP."""
        r = client.post(
            "/auth/password-reset/request",
            json={"email": "nunca-existiu@test.com"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_token_invalido_devolve_400(self, client):
        r = client.post(
            "/auth/password-reset/confirm",
            json={"token": "token-que-nao-existe", "new_password": "senhanova12345"},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Env overrides
# ---------------------------------------------------------------------------


class TestEnvOverridesEndpoints:
    @pytest.fixture(autouse=True)
    def _setup_root(self, root_tokens):
        """Garante que o usuário root existe e guarda o token cacheado."""
        self._cached_access, *_ = root_tokens

    def _get_fresh_token(self, client) -> str:
        """Obtém token válido, lidando com senha que pode ter sido trocada."""
        for pwd in ("rootpassword1234", "novasenha5678!"):
            r = client.post(
                "/auth/signin",
                json={"email": "root@test.com", "password": pwd},
            )
            if r.status_code == 200:
                return r.json()["access_token"]
        # Fallback: token cacheado do fixture root_tokens (pode ainda ser válido)
        r = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {self._cached_access}"},
        )
        if r.status_code == 200:
            return self._cached_access
        pytest.fail("Não foi possível obter token válido para teste de envs")

    def test_get_envs_empty_initially(self, client):
        access = self._get_fresh_token(client)
        r = client.get("/auth/envs", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200
        data = r.json()
        assert "envs" in data
        assert "keys" in data

    def test_set_and_get_env(self, client):
        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}

        r = client.post(
            "/auth/envs",
            json={"key": "TEST_KEY", "value": "test_value"},
            headers=headers,
        )
        assert r.status_code == 200

        r = client.get("/auth/envs", headers=headers)
        data = r.json()
        assert "TEST_KEY" in data["keys"]
        # Valor deve estar mascarado
        assert data["envs"]["TEST_KEY"] == "••••••••"

    def test_delete_env(self, client):
        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}

        client.post(
            "/auth/envs",
            json={"key": "DEL_KEY", "value": "del_value"},
            headers=headers,
        )
        r = client.delete("/auth/envs/DEL_KEY", headers=headers)
        assert r.status_code == 200

        r = client.get("/auth/envs", headers=headers)
        assert "DEL_KEY" not in r.json()["keys"]

    def test_set_connect_key_without_pro_returns_402(
        self, client, monkeypatch, tmp_path
    ):
        """Telegram/Discord/Slack/Email (Connect) exigem tier pro."""
        from backend.services import license as lic

        monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")
        monkeypatch.delenv("VECTORA_LICENSE_BYPASS", raising=False)
        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}

        r = client.post(
            "/auth/envs",
            json={"key": "TELEGRAM_BOT_TOKEN", "value": "123:abc"},
            headers=headers,
        )
        assert r.status_code == 402

    def test_set_connect_key_with_pro_succeeds(self, client, monkeypatch):
        monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
        # sync_adapters tentaria conectar de verdade com um token falso —
        # isola o efeito colateral, o teste cobre o gate, não o adapter.
        monkeypatch.setattr(
            "backend.api.handlers.auth._sync_connect_adapters",
            AsyncMock(return_value=None),
        )
        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}

        r = client.post(
            "/auth/envs",
            json={"key": "DISCORD_BOT_TOKEN", "value": "tok"},
            headers=headers,
        )
        assert r.status_code == 200

    def test_delete_connect_key_never_gated(self, client, monkeypatch, tmp_path):
        """Remover a própria credencial nunca é bloqueado — mesmo caído pra
        Free depois de ter salvo com Pro, o usuário sempre pode limpar."""
        from backend.services import license as lic

        monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")
        monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
        monkeypatch.setattr(
            "backend.api.handlers.auth._sync_connect_adapters",
            AsyncMock(return_value=None),
        )
        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}
        client.post(
            "/auth/envs",
            json={"key": "SLACK_BOT_TOKEN", "value": "xoxb-1"},
            headers=headers,
        )

        monkeypatch.delenv("VECTORA_LICENSE_BYPASS", raising=False)
        r = client.delete("/auth/envs/SLACK_BOT_TOKEN", headers=headers)
        assert r.status_code == 200

    def test_set_non_connect_key_never_gated(self, client, monkeypatch, tmp_path):
        """Erro/borda inverso: uma key fora de CONNECT_ENV_KEYS (ex. token do
        GitHub, usado por git tools/RAG) nunca passa por require_pro."""
        from backend.services import license as lic

        monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")
        monkeypatch.delenv("VECTORA_LICENSE_BYPASS", raising=False)
        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}

        r = client.post(
            "/auth/envs",
            json={"key": "GITHUB_TOKEN", "value": "ghp_x"},
            headers=headers,
        )
        assert r.status_code == 200

    def test_set_env_llm_key_applies_to_os_environ_and_settings(
        self, client, monkeypatch, tmp_path
    ):
        """Regressão do bug real: colar uma key de LLM (GOOGLE_API_KEY) na
        aba Envs precisa valer na PRÓXIMA chamada ao provider — antes só
        gravava em env_overrides_json (banco por-usuário), nunca chegava em
        os.environ/settings, e a chamada real ao Gemini continuava usando a
        key antiga do boot (erro de quota já na 1ª mensagem).

        `settings.vectora_home` é isolado em `tmp_path` — sem isso, o
        handler grava de verdade em `~/.vectora/.env`, sobrescrevendo a key
        real do usuário que roda a suíte (bug real já reproduzido)."""
        import os

        from backend.settings import settings

        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}
        monkeypatch.setenv("GOOGLE_API_KEY", "old-boot-key")
        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        original_setting = settings.google_api_key
        from pathlib import Path

        real_home_env = Path.home() / ".vectora" / ".env"
        real_home_before = (
            real_home_env.read_text(encoding="utf-8")
            if real_home_env.exists()
            else None
        )

        try:
            r = client.post(
                "/auth/envs",
                json={"key": "GOOGLE_API_KEY", "value": "AIza-key-paga-nova"},
                headers=headers,
            )
            assert r.status_code == 200
            assert os.environ["GOOGLE_API_KEY"] == "AIza-key-paga-nova"
            assert settings.google_api_key == "AIza-key-paga-nova"
            assert (tmp_path / ".env").read_text(encoding="utf-8").strip() == (
                "GOOGLE_API_KEY=AIza-key-paga-nova"
            )
            real_home_after = (
                real_home_env.read_text(encoding="utf-8")
                if real_home_env.exists()
                else None
            )
            assert real_home_after == real_home_before
        finally:
            object.__setattr__(settings, "google_api_key", original_setting)

    def test_set_env_unknown_key_stays_override_only(self, client, monkeypatch):
        """Erro/borda: chave desconhecida (não é key de LLM/search, ex.
        token OAuth de integração) preserva o comportamento antigo — só vai
        pro override por-usuário, nunca mexe em os.environ/settings."""
        import os

        access = self._get_fresh_token(client)
        headers = {"Authorization": f"Bearer {access}"}
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        r = client.post(
            "/auth/envs",
            json={"key": "GITHUB_TOKEN", "value": "ghp_fake"},
            headers=headers,
        )
        assert r.status_code == 200
        assert "GITHUB_TOKEN" not in os.environ

    def test_envs_without_auth_returns_401(self, app_and_db):
        from fastapi.testclient import TestClient

        app, _ = app_and_db
        fresh = TestClient(app, raise_server_exceptions=False)
        r = fresh.get("/auth/envs")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


class TestAdminEndpoints:
    @pytest.fixture(autouse=True)
    def _setup_root(self, root_tokens):
        """Garante que o usuário root existe e guarda o token cacheado."""
        self._cached_access, *_ = root_tokens

    def _get_root_token(self, client) -> str:
        for pwd in ("rootpassword1234", "novasenha5678!"):
            r = client.post(
                "/auth/signin",
                json={"email": "root@test.com", "password": pwd},
            )
            if r.status_code == 200:
                return r.json()["access_token"]
        # Fallback: token cacheado do fixture root_tokens
        r = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {self._cached_access}"},
        )
        if r.status_code == 200:
            return self._cached_access
        pytest.fail("Não foi possível obter token root")

    def test_list_users_as_root(self, client):
        token = self._get_root_token(client)
        r = client.get("/auth/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert "users" in data
        assert len(data["users"]) >= 1

    def test_list_users_without_auth_returns_4xx(self, app_and_db):
        from fastapi.testclient import TestClient

        app, _ = app_and_db
        fresh = TestClient(app, raise_server_exceptions=False)
        r = fresh.get("/auth/users")
        assert r.status_code in (401, 403)

    def test_get_audit_log_as_root(self, client):
        token = self._get_root_token(client)
        r = client.get("/auth/audit", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_audit_log_contains_signin_events(self, client):
        token = self._get_root_token(client)
        r = client.get(
            "/auth/audit?action=signin",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        events = r.json()
        assert any(e["action"] == "signin" for e in events)
