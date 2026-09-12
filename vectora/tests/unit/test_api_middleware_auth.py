"""Testes unitários para src/api/middleware/auth.py.

Cobre:
- _is_public_route: rotas públicas corretas e rotas privadas
- _auth_enabled: lê variável de ambiente em tempo de request
- AuthMiddleware: passa em rotas públicas, bloqueia privadas sem token,
  injeta request.state.user em rotas protegidas com token válido
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# _is_public_route
# ---------------------------------------------------------------------------


class TestIsPublicRoute:
    def test_auth_prefix_is_public(self):
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/auth/signin") is True
        assert _is_public_route("/auth/signup") is True
        assert _is_public_route("/auth/has-users") is True
        assert _is_public_route("/auth/refresh") is True

    def test_health_is_public(self):
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/health") is True

    def test_docs_is_public(self):
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/docs") is True
        assert _is_public_route("/openapi.json") is True

    def test_static_files_are_public(self):
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/static/app.js") is True
        assert _is_public_route("/favicon.ico") is True
        assert _is_public_route("/assets/logo.png") is True

    def test_chat_endpoints_are_private(self):
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/vectora.chat.v1.ChatService/StreamChat") is False
        assert _is_public_route("/vectora.chat.v1.ThreadService/ListThreads") is False

    def test_metrics_is_private(self):
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/metrics") is False

    def test_update_changelog_is_private(self):
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/api/updates/changelog") is False

    def test_sessions_background_tasks_are_private(self):
        """`/sessions` está em `_API_PREFIXES`, então rotas de tarefas em
        segundo plano (`/sessions/{thread_id}/background/*`, ver
        backend/api/handlers/background.py) exigem token em modo servidor —
        não caem no fallback "não é rota de API → SPA → pública"."""
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/sessions/thread-1/background/tasks") is False
        assert _is_public_route("/sessions/thread-1/background/tasks/task-1") is False
        assert _is_public_route("/sessions/thread-1/background/runs") is False

    def test_root_path_is_public_frontend(self):
        # Paths fora dos prefixos de API são proxy para o Next.js e
        # portanto públicos do ponto de vista do middleware Python —
        # o frontend cuida da sua própria autenticação via cookie.
        from backend.api.middleware.auth import _is_public_route

        assert _is_public_route("/") is True


# ---------------------------------------------------------------------------
# _auth_enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_runtime_settings(tmp_path, monkeypatch):
    """Isola _auth_enabled()/runtime_settings de cada teste (nunca toca o real)."""
    from backend.workspace import runtime_settings as rs_module
    from backend.workspace.runtime_settings import RuntimeSettings

    fresh = RuntimeSettings(path=tmp_path / "checkpoints.db")
    monkeypatch.setattr(rs_module, "runtime_settings", fresh)
    return fresh


class TestAuthEnabled:
    def test_true_by_default(self, monkeypatch, _isolated_runtime_settings):
        from backend.api.middleware import auth as m

        monkeypatch.delenv("VECTORA_AUTH_REQUIRED", raising=False)
        assert m._auth_enabled() is True

    def test_false_when_set_false(self, monkeypatch):
        from backend.api.middleware import auth as m

        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
        assert m._auth_enabled() is False

    def test_false_when_set_0(self, monkeypatch):
        from backend.api.middleware import auth as m

        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "0")
        assert m._auth_enabled() is False

    def test_true_when_set_true(self, monkeypatch):
        from backend.api.middleware import auth as m

        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "true")
        assert m._auth_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        from backend.api.middleware import auth as m

        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "FALSE")
        assert m._auth_enabled() is False

    def test_sem_env_var_cai_no_runtime_settings(
        self, monkeypatch, _isolated_runtime_settings
    ):
        """Sem VECTORA_AUTH_REQUIRED no ambiente, o valor persistido pelo
        wizard (setup-local) em app_settings é a fonte de verdade — não mais
        o .env."""
        from backend.api.middleware import auth as m

        monkeypatch.delenv("VECTORA_AUTH_REQUIRED", raising=False)
        _isolated_runtime_settings.auth_required = False
        assert m._auth_enabled() is False

    def test_env_var_tem_prioridade_sobre_runtime_settings(
        self, monkeypatch, _isolated_runtime_settings
    ):
        """Env var é override de operador (Docker/CI/testes) — ganha mesmo
        se app_settings diz o contrário."""
        from backend.api.middleware import auth as m

        _isolated_runtime_settings.auth_required = False
        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "true")
        assert m._auth_enabled() is True


# ---------------------------------------------------------------------------
# _get_virtual_local_user
# ---------------------------------------------------------------------------


class TestGetVirtualLocalUser:
    def test_sem_nome_configurado_usa_fallback(self, _isolated_runtime_settings):
        from backend.api.middleware.auth import _get_virtual_local_user

        user = _get_virtual_local_user()
        assert user.name == "Local User"
        assert user.id == "local"
        assert user.role == "root"

    def test_usa_nome_persistido_em_runtime_settings(self, _isolated_runtime_settings):
        from backend.api.middleware.auth import _get_virtual_local_user

        _isolated_runtime_settings.set_local_user("Bruno", "Vectora")
        user = _get_virtual_local_user()
        assert user.name == "Bruno"
        assert user.username == "bruno"

    def test_usa_username_persistido_quando_presente(self, _isolated_runtime_settings):
        """Username escolhido no onboarding tem prioridade sobre o slugify
        do nome."""
        from backend.api.middleware.auth import _get_virtual_local_user

        _isolated_runtime_settings.set_local_user(
            "Bruno", "Vectora", username="brunosoares"
        )
        user = _get_virtual_local_user()
        assert user.username == "brunosoares"

    def test_sem_username_cai_no_slugify_do_nome(self, _isolated_runtime_settings):
        from backend.api.middleware.auth import _get_virtual_local_user

        _isolated_runtime_settings.set_local_user("Ada Lovelace", "")
        user = _get_virtual_local_user()
        assert user.username == "adalovelace"


# ---------------------------------------------------------------------------
# AuthMiddleware via TestClient (integração mínima)
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    """App com auth habilitada e banco temporário."""
    os.environ["VECTORA_AUTH_REQUIRED"] = "true"

    import aiosqlite

    import backend.rbac.auth as auth_mod

    auth_mod._db_conn = None
    original_get_secret = auth_mod._get_secret
    auth_mod._get_secret = lambda: "middleware-test-secret-abcdefghi"  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    db_file = str(tmp_path / "mw_test.db")

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

    from fastapi.testclient import TestClient

    from backend.api.server import create_app

    app = create_app(serve_static=False)
    client = TestClient(app, raise_server_exceptions=False)
    yield client

    # Limpeza
    import asyncio

    async def _close():
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    asyncio.run(_close())
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"
    auth_mod._get_secret = original_get_secret


class TestAuthMiddlewareIntegration:
    def test_public_route_passes_without_token(self, auth_client):
        r = auth_client.get("/health")
        assert r.status_code == 200

    def test_auth_route_passes_without_token(self, auth_client):
        r = auth_client.get("/auth/has-users")
        assert r.status_code == 200

    def test_private_route_without_token_returns_401(self, auth_client):
        r = auth_client.get("/auth/me")
        assert r.status_code == 401

    def test_feedback_without_token_returns_401(self, auth_client: TestClient) -> None:
        r = auth_client.post("/feedback", json={"kind": "bug", "description": "falha"})
        assert r.status_code == 401

    def test_update_changelog_without_token_returns_401(self, auth_client):
        r = auth_client.get("/api/updates/changelog")
        assert r.status_code == 401

    def test_private_route_with_invalid_bearer_returns_401(self, auth_client):
        r = auth_client.get(
            "/auth/me", headers={"Authorization": "Bearer token-invalido"}
        )
        assert r.status_code == 401

    def test_private_route_with_valid_token_passes(self, auth_client):
        # Cria usuário e obtém token
        r_signup = auth_client.post(
            "/auth/signup",
            json={"email": "mw@test.com", "password": "middlewaretest1234"},
        )
        assert r_signup.status_code == 200
        access = r_signup.json()["access_token"]

        r = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200
        assert r.json()["email"] == "mw@test.com"

    def test_tool_usage_requires_authentication(self, auth_client):
        response = auth_client.get("/tools/usage")

        assert response.status_code == 401

    def test_url_preview_requires_authentication(self, auth_client: TestClient) -> None:
        response = auth_client.get("/url-preview?url=https://example.com")

        assert response.status_code == 401

    def test_tool_usage_contract_is_user_scoped_and_redacted(
        self, auth_client, monkeypatch
    ):
        """The HTTP contract exposes only per-user counts for available tools."""
        signup = auth_client.post(
            "/auth/signup",
            json={
                "email": "tool-usage-contract@test.com",
                "password": "contracttest1234",
            },
        )
        assert signup.status_code == 200
        access_token = signup.json()["access_token"]

        import backend.api.handlers.tools as tools_handler

        monkeypatch.setattr(
            tools_handler, "_all_tool_names", lambda: ["file_read", "terminal"]
        )

        observed_user_ids: list[str] = []

        async def fake_aggregate(user_id: str) -> dict[str, int]:
            observed_user_ids.append(user_id)
            return {"file_read": 3}

        from backend.services import tool_usage

        monkeypatch.setattr(tool_usage, "aggregate_last_7d", fake_aggregate)

        response = auth_client.get(
            "/tools/usage?user_id=attacker",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        assert observed_user_ids == [signup.json()["user"]["id"]]
        assert response.json() == {
            "window_days": 7,
            "usage": {"file_read": 3, "terminal": 0},
        }
        assert not {"arguments", "result", "results", "payload"}.intersection(
            response.json()
        )

    def test_tool_usage_returns_zero_for_tools_without_events(
        self, auth_client, monkeypatch
    ) -> None:
        signup = auth_client.post(
            "/auth/signup",
            json={
                "email": "tool-usage-empty@test.com",
                "password": "emptytest1234",
            },
        )
        assert signup.status_code == 200
        access_token = signup.json()["access_token"]

        import backend.api.handlers.tools as tools_handler

        monkeypatch.setattr(
            tools_handler, "_all_tool_names", lambda: ["file_read", "terminal"]
        )
        from backend.services import tool_usage

        async def no_events(_user_id: str) -> dict[str, int]:
            return {}

        monkeypatch.setattr(tool_usage, "aggregate_last_7d", no_events)

        response = auth_client.get(
            "/tools/usage", headers={"Authorization": f"Bearer {access_token}"}
        )

        assert response.status_code == 200
        assert response.json() == {
            "window_days": 7,
            "usage": {"file_read": 0, "terminal": 0},
        }

    def test_private_route_with_cookie_token_passes(self, auth_client):
        # Signup para ter cookies definidos
        r = auth_client.post(
            "/auth/signup",
            json={"email": "cookie@test.com", "password": "cookietest12345"},
        )
        # Se bloqueado (já há users), faz signin
        if r.status_code == 403:
            r = auth_client.post(
                "/auth/signin",
                json={"email": "mw@test.com", "password": "middlewaretest1234"},
            )
        assert r.status_code == 200
        # O TestClient mantém cookies automaticamente após set_cookie
        r_me = auth_client.get("/auth/me")
        assert r_me.status_code == 200

    def test_private_route_with_valid_service_token_passes(self, auth_client):
        """Erro/borda (feliz + inválido no mesmo teste): um
        token de serviço válido (`vst_...`) autentica como um JWT normal
        autenticaria; revogado é rejeitado com 401, igual a um JWT
        inválido."""
        import asyncio

        import backend.rbac.auth as auth_mod
        from backend.rbac import token_auth

        async def _criar_token():
            db = await auth_mod._get_db()
            return await token_auth.create_service_token(db, "ci-bot", ["*"])

        _, raw_token = asyncio.run(_criar_token())

        r = auth_client.get(
            "/auth/me", headers={"Authorization": f"Bearer {raw_token}"}
        )
        assert r.status_code == 200
        assert r.json()["id"].startswith("service:")

        async def _revogar(token_id: str):
            db = await auth_mod._get_db()
            await token_auth.revoke_service_token(db, token_id)

        token_obj, raw_token_2 = asyncio.run(_criar_token())
        asyncio.run(_revogar(token_obj.id))
        r_revogado = auth_client.get(
            "/auth/me", headers={"Authorization": f"Bearer {raw_token_2}"}
        )
        assert r_revogado.status_code == 401
