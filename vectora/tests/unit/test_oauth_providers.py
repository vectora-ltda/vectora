"""Testes dos OAuth providers GitLab, Google e Slack.

Cobre:
- Inicio de fluxo OAuth → redirect correto com parâmetros
- Callback com code válido → token salvo + redirect de sucesso
- Callback sem code → 400
- Status com token → connected=True
- Status sem token → connected=False
- Disconnect → chama delete_env_override
- Verificação de API key por provider
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.handlers.oauth import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)

    def fake_user(request):
        from types import SimpleNamespace

        request.state.user = SimpleNamespace(id="user-123")

    app.middleware("http")(lambda req, call_next: (fake_user(req), call_next(req))[1])
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture(autouse=True)
def allow_legacy_local_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep provider unit tests focused on local compatibility handlers."""
    monkeypatch.setenv("VECTORA_ALLOW_LOCAL_OAUTH_FALLBACK", "1")


# ---------------------------------------------------------------------------
# GitLab OAuth
# ---------------------------------------------------------------------------


class TestGitLabOAuth:
    _env = {
        "GITLAB_OAUTH_CLIENT_ID": "gl-client-id",
        "GITLAB_OAUTH_CLIENT_SECRET": "gl-client-secret",
        "GITLAB_BASE_URL": "https://gitlab.com",
    }

    def test_start_redirect(self, client: TestClient) -> None:
        with patch.dict("os.environ", self._env):
            r = client.get("/auth/gitlab")
        assert r.status_code == 302
        loc = r.headers["location"]
        assert "gitlab.com/oauth/authorize" in loc
        assert "gl-client-id" in loc
        assert "user-123" in loc

    def test_callback_sem_code(self, client: TestClient) -> None:
        with patch.dict("os.environ", self._env):
            r = client.get("/auth/gitlab/callback")
        assert r.status_code == 400

    @patch("backend.rbac.auth.set_env_override", new_callable=AsyncMock)
    def test_callback_token_salvo(
        self, mock_set: AsyncMock, client: TestClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "gltoken123"}

        with (
            patch.dict("os.environ", self._env),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            r = client.get("/auth/gitlab/callback?code=abc&state=user-123")

        assert r.status_code in {302, 307}
        assert "oauth_success=gitlab" in r.headers.get("location", "")

    def test_status_sem_token(self, client: TestClient) -> None:
        with (
            patch.dict(
                "os.environ", {k: v for k, v in self._env.items() if "CLIENT" not in k}
            ),
            patch(
                "backend.rbac.auth.get_env_overrides",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            r = client.get("/auth/gitlab/status")
        assert r.status_code == 200
        assert r.json()["connected"] is False

    @patch("backend.rbac.auth.delete_env_override", new_callable=AsyncMock)
    def test_disconnect(self, mock_del: AsyncMock, client: TestClient) -> None:
        r = client.request("DELETE", "/auth/gitlab")
        assert r.status_code == 200
        assert r.json()["status"] == "disconnected"


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------


class TestGoogleOAuth:
    _env = {
        "GOOGLE_OAUTH_CLIENT_ID": "google-client-id.apps.googleusercontent.com",
        "GOOGLE_OAUTH_CLIENT_SECRET": "GOCSPX-secret",
    }

    def test_start_redirect(self, client: TestClient) -> None:
        with patch.dict("os.environ", self._env):
            r = client.get("/auth/google")
        assert r.status_code == 302
        loc = r.headers["location"]
        assert "accounts.google.com" in loc
        assert "drive.readonly" in loc
        assert "gmail.readonly" in loc

    def test_callback_sem_code(self, client: TestClient) -> None:
        with patch.dict("os.environ", self._env):
            r = client.get("/auth/google/callback")
        assert r.status_code == 400

    @patch("backend.rbac.auth.set_env_override", new_callable=AsyncMock)
    def test_callback_salva_access_e_refresh(
        self, mock_set: AsyncMock, client: TestClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "goog-access",
            "refresh_token": "goog-refresh",
        }

        with (
            patch.dict("os.environ", self._env),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            r = client.get("/auth/google/callback?code=gcode&state=user-123")

        assert r.status_code in {302, 307}
        assert "oauth_success=google" in r.headers.get("location", "")
        calls = [str(c) for c in mock_set.await_args_list]
        assert any("GOOGLE_ACCESS_TOKEN" in c for c in calls)
        assert any("GOOGLE_REFRESH_TOKEN" in c for c in calls)

    def test_status_sem_token(self, client: TestClient) -> None:
        with patch(
            "backend.rbac.auth.get_env_overrides",
            new_callable=AsyncMock,
            return_value={},
        ):
            r = client.get("/auth/google/status")
        assert r.json()["connected"] is False

    @patch("backend.rbac.auth.delete_env_override", new_callable=AsyncMock)
    def test_disconnect_remove_ambos_tokens(
        self, mock_del: AsyncMock, client: TestClient
    ) -> None:
        r = client.request("DELETE", "/auth/google")
        assert r.status_code == 200
        assert mock_del.await_count == 2  # access + refresh


# ---------------------------------------------------------------------------
# Slack OAuth
# ---------------------------------------------------------------------------


class TestSlackOAuth:
    _env = {
        "SLACK_OAUTH_CLIENT_ID": "xxxx.yyyy",
        "SLACK_OAUTH_CLIENT_SECRET": "slack-secret",
    }

    def test_start_redirect(self, client: TestClient) -> None:
        with patch.dict("os.environ", self._env):
            r = client.get("/auth/slack")
        assert r.status_code == 302
        loc = r.headers["location"]
        assert "slack.com/oauth/v2/authorize" in loc
        assert "xxxx.yyyy" in loc

    def test_callback_sem_code(self, client: TestClient) -> None:
        with patch.dict("os.environ", self._env):
            r = client.get("/auth/slack/callback")
        assert r.status_code == 400

    @patch("backend.rbac.auth.set_env_override", new_callable=AsyncMock)
    def test_callback_token_salvo(
        self, mock_set: AsyncMock, client: TestClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "access_token": "xoxb-slack"}

        with (
            patch.dict("os.environ", self._env),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            r = client.get("/auth/slack/callback?code=slcode&state=user-123")

        assert r.status_code in {302, 307}
        assert "oauth_success=slack" in r.headers.get("location", "")

    def test_callback_oauth_error(self, client: TestClient) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "error": "invalid_code"}

        with (
            patch.dict("os.environ", self._env),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            r = client.get("/auth/slack/callback?code=bad&state=user-123")

        loc = r.headers.get("location", "")
        assert "oauth_error=slack" in loc

    def test_status_sem_token(self, client: TestClient) -> None:
        with patch(
            "backend.rbac.auth.get_env_overrides",
            new_callable=AsyncMock,
            return_value={},
        ):
            r = client.get("/auth/slack/status")
        assert r.json()["connected"] is False

    @patch("backend.rbac.auth.delete_env_override", new_callable=AsyncMock)
    def test_disconnect(self, mock_del: AsyncMock, client: TestClient) -> None:
        r = client.request("DELETE", "/auth/slack")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Registry de integrações — novos providers aparecem
# ---------------------------------------------------------------------------


class TestIntegrationsRegistry:
    def test_novos_providers_presentes(self, client: TestClient) -> None:
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        ids = {i["id"] for i in INTEGRATIONS_REGISTRY}
        expected = {
            "github",
            "gitlab",
            "google",
            "google-drive",
            "gmail",
            "slack",
            "linear",
            "jira",
            "notion",
            "resend",
            "sendgrid",
            "mailgun",
        }
        missing = expected - ids
        assert not missing, f"Providers faltando no registry: {missing}"

    def test_todos_tem_env_var(self) -> None:
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        for integ in INTEGRATIONS_REGISTRY:
            assert "env_var" in integ, f"{integ['id']} sem env_var"

    def test_oauth_providers_tem_scopes(self) -> None:
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        # Deriva do próprio `kind` em vez de uma lista fixa de ids: quando uma
        # integração deixa de ser OAuth (Slack virou Socket Mode com token
        # colado), a lista fixa passa a exigir scopes de quem não tem mais.
        for integ in INTEGRATIONS_REGISTRY:
            if integ["kind"] in ("oauth", "hybrid"):
                assert integ.get("oauth_scopes") or integ.get("parent"), (
                    f"{integ['id']} oauth sem oauth_scopes nem parent"
                )

        # Erro/borda: integração `apikey` não pode carregar `oauth_scopes` —
        # seria configuração morta induzindo a UI a oferecer botão de OAuth.
        for integ in INTEGRATIONS_REGISTRY:
            if integ["kind"] == "apikey":
                assert not integ.get("oauth_scopes"), (
                    f"{integ['id']} é apikey mas declara oauth_scopes"
                )


# ---------------------------------------------------------------------------
# GET /integrations — connected via env_var_aliases, sem depender do relay
# ---------------------------------------------------------------------------


class TestOauthConfiguredFlag:
    """`oauth_configured` só é true quando o operador registrou o app
    próprio no provider (GitHub App pro GitHub, OAuth App clássico pros
    demais) com CLIENT_ID+SECRET — sem isso, "Conectar via OAuth" sempre
    falharia com 503 e a UI usa esta flag pra nunca oferecer o botão."""

    def test_sem_client_id_secret_oauth_configured_false(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("GITLAB_OAUTH_CLIENT_ID", "GITLAB_OAUTH_CLIENT_SECRET"):
            monkeypatch.delenv(var, raising=False)
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        gitlab = next(i for i in resp.json()["integrations"] if i["id"] == "gitlab")
        assert gitlab["oauth_configured"] is False

    def test_com_client_id_e_secret_oauth_configured_true(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITLAB_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GITLAB_OAUTH_CLIENT_SECRET", "csecret")
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        gitlab = next(i for i in resp.json()["integrations"] if i["id"] == "gitlab")
        assert gitlab["oauth_configured"] is True

    def test_provider_filho_herda_configuracao_do_pai(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """google-drive/gmail não têm CLIENT_ID próprio — usam o do
        provider pai ("google", via `parent`)."""
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        drive = next(
            i for i in resp.json()["integrations"] if i["id"] == "google-drive"
        )
        assert drive["oauth_configured"] is True

    def test_provider_apikey_nunca_tem_oauth_configured(
        self, client: TestClient
    ) -> None:
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        gemini = next(i for i in resp.json()["integrations"] if i["id"] == "gemini")
        assert gemini["oauth_configured"] is False


class TestListIntegrationsAlias:
    def test_github_connected_via_alias_sem_env_var_principal(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Erro/borda: usuário configurou só GITHUB_PERSONAL_ACCESS_TOKEN (o
        nome que o MCP marketplace usa), sem GITHUB_TOKEN — o card GitHub
        ainda deve aparecer conectado, não depende do relay."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_fake")
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        assert resp.status_code == 200
        github = next(i for i in resp.json()["integrations"] if i["id"] == "github")
        assert github["connected"] is True

    def test_gemini_desconectado_sem_google_api_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        assert resp.status_code == 200
        gemini = next(i for i in resp.json()["integrations"] if i["id"] == "gemini")
        assert gemini["connected"] is False

    def test_gemini_conectado_com_google_api_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", "AIza-fake")
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        assert resp.status_code == 200
        gemini = next(i for i in resp.json()["integrations"] if i["id"] == "gemini")
        assert gemini["connected"] is True


class TestOauthConnectedFlag:
    """`oauth_connected` separa "tem override setado" (`connected`) de
    "conectou via OAuth de verdade" — colar GITHUB_TOKEN manualmente também
    deixa `connected=True`, e a UI não pode mostrar "Conexão ativa (OAuth)"
    nesse caso."""

    def test_token_colado_manualmente_fica_connected_mas_nao_oauth_connected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with patch(
            "backend.rbac.auth.get_env_overrides",
            AsyncMock(return_value={"GITHUB_TOKEN": "ghp_colado_a_mao"}),
        ):
            resp = client.get("/integrations")
        github = next(i for i in resp.json()["integrations"] if i["id"] == "github")
        assert github["connected"] is True
        assert github["oauth_connected"] is False

    def test_token_via_callback_oauth_fica_connected_e_oauth_connected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with patch(
            "backend.rbac.auth.get_env_overrides",
            AsyncMock(
                return_value={
                    "GITHUB_TOKEN": "ghp_via_oauth",
                    "__oauth_source__:GITHUB_TOKEN": "1",
                }
            ),
        ):
            resp = client.get("/integrations")
        github = next(i for i in resp.json()["integrations"] if i["id"] == "github")
        assert github["connected"] is True
        assert github["oauth_connected"] is True

    def test_sem_nenhum_override_fica_desconectado_nos_dois(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
        with patch("backend.rbac.auth.get_env_overrides", AsyncMock(return_value={})):
            resp = client.get("/integrations")
        github = next(i for i in resp.json()["integrations"] if i["id"] == "github")
        assert github["connected"] is False
        assert github["oauth_connected"] is False


class TestSetupHint:
    """`setup_hint` é a linha inline de "como obter esta credencial" que a aba
    Integrações mostra ao expandir o card — o catálogo do backend é a única
    fonte, a UI não tem texto por plataforma."""

    def test_plataformas_de_connect_tem_setup_hint(self) -> None:
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        by_id = {i["id"]: i for i in INTEGRATIONS_REGISTRY}
        for plat in ("telegram", "discord", "slack", "email-connect"):
            hint = by_id[plat].get("setup_hint", "")
            assert hint and len(hint) > 20, f"{plat} sem setup_hint utilizável"

        # Erro/borda: nenhuma integração pode declarar `setup_hint` vazio ou
        # só espaço — a UI renderiza o parágrafo por truthiness, e um valor
        # branco viraria um bloco de espaçamento fantasma no card.
        for integ in INTEGRATIONS_REGISTRY:
            if "setup_hint" in integ:
                assert integ["setup_hint"].strip(), (
                    f"{integ['id']} declara setup_hint em branco"
                )

    def test_setup_hint_chega_na_resposta_de_integrations(
        self, client: TestClient
    ) -> None:
        resp = client.get("/integrations")
        assert resp.status_code == 200
        items = {i["id"]: i for i in resp.json()["integrations"]}
        assert items["telegram"]["setup_hint"].startswith("No Telegram")
        # Erro/borda: quem não declara hint não ganha um campo inventado —
        # a UI usa a ausência pra não renderizar o parágrafo.
        assert "setup_hint" not in items["gemini"]
