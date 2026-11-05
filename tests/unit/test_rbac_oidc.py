"""Testes de `backend/rbac/oidc.py` — login SSO via OIDC (Authorization
Code + PKCE S256).

Cobre PKCE (S256, nunca `plain`), descoberta `.well-known` (feliz + IDP
fora do ar/resposta malformada), verificação de `id_token` via JWKS (feliz
+ assinatura inválida), e o handshake `start_login`/`complete_login`
(feliz + CSRF via `state` desconhecido/reutilizado). Erro/borda sempre no
mesmo teste do caminho feliz correspondente, CLAUDE.md §18.
"""

from __future__ import annotations

import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.rbac import oidc

_CONFIG = oidc.OIDCConfig(
    client_id="vectora-client",
    client_secret="shh",
    issuer_url="https://idp.example.com",
)
_DISCOVERY = oidc.OIDCDiscovery(
    authorization_endpoint="https://idp.example.com/authorize",
    token_endpoint="https://idp.example.com/token",
    jwks_uri="https://idp.example.com/jwks",
)


@pytest.fixture(autouse=True)
def _clean_pending_logins():
    oidc._pending_logins.clear()
    yield
    oidc._pending_logins.clear()


class TestPKCE:
    def test_gera_par_s256_valido(self):
        verifier, challenge = oidc.generate_pkce_pair()

        assert len(verifier) >= 43
        import base64

        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected

        # Erro/borda: dois pares gerados nunca colidem (entropia real).
        verifier2, challenge2 = oidc.generate_pkce_pair()
        assert verifier != verifier2
        assert challenge != challenge2


class TestDiscover:
    async def test_descoberta_feliz_devolve_endpoints(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "authorization_endpoint": _DISCOVERY.authorization_endpoint,
            "token_endpoint": _DISCOVERY.token_endpoint,
            "jwks_uri": _DISCOVERY.jwks_uri,
            "userinfo_endpoint": "https://idp.example.com/userinfo",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oidc.discover("https://idp.example.com")

        assert result.authorization_endpoint == _DISCOVERY.authorization_endpoint
        assert result.userinfo_endpoint == "https://idp.example.com/userinfo"

    async def test_idp_fora_do_ar_ou_resposta_incompleta_viram_oidc_error(self):
        """Erro/borda: falha de rede e JSON sem os campos obrigatórios viram
        `OIDCError` tipado, nunca a exceção crua de `httpx`."""
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("recusado")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(oidc.OIDCError):
                await oidc.discover("https://idp.example.com")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "authorization_endpoint": "x"
        }  # sem token_endpoint/jwks_uri
        mock_client2 = AsyncMock()
        mock_client2.get.return_value = mock_resp
        mock_client2.__aenter__.return_value = mock_client2
        mock_client2.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client2):
            with pytest.raises(oidc.OIDCError):
                await oidc.discover("https://idp.example.com")


class TestVerifyIdToken:
    def test_token_valido_devolve_claims(self):
        fake_claims = {"sub": "user-1", "email": "dev@example.com"}
        fake_signing_key = MagicMock()
        fake_signing_key.key = "fake-key"

        with (
            patch("jwt.PyJWKClient") as mock_jwk_cls,
            patch("jwt.decode", return_value=fake_claims) as mock_decode,
        ):
            mock_jwk_cls.return_value.get_signing_key_from_jwt.return_value = (
                fake_signing_key
            )
            result = oidc.verify_id_token(_DISCOVERY, _CONFIG, "fake.jwt.token")

        assert result == fake_claims
        mock_decode.assert_called_once()

    def test_assinatura_invalida_vira_oidc_error_sem_propagar_jwterror_cru(self):
        import jwt as pyjwt

        with patch("jwt.PyJWKClient") as mock_jwk_cls:
            mock_jwk_cls.return_value.get_signing_key_from_jwt.side_effect = (
                pyjwt.InvalidSignatureError("assinatura não bate")
            )
            with pytest.raises(oidc.OIDCError):
                oidc.verify_id_token(_DISCOVERY, _CONFIG, "fake.jwt.token")


class TestStartAndCompleteLogin:
    def test_start_login_gera_url_e_guarda_state_pendente(self):
        url = oidc.start_login(
            _DISCOVERY, _CONFIG, redirect_uri="https://app.local/callback"
        )

        assert url.startswith(_DISCOVERY.authorization_endpoint)
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert len(oidc._pending_logins) == 1

    async def test_complete_login_feliz_e_state_desconhecido_vira_erro(self):
        """Erro/borda: `state` nunca visto (ou já consumido) é rejeitado
        como possível CSRF — nunca completa o login silenciosamente."""
        with pytest.raises(oidc.OIDCError, match="state"):
            await oidc.complete_login(
                _DISCOVERY, _CONFIG, state="desconhecido", code="abc"
            )

        url = oidc.start_login(
            _DISCOVERY, _CONFIG, redirect_uri="https://app.local/callback"
        )
        state = next(iter(oidc._pending_logins))

        fake_claims = {"sub": "u1", "email": "dev@example.com"}
        with (
            patch(
                "backend.rbac.oidc.exchange_code_for_tokens",
                new=AsyncMock(return_value={"id_token": "fake.jwt"}),
            ),
            patch("backend.rbac.oidc.verify_id_token", return_value=fake_claims),
        ):
            claims = await oidc.complete_login(
                _DISCOVERY, _CONFIG, state=state, code="abc"
            )

        assert claims == fake_claims
        # State consumido — reuso (replay) é rejeitado.
        with pytest.raises(oidc.OIDCError):
            await oidc.complete_login(_DISCOVERY, _CONFIG, state=state, code="abc")
        assert url  # sanity: url foi de fato gerada no arrange acima

    async def test_resposta_do_idp_sem_id_token_vira_oidc_error(self):
        oidc.start_login(_DISCOVERY, _CONFIG, redirect_uri="https://app.local/callback")
        state = next(iter(oidc._pending_logins))

        with patch(
            "backend.rbac.oidc.exchange_code_for_tokens",
            new=AsyncMock(return_value={"access_token": "sem-id-token-aqui"}),
        ):
            with pytest.raises(oidc.OIDCError, match="id_token"):
                await oidc.complete_login(_DISCOVERY, _CONFIG, state=state, code="abc")

    def test_state_expirado_e_limpo_e_tratado_como_desconhecido(self, monkeypatch):
        """Erro/borda: TTL de 10min — state velho nunca completa login,
        mesmo com code/state corretos."""
        oidc.start_login(_DISCOVERY, _CONFIG, redirect_uri="https://app.local/callback")
        state = next(iter(oidc._pending_logins))
        oidc._pending_logins[state].created_at = time.monotonic() - 9999

        oidc._cleanup_expired()
        assert state not in oidc._pending_logins
