"""OAuth redirect_uri via gateway quando token disponível."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.requests import Request


@pytest.fixture
def gateway_token_file(tmp_path: Path) -> Path:
    return tmp_path / "gateway_token"


class TestGatewayRedirectUri:
    def test_usa_gateway_quando_token_existe(self, gateway_token_file: Path) -> None:
        gateway_token_file.write_text("abc123")
        from backend.api.handlers.oauth import _gateway_callback_url

        url = _gateway_callback_url("github", token_path=gateway_token_file)
        assert url == "https://abc123.vectora.chat/auth/github/callback"

    def test_retorna_none_sem_token(self, gateway_token_file: Path) -> None:
        from backend.api.handlers.oauth import _gateway_callback_url

        url = _gateway_callback_url("github", token_path=gateway_token_file)
        assert url is None

    def test_funciona_com_qualquer_provider(self, gateway_token_file: Path) -> None:
        gateway_token_file.write_text("xyz789")
        from backend.api.handlers.oauth import _gateway_callback_url

        assert _gateway_callback_url("slack", token_path=gateway_token_file) == (
            "https://xyz789.vectora.chat/auth/slack/callback"
        )
        assert _gateway_callback_url("google", token_path=gateway_token_file) == (
            "https://xyz789.vectora.chat/auth/google/callback"
        )


class TestGithubCfgRedirect:
    def test_env_var_tem_prioridade_sobre_gateway(
        self, gateway_token_file: Path
    ) -> None:
        gateway_token_file.write_text("abc123")
        env = {
            "GITHUB_OAUTH_CLIENT_ID": "cid",
            "GITHUB_OAUTH_CLIENT_SECRET": "csec",
            "GITHUB_OAUTH_REDIRECT_URI": "https://custom.example.com/auth/github/callback",
        }
        from backend.api.handlers.oauth import _github_cfg

        with patch.dict(os.environ, env):
            with patch(
                "backend.api.handlers.oauth._GATEWAY_TOKEN_PATH", gateway_token_file
            ):
                _, _, redirect_uri = _github_cfg()
        assert redirect_uri == "https://custom.example.com/auth/github/callback"

    def test_usa_gateway_quando_nao_ha_env_var(self, gateway_token_file: Path) -> None:
        gateway_token_file.write_text("abc123")
        env = {
            "GITHUB_OAUTH_CLIENT_ID": "cid",
            "GITHUB_OAUTH_CLIENT_SECRET": "csec",
        }
        env.pop("GITHUB_OAUTH_REDIRECT_URI", None)
        from backend.api.handlers.oauth import _github_cfg

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GITHUB_OAUTH_REDIRECT_URI", None)
            with patch(
                "backend.api.handlers.oauth._GATEWAY_TOKEN_PATH", gateway_token_file
            ):
                _, _, redirect_uri = _github_cfg()
        assert redirect_uri == "https://abc123.vectora.chat/auth/github/callback"

    def test_usa_localhost_sem_gateway_sem_env_var(
        self, gateway_token_file: Path
    ) -> None:
        env = {
            "GITHUB_OAUTH_CLIENT_ID": "cid",
            "GITHUB_OAUTH_CLIENT_SECRET": "csec",
        }
        from backend.api.handlers.oauth import _github_cfg

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GITHUB_OAUTH_REDIRECT_URI", None)
            with patch(
                "backend.api.handlers.oauth._GATEWAY_TOKEN_PATH", gateway_token_file
            ):
                _, _, redirect_uri = _github_cfg()
        assert redirect_uri == "http://localhost:8080/auth/github/callback"


class TestBrokerTransaction:
    @pytest.fixture(autouse=True)
    def clear_transactions(self) -> None:
        from backend.api.handlers import oauth

        oauth._broker_transactions.clear()

    def test_remove_transacoes_expiradas_antes_de_consultar(self) -> None:
        from backend.api.handlers import oauth

        oauth._broker_transactions["expired"] = oauth._BrokerTransaction(
            state="expired",
            user_id="user-1",
            provider="github",
            proof="old-proof",
            expires_at=1,
        )
        request = Request({"type": "http", "query_string": b"oauth_proof=old-proof"})

        assert not oauth._consume_broker_transaction(
            request, "expired", "github", "user-1"
        )
        assert "expired" not in oauth._broker_transactions

    @pytest.mark.asyncio
    async def test_gateway_ausente_remove_transacao_criada(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.api.handlers import oauth

        monkeypatch.setattr(
            oauth, "_get_user", lambda _request: type("User", (), {"id": "user-1"})()
        )
        monkeypatch.setattr(oauth, "_gateway_callback_url", lambda _provider: None)
        monkeypatch.setenv("VECTORA_OAUTH_BROKER_URL", "https://services.example")
        monkeypatch.setenv("VECTORA_OAUTH_SECRET", "secret")
        request = Request({"type": "http", "query_string": b""})

        with pytest.raises(Exception, match="gateway Vectora conectado"):
            await oauth._broker_start(request, "github")
        assert oauth._broker_transactions == {}

    @pytest.mark.asyncio
    async def test_falha_de_transporte_remove_transacao_criada(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        from backend.api.handlers import oauth

        monkeypatch.setattr(
            oauth, "_get_user", lambda _request: type("User", (), {"id": "user-1"})()
        )
        monkeypatch.setattr(
            oauth,
            "_gateway_callback_url",
            lambda _provider: "https://token.vectora.chat/auth/github/callback",
        )
        monkeypatch.setenv("VECTORA_OAUTH_BROKER_URL", "https://services.example")
        monkeypatch.setenv("VECTORA_OAUTH_SECRET", "secret")
        request = Request({"type": "http", "query_string": b""})

        class FailingClient:
            async def __aenter__(self) -> FailingClient:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def get(self, *_args: object, **_kwargs: object) -> None:
                raise httpx.ConnectError("broker offline")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FailingClient())

        with pytest.raises(httpx.ConnectError):
            await oauth._broker_start(request, "github")
        assert oauth._broker_transactions == {}

    def test_transacao_eh_one_shot_e_vinculada_a_prova_do_callback(self) -> None:
        from backend.api.handlers import oauth

        request = Request(
            {
                "type": "http",
                "query_string": b"oauth_proof=signed-proof",
                "headers": [
                    (
                        b"x-test",
                        b"1",
                    )
                ],
            }
        )
        oauth._broker_transactions["signed-state"] = oauth._BrokerTransaction(
            state="signed-state",
            user_id="user-1",
            provider="github",
            proof="signed-proof",
            expires_at=9999999999,
        )

        assert oauth._consume_broker_transaction(
            request, "signed-state", "github", "user-1"
        )
        assert not oauth._consume_broker_transaction(
            request, "signed-state", "github", "user-1"
        )
