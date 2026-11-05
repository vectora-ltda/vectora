"""SSO/OIDC — login via qualquer provedor de identidade compatível com
OpenID Connect (Authorization Code + PKCE S256).

Substitui a promessa vazia que `subscription.py` só descrevia em docstring
("SSO/SAML" como feature Pro sem código nenhum por trás) — inspirado no
`DashboardAuthProvider` do Hermes (`plugins/dashboard_auth/self_hosted`):
descoberta via `.well-known/openid-configuration`, verificação via JWKS,
PKCE obrigatório (nunca client secret puro no fluxo do browser).

Single IDP por instância, configurável via o registry declarativo
(`backend/config/registry.py`, categoria `integrations`) — `client_id`,
`client_secret`, `issuer_url`. Sem IDP configurado, o login local
(email+senha) continua sendo o único caminho — SSO é aditivo, nunca
obrigatório.

Fluxo:
    1. ``start_login()`` — gera PKCE, busca a descoberta do IDP, monta a URL
       de autorização, guarda o par (state -> verifier) num store em
       memória de processo com TTL curto (login é interativo, minutos, não
       precisa sobreviver a restart do backend).
    2. Usuário autentica no IDP, é redirecionado de volta com `code`+`state`.
    3. ``complete_login(state, code)`` — valida o `state`, troca o `code`
       pelos tokens do IDP, verifica o `id_token` via JWKS, e provisiona ou
       autentica o usuário local pelo `email` do claim (reusa
       ``backend.rbac.auth.provision_or_login_sso``).

Store de state em memória (não em `pending_approvals`/schema): login SSO
é single-process, single-tenant-por-instância — não há cenário de HA
horizontal no Vectora hoje (CLAUDE.md: backend é sempre um processo só).
Se o backend reiniciar no meio do handshake, o usuário só precisa clicar
"Entrar com SSO" de novo — custo baixo, sem precisar de schema novo.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

#: TTL do state pendente — login interativo nunca deveria levar mais que
#: alguns minutos; além disso, o handshake é considerado abandonado.
_PENDING_STATE_TTL_S = 600


class OIDCError(Exception):
    """Erro tipado de qualquer etapa do fluxo OIDC — nunca deixa exceção
    crua de `httpx`/`jwt` vazar pro handler HTTP."""


@dataclass(slots=True)
class OIDCConfig:
    client_id: str
    client_secret: str
    issuer_url: str


@dataclass(slots=True)
class OIDCDiscovery:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str = ""


@dataclass(slots=True)
class _PendingLogin:
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.monotonic)


#: `state` -> pending login. Processo único, sem persistência — ver
#: docstring do módulo.
_pending_logins: dict[str, _PendingLogin] = {}


def _cleanup_expired() -> None:
    limite = time.monotonic() - _PENDING_STATE_TTL_S
    expirados = [s for s, p in _pending_logins.items() if p.created_at < limite]
    for s in expirados:
        _pending_logins.pop(s, None)


def generate_pkce_pair() -> tuple[str, str]:
    """Retorna `(code_verifier, code_challenge)` — S256 apenas (`plain`
    nunca é oferecido, é o método fraco que o PKCE existe pra substituir)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def discover(issuer_url: str) -> OIDCDiscovery:
    """Busca `{issuer_url}/.well-known/openid-configuration`.

    `follow_redirects=True` — vários IDPs reais redirecionam a descoberta
    (ex. `issuer` sem trailing slash canônico); sem isso a descoberta falha
    silenciosamente contra provedores legítimos (lição já documentada no
    plano, evitando redescobrir esse bug)."""
    import httpx

    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise OIDCError(
            f"falha ao descobrir configuração OIDC em {url!r}: {exc}"
        ) from exc
    except ValueError as exc:
        raise OIDCError(
            f"resposta de descoberta OIDC não é JSON válido: {exc}"
        ) from exc

    try:
        return OIDCDiscovery(
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            jwks_uri=data["jwks_uri"],
            userinfo_endpoint=data.get("userinfo_endpoint", ""),
        )
    except KeyError as exc:
        raise OIDCError(f"descoberta OIDC incompleta — campo ausente: {exc}") from exc


def build_authorization_url(
    discovery: OIDCDiscovery,
    config: OIDCConfig,
    *,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str = "openid email profile",
) -> str:
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{discovery.authorization_endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(
    discovery: OIDCDiscovery,
    config: OIDCConfig,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    import httpx

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code_verifier": code_verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                discovery.token_endpoint,
                data=payload,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise OIDCError(f"troca de código por token falhou: {exc}") from exc
    except ValueError as exc:
        raise OIDCError(f"resposta do token endpoint não é JSON válido: {exc}") from exc


def verify_id_token(
    discovery: OIDCDiscovery, config: OIDCConfig, id_token: str
) -> dict[str, Any]:
    """Verifica assinatura + claims padrão via JWKS. Levanta `OIDCError`
    tipado em qualquer falha (chave não encontrada, assinatura inválida,
    `aud`/`iss` incorretos, token expirado) — nunca deixa a exceção crua
    de `jwt`/`PyJWKClient` vazar pro handler."""
    import jwt

    try:
        jwk_client = jwt.PyJWKClient(discovery.jwks_uri)
        signing_key = jwk_client.get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=config.client_id,
        )
    except jwt.PyJWTError as exc:
        raise OIDCError(f"id_token inválido: {exc}") from exc


def start_login(
    discovery: OIDCDiscovery, config: OIDCConfig, *, redirect_uri: str
) -> str:
    """Gera PKCE + `state`, guarda o par pendente, devolve a URL de
    autorização pra redirecionar o usuário."""
    _cleanup_expired()
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    _pending_logins[state] = _PendingLogin(
        code_verifier=verifier, redirect_uri=redirect_uri
    )
    return build_authorization_url(
        discovery,
        config,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=challenge,
    )


async def complete_login(
    discovery: OIDCDiscovery, config: OIDCConfig, *, state: str, code: str
) -> dict[str, Any]:
    """Resolve o `state` pendente (rejeitando CSRF — `state` desconhecido ou
    já consumido), troca o código pelos tokens, verifica o `id_token` e
    devolve os claims verificados (`email`, `name`, `sub`, ...).

    O `state` é sempre removido do store, mesmo em falha — nunca reutilizável.
    """
    _cleanup_expired()
    pending = _pending_logins.pop(state, None)
    if pending is None:
        raise OIDCError("state inválido, expirado ou já usado — possível CSRF")

    tokens = await exchange_code_for_tokens(
        discovery,
        config,
        code=code,
        redirect_uri=pending.redirect_uri,
        code_verifier=pending.code_verifier,
    )
    id_token = tokens.get("id_token")
    if not id_token:
        raise OIDCError("resposta do IDP não trouxe id_token")

    return verify_id_token(discovery, config, id_token)
