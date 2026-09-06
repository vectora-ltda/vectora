"""Handler de OAuth e registry de integrações externas.

Endpoints de API Key:
    GET  /integrations                  — lista integrações com status (conectado/não)
    POST /integrations/{id}/verify      — testa se a API key configurada é válida

Endpoints de OAuth — GitHub (via GitHub App) + GitLab/Google/Slack (via OAuth App clássico):
    GET  /auth/{provider}               — inicia o fluxo de autorização
    GET  /auth/{provider}/callback      — callback, salva token
    GET  /auth/{provider}/status        — {connected: bool, ...}
    DELETE /auth/{provider}             — desconecta

GitHub usa GitHub App (não OAuth App clássico, deprecado pelo GitHub pra
integrações novas) — mesmos endpoints `login/oauth/authorize` e
`login/oauth/access_token`, então o código de troca `code → token` é
idêntico ao de um OAuth App. Só muda o cadastro: registre um GitHub App
em https://github.com/settings/apps/new com "Request user authorization
(OAuth) during installation" marcado, e desmarque a expiração de 8h do
token do usuário em "Optional features" (senão o token expira e precisa
de refresh, não implementado aqui) — GITHUB_OAUTH_CLIENT_ID/SECRET vêm
das credenciais desse GitHub App.

Os client IDs e secrets dos providers são mantidos pelo Worker services. O
backend local usa apenas VECTORA_OAUTH_BROKER_URL e VECTORA_OAUTH_SECRET para
iniciar o fluxo e consumir o resultado one-shot; as variáveis dos providers
abaixo só permanecem como fallback de compatibilidade para instalações antigas.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

_GATEWAY_TOKEN_PATH = settings.vectora_home / "gateway_token"
_OAUTH_BROKER_URL = os.getenv("VECTORA_OAUTH_BROKER_URL", "").strip()
_OAUTH_BROKER_SECRET = os.getenv("VECTORA_OAUTH_SECRET", "").strip()
_BROKER_STATE_TTL = 300.0


def _broker_enabled() -> bool:
    return bool(_OAUTH_BROKER_URL and _OAUTH_BROKER_SECRET)


async def _broker_providers() -> set[str]:
    if not _broker_enabled():
        return set()
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{_OAUTH_BROKER_URL}/oauth/integrations/providers"
            )
        if response.is_success:
            return {str(item) for item in response.json().get("providers", [])}
    except Exception as exc:
        logger.warning("OAuth broker provider discovery failed: %s", exc)
    return set()


async def _broker_start(request: Request, provider: str) -> RedirectResponse:
    user = _get_user(request)
    expires_at = int(time.time()) + int(_BROKER_STATE_TTL)
    # Keep the signed state below the gateway's compact-state limit while
    # retaining enough entropy for a short-lived, one-time callback.
    payload = f"{user.id}:{provider}:{expires_at}:{secrets.token_urlsafe(12)}"
    signature = hmac.new(
        _OAUTH_BROKER_SECRET.encode(), payload.encode(), hashlib.sha256
    ).digest()[:12]
    state = (
        base64.urlsafe_b64encode(
            f"{payload}:{base64.urlsafe_b64encode(signature).decode()}".encode()
        )
        .decode()
        .rstrip("=")
    )
    callback = _gateway_callback_url(provider)
    if not callback:
        raise HTTPException(
            status_code=503,
            detail="OAuth centralizado exige um gateway Vectora conectado",
        )
    import httpx

    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(
            f"{_OAUTH_BROKER_URL}/oauth/integrations/{provider}/start",
            params={"state": state, "return_to": callback},
        )
    if response.status_code != 302:
        detail = (
            "OAuth broker não configurado"
            if response.status_code == 503
            else "Falha ao iniciar OAuth"
        )
        raise HTTPException(status_code=response.status_code, detail=detail)
    location = response.headers.get("location")
    if not location:
        raise HTTPException(
            status_code=502, detail="Broker OAuth não retornou redirect"
        )
    return RedirectResponse(url=location, status_code=302)


async def _try_broker_start(request: Request, provider: str) -> RedirectResponse | None:
    """Use the central broker when advertised, falling back to local OAuth."""
    if provider not in await _broker_providers():
        return None
    try:
        return await _broker_start(request, provider)
    except HTTPException as exc:
        if exc.status_code not in (502, 503):
            raise
        logger.warning("OAuth broker unavailable for %s: %s", provider, exc.detail)
    except Exception as exc:
        logger.warning("OAuth broker transport failed for %s: %s", provider, exc)
    return None


async def _broker_callback(
    provider: str, state: str, request: Request
) -> RedirectResponse:
    try:
        padded = state + "=" * (-len(state) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        user_id, state_provider, expires_at, nonce, encoded_signature = decoded.split(
            ":", 4
        )
        signed_payload = f"{user_id}:{state_provider}:{expires_at}:{nonce}"
        expected = hmac.new(
            _OAUTH_BROKER_SECRET.encode(), signed_payload.encode(), hashlib.sha256
        ).digest()[:12]
        actual = base64.urlsafe_b64decode(encoded_signature + "=")
        expires_epoch = int(expires_at)
        valid = hmac.compare_digest(actual, expected)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        valid = False
        user_id = ""
        state_provider = ""
        expires_epoch = 0
    if not valid or state_provider != provider or expires_epoch < int(time.time()):
        raise HTTPException(status_code=400, detail="Estado OAuth expirado ou inválido")
    import httpx

    response = None
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(5):
            response = await client.get(
                f"{_OAUTH_BROKER_URL}/oauth/integrations/{provider}/result/{state}",
                headers={"Authorization": f"Bearer {_OAUTH_BROKER_SECRET}"},
            )
            if response.status_code != 202:
                break
            await asyncio.sleep(0.2 * (attempt + 1))
    if response is None or response.status_code == 202:
        raise HTTPException(
            status_code=502, detail="Resultado OAuth ainda não disponível"
        )
    if not response.is_success:
        raise HTTPException(
            status_code=502, detail="Broker OAuth falhou ao recuperar o token"
        )
    payload = response.json()
    access_token = payload.get("accessToken")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(status_code=502, detail="Broker OAuth não retornou token")
    env_var = _REGISTRY_BY_ID[provider]["env_var"]
    from backend.rbac import auth as auth_svc

    await auth_svc.set_env_override(user_id, env_var, access_token, source="oauth")
    refresh_token = payload.get("refreshToken")
    if provider == "google" and isinstance(refresh_token, str) and refresh_token:
        await auth_svc.set_env_override(
            user_id, "GOOGLE_REFRESH_TOKEN", refresh_token, source="oauth"
        )
    logger.info(
        "OAuth broker: token salvo para provider=%s user_id=%s", provider, user_id
    )
    return RedirectResponse(url=f"/?oauth_success={provider}", status_code=302)


def _looks_like_broker_state(state: str, provider: str) -> bool:
    try:
        padded = state + "=" * (-len(state) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        parts = decoded.split(":", 4)
        return len(parts) == 5 and parts[1] == provider
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return False


def _gateway_callback_url(
    provider: str,
    token_path: Path | None = None,
) -> str | None:
    """Retorna URL de callback via gateway se token disponível, ou None."""
    path = token_path if token_path is not None else _GATEWAY_TOKEN_PATH
    try:
        token = path.read_text().strip()
        if token:
            return f"https://{token}.vectora.chat/auth/{provider}/callback"
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Registry de integrações
# ---------------------------------------------------------------------------

#: Todas as integrações suportadas — consumido pela UI e pelo endpoint /integrations.
#: kind="apikey" → apenas chave inserida manualmente pelo usuário.
#: kind="oauth"  → fluxo OAuth delegado; o token é salvo como env_override.
INTEGRATIONS_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "gemini",
        "name": "Google Gemini",
        "env_var": "GOOGLE_API_KEY",
        "kind": "apikey",
        "description": "Gemini 2.5 (Pro/Flash) — provider padrão do Vectora",
        "docs_url": "https://aistudio.google.com/app/apikey",
        "icon": "gemini",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "env_var": "OPENAI_API_KEY",
        "kind": "apikey",
        "description": "GPT-4.x, o3/o4-mini e embeddings",
        "docs_url": "https://platform.openai.com/api-keys",
        "icon": "openai",
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "kind": "apikey",
        "description": "Claude 4.x (Opus, Sonnet, Haiku)",
        "docs_url": "https://console.anthropic.com/settings/keys",
        "icon": "anthropic",
    },
    {
        "id": "cohere",
        "name": "Cohere",
        "env_var": "COHERE_API_KEY",
        "kind": "apikey",
        "description": "Command R+ (chat), embeddings e reranker semântico",
        "docs_url": "https://dashboard.cohere.com/api-keys",
        "icon": "cohere",
    },
    {
        "id": "tavily",
        "name": "Tavily",
        "env_var": "TAVILY_API_KEY",
        "kind": "apikey",
        "description": "Busca web com contexto para LLMs",
        "docs_url": "https://app.tavily.com/",
        "icon": "tavily",
    },
    {
        "id": "github",
        "name": "GitHub",
        "env_var": "GITHUB_TOKEN",
        # híbrido: aceita OAuth (delegado, via GitHub App — ver docstring do
        # módulo) OU um Personal Access Token colado manualmente. Ambos
        # gravam GITHUB_TOKEN nos env_overrides do user — o `gh` CLI e as
        # git tools leem essa env. Permite quem não quer registrar um
        # GitHub App usar só um PAT.
        "kind": "hybrid",
        "description": "Acesso a repositórios, PRs e issues (OAuth ou token)",
        "docs_url": "https://github.com/settings/tokens",
        "icon": "github",
        "oauth_scopes": ["repo", "user:email", "read:org"],
        # GITHUB_PERSONAL_ACCESS_TOKEN é a convenção do servidor MCP oficial
        # do GitHub (marketplace) — reconhecida aqui como alias pra "conectado"
        # não depender de saber qual dos dois nomes o usuário configurou.
        "env_var_aliases": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    },
    {
        "id": "gitlab",
        "name": "GitLab",
        "env_var": "GITLAB_TOKEN",
        "kind": "oauth",
        "description": "Repositórios, merge requests e pipelines do GitLab",
        "docs_url": "https://gitlab.com/-/profile/applications",
        "icon": "gitlab",
        "oauth_scopes": ["api", "read_repository", "write_repository", "read_user"],
    },
    {
        "id": "google",
        "name": "Google",
        "env_var": "GOOGLE_ACCESS_TOKEN",
        "kind": "oauth",
        "description": "Conta Google (base para Drive e Gmail)",
        "docs_url": "https://console.cloud.google.com/apis/credentials",
        "icon": "google",
        "oauth_scopes": ["openid", "email", "profile"],
    },
    {
        "id": "google-drive",
        "name": "Google Drive",
        "env_var": "GOOGLE_ACCESS_TOKEN",
        "kind": "oauth",
        "description": "Leitura e busca de arquivos no Google Drive",
        "docs_url": "https://console.cloud.google.com/apis/credentials",
        "icon": "google-drive",
        "parent": "google",
    },
    {
        "id": "gmail",
        "name": "Gmail",
        "env_var": "GOOGLE_ACCESS_TOKEN",
        "kind": "oauth",
        "description": "Leitura de emails recebidos no Gmail",
        "docs_url": "https://console.cloud.google.com/apis/credentials",
        "icon": "gmail",
        "parent": "google",
    },
    {
        # Socket Mode, não OAuth: a conexão WebSocket sai do processo pro
        # Slack, então não há endpoint público pra receber o callback nem
        # evento. Precisa dos dois tokens — o `xoxb-` autentica as chamadas
        # de API, o `xapp-` abre o socket.
        "id": "slack",
        "name": "Slack",
        "env_var": "SLACK_BOT_TOKEN",
        "kind": "apikey",
        "description": "Converse com o Vectora pelo Slack (Socket Mode)",
        "docs_url": "https://api.slack.com/apps",
        "icon": "slack",
        "setup_hint": "Crie um Slack App, habilite Socket Mode e gere os dois tokens: o de bot (xoxb-, em OAuth & Permissions) e o de app (xapp-, em Basic Information -> App-Level Tokens).",
        "extra_vars": ["SLACK_APP_TOKEN"],
    },
    {
        "id": "telegram",
        "name": "Telegram",
        "env_var": "TELEGRAM_BOT_TOKEN",
        "kind": "apikey",
        "description": "Converse com o Vectora pelo Telegram (bot próprio via @BotFather)",
        "docs_url": "https://core.telegram.org/bots/features#botfather",
        "icon": "telegram",
        "setup_hint": "No Telegram, fale com @BotFather, mande /newbot e cole aqui o token que ele devolver.",
    },
    {
        "id": "discord",
        "name": "Discord",
        "env_var": "DISCORD_BOT_TOKEN",
        "kind": "apikey",
        "description": "Converse com o Vectora pelo Discord (Application própria)",
        "docs_url": "https://discord.com/developers/applications",
        "icon": "discord",
        "setup_hint": "No Developer Portal: crie uma Application, aba Bot -> Reset Token pra copiar, e ligue o Message Content Intent (sem ele o bot recebe mensagens vazias). Depois convide o bot pro seu servidor.",
    },
    {
        # Caixa do próprio usuário (IMAP pra ler, SMTP pra responder) — sem
        # provider transacional no meio. `EMAIL_SMTP_HOST` é opcional: sem
        # ele o envio usa o mesmo host do IMAP.
        "id": "email-connect",
        "name": "Email",
        "env_var": "EMAIL_IMAP_PASSWORD",
        "kind": "apikey",
        "description": "Converse com o Vectora por email (IMAP + SMTP da sua caixa)",
        "docs_url": "https://support.google.com/accounts/answer/185833",
        "icon": "mail",
        "setup_hint": "Use uma senha de app do seu provedor (no Gmail exige 2FA ligada), nunca a senha da conta. Preencha host IMAP, usuário e senha; o host SMTP é opcional e assume o mesmo do IMAP.",
        "extra_vars": ["EMAIL_IMAP_HOST", "EMAIL_IMAP_USER", "EMAIL_SMTP_HOST"],
    },
    {
        # Self-hosted falando com self-hosted: a instância e o token são do
        # próprio usuário. Sem `HOME_ASSISTANT_URL` a integração nasceria
        # inutilizável — não há host padrão que dê pra assumir.
        "id": "home-assistant",
        "name": "Home Assistant",
        "env_var": "HOME_ASSISTANT_TOKEN",
        "kind": "apikey",
        "description": "Controle a casa (luzes, fechaduras, sensores) pelo chat",
        "docs_url": "https://www.home-assistant.io/docs/authentication/#your-account-profile",
        "icon": "home",
        "setup_hint": "No Home Assistant, abra seu perfil -> Security -> Long-Lived Access Tokens e crie um. Preencha também a URL da instância (ex.: http://homeassistant.local:8123).",
        "extra_vars": ["HOME_ASSISTANT_URL"],
    },
    {
        "id": "linear",
        "name": "Linear",
        "env_var": "LINEAR_API_KEY",
        "kind": "apikey",
        "description": "Issues e projetos do Linear",
        "docs_url": "https://linear.app/settings/api",
        "icon": "linear",
    },
    {
        "id": "jira",
        "name": "Jira",
        "env_var": "JIRA_API_TOKEN",
        "kind": "apikey",
        "description": "Issues e sprints do Jira (Atlassian)",
        "docs_url": "https://id.atlassian.com/manage-profile/security/api-tokens",
        "icon": "jira",
        "extra_vars": ["JIRA_BASE_URL", "JIRA_EMAIL"],
    },
    {
        "id": "notion",
        "name": "Notion",
        "env_var": "NOTION_API_KEY",
        "kind": "apikey",
        "description": "Páginas e databases do Notion",
        "docs_url": "https://www.notion.so/my-integrations",
        "icon": "notion",
    },
    {
        "id": "resend",
        "name": "Resend",
        "env_var": "RESEND_API_KEY",
        "kind": "apikey",
        "description": "Envio de emails transacionais via Resend",
        "docs_url": "https://resend.com/api-keys",
        "icon": "resend",
    },
    {
        "id": "sendgrid",
        "name": "SendGrid",
        "env_var": "SENDGRID_API_KEY",
        "kind": "apikey",
        "description": "Envio de emails e campanhas via SendGrid (Twilio)",
        "docs_url": "https://app.sendgrid.com/settings/api_keys",
        "icon": "sendgrid",
    },
    {
        "id": "mailgun",
        "name": "Mailgun",
        "env_var": "MAILGUN_API_KEY",
        "kind": "apikey",
        "description": "Envio e recebimento de emails via Mailgun",
        "docs_url": "https://app.mailgun.com/settings/api-security",
        "icon": "mailgun",
    },
]

# Índice rápido por id
_REGISTRY_BY_ID: dict[str, dict[str, Any]] = {i["id"]: i for i in INTEGRATIONS_REGISTRY}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_user(request: Request) -> Any:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


def _oauth_configured(provider_id: str) -> bool:
    """True quando o operador desta instância registrou o app próprio no
    provider (GitHub App pro GitHub, OAuth App clássico pros demais — ver
    docstring do módulo) e setou CLIENT_ID + CLIENT_SECRET — sem isso, o
    fluxo `/auth/{provider}` sempre falha com 503 (ver
    `_github_cfg`/`_gitlab_cfg`/etc.). A UI usa isto pra só oferecer
    "Conectar via OAuth" quando o botão de fato funciona, caindo pro token
    manual (sempre disponível) caso contrário."""
    prefix = provider_id.upper().replace("-", "_")
    return bool(
        os.environ.get(f"{prefix}_OAUTH_CLIENT_ID")
        and os.environ.get(f"{prefix}_OAUTH_CLIENT_SECRET")
    )


def _github_cfg() -> tuple[str, str, str]:
    """Lê configuração do GitHub App nas env vars (client_id/secret do App
    registrado em https://github.com/settings/apps, não de um OAuth App
    clássico — ver docstring do módulo).

    Retorna (client_id, client_secret, redirect_uri).
    Levanta HTTPException 503 se não configurado.
    """
    client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "")
    redirect_uri = (
        os.environ.get("GITHUB_OAUTH_REDIRECT_URI")
        or _gateway_callback_url("github")
        or "http://localhost:8080/auth/github/callback"
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "GitHub App não configurado. "
                "Defina GITHUB_OAUTH_CLIENT_ID e GITHUB_OAUTH_CLIENT_SECRET."
            ),
        )
    return client_id, client_secret, redirect_uri


# ---------------------------------------------------------------------------
# Endpoints de integrações (listagem + status + verificação)
# ---------------------------------------------------------------------------


@router.get("/integrations")
async def list_integrations(request: Request) -> dict:
    """Lista todas as integrações com status de conexão do usuário atual."""
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        overrides = await auth_svc.get_env_overrides(user.id)
    except Exception:
        overrides = {}

    from backend.rbac.auth import is_oauth_sourced

    broker_providers = await _broker_providers()
    items = []
    for integ in INTEGRATIONS_REGISTRY:
        env_vars = [integ["env_var"], *integ.get("env_var_aliases", [])]
        # Considera conectado se há override do usuário OU env global setada,
        # em qualquer um dos nomes aceitos (env_var principal ou alias).
        connected = any(overrides.get(v) or os.environ.get(v) for v in env_vars)
        # Diferente de `connected`: só true se o valor setado veio do fluxo
        # OAuth (callback em oauth.py), não de um token colado manualmente
        # num provider `hybrid` como GitHub — a UI usa isto pra decidir se
        # mostra as ações OAuth (desconectar via /auth/<provider>) em vez do
        # badge/remoção manual genéricos.
        oauth_connected = any(
            overrides.get(v) and is_oauth_sourced(overrides, v) for v in env_vars
        )
        oauth_provider_id = integ.get("parent", integ["id"])
        items.append(
            {
                **integ,
                "connected": connected,
                "oauth_connected": oauth_connected,
                # Nunca expõe o valor — apenas informa se existe
                "oauth_configured": (
                    oauth_provider_id in broker_providers
                    or _oauth_configured(oauth_provider_id)
                    if integ["kind"] in ("oauth", "hybrid")
                    else False
                ),
            }
        )
    return {"integrations": items}


@router.post("/integrations/{integration_id}/verify")
async def verify_integration(request: Request, integration_id: str) -> dict:
    """Testa se a chave/token da integração está funcional."""
    _get_user(request)
    integ = _REGISTRY_BY_ID.get(integration_id)
    if integ is None:
        raise HTTPException(
            status_code=404, detail=f"Integração '{integration_id}' desconhecida"
        )

    # Lê do env efetivo (system + user overrides já mesclados pelo middleware),
    # tentando o env_var principal e, se ausente, os aliases aceitos. Não é
    # senha hardcoded (B105 falso-positivo) — é o valor inicial antes do loop.
    token = ""  # nosec B105
    for v in [integ["env_var"], *integ.get("env_var_aliases", [])]:
        token = os.environ.get(v, "")
        if token:
            break
    if not token:
        return {"ok": False, "message": "Chave não configurada"}

    try:
        ok, message = await _verify_apikey(integration_id, token)
        return {"ok": ok, "message": message}
    except Exception as exc:
        logger.warning("verify_integration(%s) error: %s", integration_id, exc)
        return {"ok": False, "message": str(exc)}


async def _verify_apikey(integration_id: str, token: str) -> tuple[bool, str]:  # noqa: PLR0911
    """Faz uma chamada mínima para validar a chave de cada provedor."""
    import httpx

    async with httpx.AsyncClient(timeout=8) as client:
        if integration_id == "openai":
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                return True, "Chave válida"
            return False, f"OpenAI retornou {r.status_code}"

        if integration_id == "anthropic":
            r = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": token, "anthropic-version": "2023-06-01"},
            )
            if r.status_code == 200:
                return True, "Chave válida"
            return False, f"Anthropic retornou {r.status_code}"

        if integration_id == "cohere":
            # GET /v2/models lista modelos disponíveis — endpoint estável e
            # leve, sem custo; o antigo /v1/tokenize exigia um modelo válido
            # no body e retornava erro mesmo com chave correta se o modelo
            # tivesse sido descontinuado.
            r = await client.get(
                "https://api.cohere.com/v2/models",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                return True, "Chave válida"
            try:
                err = r.json().get("message", "")
            except Exception:
                err = ""
            return False, err or f"Cohere retornou {r.status_code}"

        if integration_id == "tavily":
            r = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": token, "query": "test", "max_results": 1},
            )
            if r.status_code in {200, 422}:
                return True, "Chave válida"
            return False, f"Tavily retornou {r.status_code}"

        if integration_id == "github":
            r = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if r.status_code == 200:
                username = r.json().get("login", "")
                return True, f"Conectado como @{username}"
            return False, f"GitHub retornou {r.status_code}"

        if integration_id == "gitlab":
            base = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com")
            r = await client.get(
                f"{base}/api/v4/user",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                username = r.json().get("username", "")
                return True, f"Conectado como @{username}"
            return False, f"GitLab retornou {r.status_code}"

        if integration_id == "google":
            r = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                email = r.json().get("email", "")
                return True, f"Conectado como {email}"
            return False, f"Google retornou {r.status_code}"

        if integration_id == "gemini":
            # Gemini usa a Google AI Studio API key — validada listando modelos.
            r = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": token},
            )
            if r.status_code == 200:
                return True, "Chave válida"
            try:
                err = r.json().get("error", {}).get("message", "")
            except Exception:
                err = ""
            return False, err or f"Gemini retornou {r.status_code}"

        if integration_id in ("google-drive", "gmail"):
            r = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                return True, "Token Google válido"
            return False, f"Google retornou {r.status_code}"

        if integration_id == "slack":
            r = await client.get(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = r.json()
            if data.get("ok"):
                return True, f"Conectado como @{data.get('user', '')}"
            return False, data.get("error", "Token Slack inválido")

        if integration_id == "linear":
            r = await client.post(
                "https://api.linear.app/graphql",
                json={"query": "{ viewer { id name } }"},
                headers={"Authorization": token, "Content-Type": "application/json"},
            )
            if r.status_code == 200 and "data" in r.json():
                name = r.json()["data"]["viewer"]["name"]
                return True, f"Conectado como {name}"
            return False, f"Linear retornou {r.status_code}"

        if integration_id == "notion":
            r = await client.get(
                "https://api.notion.com/v1/users/me",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                },
            )
            if r.status_code == 200:
                name = r.json().get("name", "")
                return True, f"Conectado como {name}"
            return False, f"Notion retornou {r.status_code}"

    # Provedores sem verificação automática
    return True, "Chave salva (verificação não disponível para este provedor)"


# ---------------------------------------------------------------------------
# GitHub OAuth
# ---------------------------------------------------------------------------


@router.get("/auth/github")
async def github_oauth_start(request: Request) -> RedirectResponse:
    """Inicia o fluxo OAuth do GitHub — redireciona para github.com/login/oauth."""
    broker_redirect = await _try_broker_start(request, "github")
    if broker_redirect is not None:
        return broker_redirect
    user = _get_user(request)
    client_id, _secret, redirect_uri = _github_cfg()

    scopes = ",".join(
        _REGISTRY_BY_ID["github"].get("oauth_scopes", ["repo", "user:email"])
    )
    # state = user.id garante que só o user que iniciou pode finalizar o callback
    state = user.id

    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&state={state}"
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/github/callback")
async def github_oauth_callback(
    request: Request, code: str = "", state: str = ""
) -> RedirectResponse:
    """Callback do GitHub — troca code por token e salva como env_override."""
    if _broker_enabled() and _looks_like_broker_state(state, "github"):
        return await _broker_callback("github", state, request)
    if not code:
        raise HTTPException(status_code=400, detail="Parâmetro 'code' ausente")

    client_id, client_secret, redirect_uri = _github_cfg()

    # Troca o code pelo access_token
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )

    if r.status_code != 200:
        logger.error("GitHub token exchange failed: %s", r.text)
        return RedirectResponse(
            url="/?oauth_error=github_exchange_failed", status_code=302
        )

    data = r.json()
    access_token = data.get("access_token", "")
    if not access_token:
        logger.error("GitHub callback: token ausente na resposta: %s", data)
        return RedirectResponse(url="/?oauth_error=github_no_token", status_code=302)

    # Salva como env_override do usuário identificado pelo state
    user_id = state
    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.set_env_override(
            user_id, "GITHUB_TOKEN", access_token, source="oauth"
        )
        logger.info("GitHub OAuth: token salvo para user_id=%s", user_id)
    except Exception as exc:
        logger.exception("GitHub OAuth: falha ao salvar token: %s", exc)
        return RedirectResponse(url="/?oauth_error=github_save_failed", status_code=302)

    return RedirectResponse(url="/?oauth_success=github", status_code=302)


@router.get("/auth/github/status")
async def github_oauth_status(request: Request) -> dict:
    """Retorna se o GitHub está conectado e o username associado."""
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        overrides = await auth_svc.get_env_overrides(user.id)
        token = overrides.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    except Exception:
        token = os.environ.get("GITHUB_TOKEN", "")

    if not token:
        return {"connected": False, "username": None}

    # Resolve username via API do GitHub
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if r.status_code == 200:
            return {"connected": True, "username": r.json().get("login")}
    except Exception:
        pass

    return {"connected": True, "username": None}


@router.delete("/auth/github")
async def github_oauth_disconnect(request: Request) -> dict:
    """Remove o GITHUB_TOKEN dos env_overrides do usuário."""
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.delete_env_override(user.id, "GITHUB_TOKEN")
        logger.info("GitHub OAuth: token removido para user_id=%s", user.id)
    except Exception as exc:
        logger.warning("GitHub OAuth disconnect error: %s", exc)
    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# GitLab OAuth
# ---------------------------------------------------------------------------


def _gitlab_cfg() -> tuple[str, str, str, str]:
    client_id = os.environ.get("GITLAB_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GITLAB_OAUTH_CLIENT_SECRET", "")
    base_url = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com")
    redirect_uri = (
        os.environ.get("GITLAB_OAUTH_REDIRECT_URI")
        or _gateway_callback_url("gitlab")
        or "http://localhost:8080/auth/gitlab/callback"
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="GitLab OAuth não configurado. Defina GITLAB_OAUTH_CLIENT_ID e GITLAB_OAUTH_CLIENT_SECRET.",
        )
    return client_id, client_secret, base_url, redirect_uri


@router.get("/auth/gitlab")
async def gitlab_oauth_start(request: Request) -> RedirectResponse:
    broker_redirect = await _try_broker_start(request, "gitlab")
    if broker_redirect is not None:
        return broker_redirect
    user = _get_user(request)
    client_id, _secret, base_url, redirect_uri = _gitlab_cfg()
    scopes = " ".join(
        _REGISTRY_BY_ID["gitlab"].get("oauth_scopes", ["api", "read_user"])
    )
    url = (
        f"{base_url}/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={scopes}"
        f"&state={user.id}"
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/gitlab/callback")
async def gitlab_oauth_callback(
    request: Request, code: str = "", state: str = ""
) -> RedirectResponse:
    if _broker_enabled() and _looks_like_broker_state(state, "gitlab"):
        return await _broker_callback("gitlab", state, request)
    if not code:
        raise HTTPException(status_code=400, detail="Parâmetro 'code' ausente")
    client_id, client_secret, base_url, redirect_uri = _gitlab_cfg()

    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{base_url}/oauth/token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )

    if r.status_code != 200:
        logger.error("GitLab token exchange failed: %s", r.text)
        return RedirectResponse(
            url="/?oauth_error=gitlab_exchange_failed", status_code=302
        )

    access_token = r.json().get("access_token", "")
    if not access_token:
        return RedirectResponse(url="/?oauth_error=gitlab_no_token", status_code=302)

    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.set_env_override(
            state, "GITLAB_TOKEN", access_token, source="oauth"
        )
        logger.info("GitLab OAuth: token salvo para user_id=%s", state)
    except Exception as exc:
        logger.exception("GitLab OAuth: falha ao salvar token: %s", exc)
        return RedirectResponse(url="/?oauth_error=gitlab_save_failed", status_code=302)

    return RedirectResponse(url="/?oauth_success=gitlab", status_code=302)


@router.get("/auth/gitlab/status")
async def gitlab_oauth_status(request: Request) -> dict:
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        overrides = await auth_svc.get_env_overrides(user.id)
        token = overrides.get("GITLAB_TOKEN") or os.environ.get("GITLAB_TOKEN", "")
    except Exception:
        token = os.environ.get("GITLAB_TOKEN", "")

    if not token:
        return {"connected": False, "username": None}

    try:
        import httpx

        base_url = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com")
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{base_url}/api/v4/user",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200:
            return {"connected": True, "username": r.json().get("username")}
    except Exception:
        pass

    return {"connected": True, "username": None}


@router.delete("/auth/gitlab")
async def gitlab_oauth_disconnect(request: Request) -> dict:
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.delete_env_override(user.id, "GITLAB_TOKEN")
    except Exception as exc:
        logger.warning("GitLab disconnect error: %s", exc)
    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# Google OAuth (Drive + Gmail)
# ---------------------------------------------------------------------------


def _google_cfg() -> tuple[str, str, str]:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    redirect_uri = (
        os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
        or _gateway_callback_url("google")
        or "http://localhost:8080/auth/google/callback"
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth não configurado. Defina GOOGLE_OAUTH_CLIENT_ID e GOOGLE_OAUTH_CLIENT_SECRET.",
        )
    return client_id, client_secret, redirect_uri


@router.get("/auth/google")
async def google_oauth_start(request: Request) -> RedirectResponse:
    broker_redirect = await _try_broker_start(request, "google")
    if broker_redirect is not None:
        return broker_redirect
    user = _get_user(request)
    client_id, _secret, redirect_uri = _google_cfg()
    scopes = (
        "openid email profile "
        "https://www.googleapis.com/auth/drive.readonly "
        "https://www.googleapis.com/auth/gmail.readonly"
    )
    import urllib.parse

    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        "&response_type=code"
        f"&scope={urllib.parse.quote(scopes)}"
        "&access_type=offline"
        "&prompt=consent"
        f"&state={user.id}"
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request, code: str = "", state: str = ""
) -> RedirectResponse:
    if _broker_enabled() and _looks_like_broker_state(state, "google"):
        return await _broker_callback("google", state, request)
    if not code:
        raise HTTPException(status_code=400, detail="Parâmetro 'code' ausente")
    client_id, client_secret, redirect_uri = _google_cfg()

    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )

    if r.status_code != 200:
        logger.error("Google token exchange failed: %s", r.text)
        return RedirectResponse(
            url="/?oauth_error=google_exchange_failed", status_code=302
        )

    data = r.json()
    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    if not access_token:
        return RedirectResponse(url="/?oauth_error=google_no_token", status_code=302)

    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.set_env_override(
            state, "GOOGLE_ACCESS_TOKEN", access_token, source="oauth"
        )
        if refresh_token:
            await auth_svc.set_env_override(
                state, "GOOGLE_REFRESH_TOKEN", refresh_token, source="oauth"
            )
        logger.info("Google OAuth: token salvo para user_id=%s", state)
    except Exception as exc:
        logger.exception("Google OAuth: falha ao salvar token: %s", exc)
        return RedirectResponse(url="/?oauth_error=google_save_failed", status_code=302)

    return RedirectResponse(url="/?oauth_success=google", status_code=302)


@router.get("/auth/google/status")
async def google_oauth_status(request: Request) -> dict:
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        overrides = await auth_svc.get_env_overrides(user.id)
        token = overrides.get("GOOGLE_ACCESS_TOKEN") or os.environ.get(
            "GOOGLE_ACCESS_TOKEN", ""
        )
    except Exception:
        token = os.environ.get("GOOGLE_ACCESS_TOKEN", "")

    if not token:
        return {"connected": False, "email": None}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200:
            info = r.json()
            return {
                "connected": True,
                "email": info.get("email"),
                "name": info.get("name"),
            }
    except Exception:
        pass

    return {"connected": True, "email": None}


@router.delete("/auth/google")
async def google_oauth_disconnect(request: Request) -> dict:
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.delete_env_override(user.id, "GOOGLE_ACCESS_TOKEN")
        await auth_svc.delete_env_override(user.id, "GOOGLE_REFRESH_TOKEN")
    except Exception as exc:
        logger.warning("Google disconnect error: %s", exc)
    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# Slack OAuth
# ---------------------------------------------------------------------------


def _slack_cfg() -> tuple[str, str, str]:
    client_id = os.environ.get("SLACK_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("SLACK_OAUTH_CLIENT_SECRET", "")
    redirect_uri = (
        os.environ.get("SLACK_REDIRECT_URI")
        or _gateway_callback_url("slack")
        or "http://localhost:8080/auth/slack/callback"
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Slack OAuth não configurado. Defina SLACK_OAUTH_CLIENT_ID e SLACK_OAUTH_CLIENT_SECRET.",
        )
    return client_id, client_secret, redirect_uri


@router.get("/auth/slack")
async def slack_oauth_start(request: Request) -> RedirectResponse:
    user = _get_user(request)
    client_id, _secret, redirect_uri = _slack_cfg()
    scopes = ",".join(
        _REGISTRY_BY_ID["slack"].get("oauth_scopes", ["chat:write", "channels:read"])
    )
    import urllib.parse

    url = (
        "https://slack.com/oauth/v2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&scope={scopes}"
        f"&state={user.id}"
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/slack/callback")
async def slack_oauth_callback(
    request: Request, code: str = "", state: str = ""
) -> RedirectResponse:
    if _broker_enabled() and _looks_like_broker_state(state, "slack"):
        return await _broker_callback("slack", state, request)
    if not code:
        raise HTTPException(status_code=400, detail="Parâmetro 'code' ausente")
    client_id, client_secret, redirect_uri = _slack_cfg()

    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )

    data = r.json()
    if not data.get("ok"):
        logger.error("Slack OAuth failed: %s", data)
        return RedirectResponse(
            url="/?oauth_error=slack_exchange_failed", status_code=302
        )

    bot_token = data.get("access_token", "")
    if not bot_token:
        return RedirectResponse(url="/?oauth_error=slack_no_token", status_code=302)

    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.set_env_override(
            state, "SLACK_BOT_TOKEN", bot_token, source="oauth"
        )
        logger.info("Slack OAuth: token salvo para user_id=%s", state)
    except Exception as exc:
        logger.exception("Slack OAuth: falha ao salvar token: %s", exc)
        return RedirectResponse(url="/?oauth_error=slack_save_failed", status_code=302)

    return RedirectResponse(url="/?oauth_success=slack", status_code=302)


@router.get("/auth/slack/status")
async def slack_oauth_status(request: Request) -> dict:
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        overrides = await auth_svc.get_env_overrides(user.id)
        token = overrides.get("SLACK_BOT_TOKEN") or os.environ.get(
            "SLACK_BOT_TOKEN", ""
        )
    except Exception:
        token = os.environ.get("SLACK_BOT_TOKEN", "")

    if not token:
        return {"connected": False, "team": None}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"},
            )
        data = r.json()
        if data.get("ok"):
            return {
                "connected": True,
                "team": data.get("team"),
                "user": data.get("user"),
            }
    except Exception:
        pass

    return {"connected": True, "team": None}


@router.delete("/auth/slack")
async def slack_oauth_disconnect(request: Request) -> dict:
    user = _get_user(request)
    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.delete_env_override(user.id, "SLACK_BOT_TOKEN")
    except Exception as exc:
        logger.warning("Slack disconnect error: %s", exc)
    return {"status": "disconnected"}
