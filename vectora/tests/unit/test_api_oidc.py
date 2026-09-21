"""Testes de integração dos endpoints HTTP de SSO/OIDC (`/auth/oidc/*`).

GET /auth/oidc/status    — feliz (configurado/não configurado)
GET /auth/oidc/login     — feliz (redireciona pro IDP) + erro (sem config,
                            IDP fora do ar na descoberta)
GET /auth/oidc/callback  — feliz (provisiona/loga e grava cookies) + erro
                            (IDP recusou, sem code/state, id_token sem email)

Mesmo padrão de `test_api_auth.py`: TestClient + banco SQLite temporário,
mockando só a fronteira de rede (`httpx`/JWKS), nunca o handler em si.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_and_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_api_oidc.db")
    os.environ["VECTORA_AUTH_REQUIRED"] = "true"

    import aiosqlite

    import backend.rbac.auth as auth_mod

    auth_mod._db_conn = None
    monkeypatch.setattr(auth_mod, "_get_secret", lambda: "oidc-api-test-secret-xxxxx")

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

    from backend.api.server import create_app

    app = create_app(serve_static=False)
    yield app

    import asyncio

    async def _close():
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    asyncio.run(_close())
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"


@pytest.fixture
def client(app_and_db):
    return TestClient(app_and_db, raise_server_exceptions=False)


class TestOidcStatus:
    def test_desabilitado_sem_config(self, client):
        r = client.get("/auth/oidc/status")
        assert r.status_code == 200
        assert r.json() == {"enabled": False}

    def test_habilitado_com_config_completa(self, client):
        from backend.api.handlers import oidc as oidc_handler
        from backend.rbac.oidc import OIDCConfig

        fake_config = OIDCConfig(
            client_id="cid", client_secret="csecret", issuer_url="https://idp.test"
        )
        with patch.object(oidc_handler, "_load_config", return_value=fake_config):
            r = client.get("/auth/oidc/status")
        assert r.json() == {"enabled": True}


class TestOidcLogin:
    def test_sem_config_devolve_404(self, client):
        r = client.get("/auth/oidc/login", follow_redirects=False)
        assert r.status_code == 404

    def test_idp_fora_do_ar_na_descoberta_vira_502(self, client):
        from backend.api.handlers import oidc as oidc_handler
        from backend.rbac.oidc import OIDCConfig, OIDCError

        fake_config = OIDCConfig(
            client_id="cid", client_secret="csecret", issuer_url="https://idp.test"
        )
        with (
            patch.object(oidc_handler, "_load_config", return_value=fake_config),
            patch(
                "backend.rbac.oidc.discover",
                new=AsyncMock(side_effect=OIDCError("timeout")),
            ),
        ):
            r = client.get("/auth/oidc/login", follow_redirects=False)
        assert r.status_code == 502

    def test_feliz_redireciona_pro_idp_com_pkce(self, client):
        from backend.api.handlers import oidc as oidc_handler
        from backend.rbac.oidc import OIDCConfig, OIDCDiscovery

        fake_config = OIDCConfig(
            client_id="cid", client_secret="csecret", issuer_url="https://idp.test"
        )
        fake_discovery = OIDCDiscovery(
            authorization_endpoint="https://idp.test/authorize",
            token_endpoint="https://idp.test/token",
            jwks_uri="https://idp.test/jwks",
        )
        with (
            patch.object(oidc_handler, "_load_config", return_value=fake_config),
            patch(
                "backend.rbac.oidc.discover", new=AsyncMock(return_value=fake_discovery)
            ),
        ):
            r = client.get("/auth/oidc/login", follow_redirects=False)

        assert r.status_code == 302
        location = r.headers["location"]
        assert location.startswith("https://idp.test/authorize")
        assert "code_challenge=" in location
        assert "state=" in location


class TestOidcCallback:
    def test_erro_do_idp_no_query_vira_401(self, client):
        r = client.get(
            "/auth/oidc/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        assert r.status_code == 401

    def test_sem_code_ou_state_vira_400(self, client):
        r = client.get("/auth/oidc/callback", follow_redirects=False)
        assert r.status_code == 400

    def test_sem_config_devolve_404(self, client):
        r = client.get(
            "/auth/oidc/callback",
            params={"code": "abc", "state": "xyz"},
            follow_redirects=False,
        )
        assert r.status_code == 404

    def test_id_token_sem_claim_email_vira_401(self, client):
        from backend.api.handlers import oidc as oidc_handler
        from backend.rbac.oidc import OIDCConfig, OIDCDiscovery

        fake_config = OIDCConfig(
            client_id="cid", client_secret="csecret", issuer_url="https://idp.test"
        )
        fake_discovery = OIDCDiscovery(
            authorization_endpoint="https://idp.test/authorize",
            token_endpoint="https://idp.test/token",
            jwks_uri="https://idp.test/jwks",
        )
        with (
            patch.object(oidc_handler, "_load_config", return_value=fake_config),
            patch(
                "backend.rbac.oidc.discover", new=AsyncMock(return_value=fake_discovery)
            ),
            patch(
                "backend.rbac.oidc.complete_login",
                new=AsyncMock(return_value={"sub": "u1"}),
            ),
        ):
            r = client.get(
                "/auth/oidc/callback",
                params={"code": "abc", "state": "xyz"},
                follow_redirects=False,
            )
        assert r.status_code == 401

    def test_feliz_provisiona_usuario_e_grava_cookies(self, client):
        from backend.api.handlers import oidc as oidc_handler
        from backend.rbac.oidc import OIDCConfig, OIDCDiscovery

        fake_config = OIDCConfig(
            client_id="cid", client_secret="csecret", issuer_url="https://idp.test"
        )
        fake_discovery = OIDCDiscovery(
            authorization_endpoint="https://idp.test/authorize",
            token_endpoint="https://idp.test/token",
            jwks_uri="https://idp.test/jwks",
        )
        with (
            patch.object(oidc_handler, "_load_config", return_value=fake_config),
            patch(
                "backend.rbac.oidc.discover", new=AsyncMock(return_value=fake_discovery)
            ),
            patch(
                "backend.rbac.oidc.complete_login",
                new=AsyncMock(
                    return_value={
                        "sub": "u1",
                        "email": "sso@example.com",
                        "name": "SSO User",
                        "email_verified": True,
                    }
                ),
            ),
        ):
            r = client.get(
                "/auth/oidc/callback",
                params={"code": "abc", "state": "xyz"},
                follow_redirects=False,
            )

        assert r.status_code == 302
        assert r.headers["location"] == "/"
        assert "vectora_access" in r.cookies
        assert "vectora_refresh" in r.cookies

        me = client.get("/auth/me", cookies=r.cookies)
        assert me.status_code == 200
        assert me.json()["email"] == "sso@example.com"

    @pytest.mark.parametrize("email_verified", [False, None])
    def test_email_nao_verificado_rejeita_e_nao_provisiona(
        self, client: TestClient, email_verified: bool | None
    ) -> None:
        from backend.api.handlers import oidc as oidc_handler
        from backend.rbac.oidc import OIDCConfig, OIDCDiscovery

        fake_config = OIDCConfig(
            client_id="cid", client_secret="csecret", issuer_url="https://idp.test"
        )
        fake_discovery = OIDCDiscovery(
            authorization_endpoint="https://idp.test/authorize",
            token_endpoint="https://idp.test/token",
            jwks_uri="https://idp.test/jwks",
        )
        claims: dict[str, object] = {
            "sub": "u1",
            "email": "sso@example.com",
            "name": "SSO User",
        }
        if email_verified is not None:
            claims["email_verified"] = email_verified
        provision = AsyncMock()
        with (
            patch.object(oidc_handler, "_load_config", return_value=fake_config),
            patch(
                "backend.rbac.oidc.discover", new=AsyncMock(return_value=fake_discovery)
            ),
            patch(
                "backend.rbac.oidc.complete_login",
                new=AsyncMock(return_value=claims),
            ),
            patch("backend.rbac.auth.provision_or_login_sso", new=provision),
        ):
            response = client.get(
                "/auth/oidc/callback",
                params={"code": "abc", "state": "xyz"},
                follow_redirects=False,
            )

        assert response.status_code == 401
        provision.assert_not_awaited()
