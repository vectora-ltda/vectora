"""Handler de status, validação, conexão e portal de licença.

Endpoints:

- ``GET /license/status`` — público, lê cache local que o Launcher escreveu
  no boot. Consumido pelo trial banner do chat.
- ``POST /license/validate`` — força revalidação remota do token atual e
  devolve o status fresco. Usado pelo setup wizard após salvar token.
- ``POST /license/connect`` — root only. Login com a conta vectora.company
  (email + senha): ``services.vectora.company/license/agent-login`` autentica
  e devolve um VECTORA_TOKEN, que é persistido e validado — conecta tudo em
  um passo.
- ``POST /license/portal`` — autenticado, chama
  ``services.vectora.company/license/portal`` (Stripe Customer Portal para
  INTL ou Asaas dashboard para BR) e retorna a URL pra abrir externamente.

Roteamento BR vs INTL fica em services, por moeda da assinatura — o backend
só repassa o token.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.services.license import (
    LicenseError,
    clear_license_cache,
    read_cached_status,
    validate_license_async,
    write_token_to_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/license", tags=["license"])

# Estado efêmero de OAuth — states expiram em 5 min; limpeza via task assíncrona.
_oauth_states: dict[str, float] = {}  # state → expires_at (monotonic)
_OAUTH_TTL = 300.0  # segundos
_GATEWAY_URL = os.getenv("VECTORA_GATEWAY_URL", "https://gateway.vectora.chat")

DEFAULT_PORTAL_URL = "https://services.vectora.company/license/portal"
DEFAULT_CONNECT_URL = "https://services.vectora.company/license/agent-login"
HTTP_TIMEOUT = 10.0


def _require_root(request: Request) -> Any:
    """Lança 401/403 se o usuário da request não é root (espelha admin.py)."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    if getattr(user, "role", None) != "root":
        raise HTTPException(
            status_code=403,
            detail="Acesso negado. Apenas root pode configurar a licença.",
        )
    return user


@router.get("/status")
async def license_status() -> dict:
    """Devolve o status atual da licença (lido do cache local)."""
    info = read_cached_status()
    if info is None:
        return {
            "configured": False,
            "tier": None,
            "status": "unknown",
            "days_remaining": 0,
            "expires_at": "",
            "cached": False,
        }
    return {"configured": True, **info.to_dict()}


@router.post("/validate")
async def license_validate() -> dict:
    """Força revalidação remota do token atual (ignora cache fresco).

    Sempre responde 200; falha de licença vai em ``{valid: false, error}``
    para o wizard exibir inline sem tratar status HTTP.
    """
    if not os.getenv("VECTORA_TOKEN", "").strip():
        # _get_token() em license.py também lê do config.toml — deixa o
        # serviço decidir; aqui só damos resposta amigável se nada existir.
        from backend.services.license import load_token_from_config

        if not load_token_from_config():
            return {"valid": False, "configured": False, "error": "token_missing"}
    try:
        info = await validate_license_async(force=True)
    except LicenseError as exc:
        return {"valid": False, "configured": True, "error": str(exc)}
    except Exception as exc:  # network/5xx — não derruba o wizard
        logger.warning("license/validate: falha inesperada — %s", exc)
        return {"valid": False, "configured": True, "error": "Falha ao validar."}
    return {"valid": True, "configured": True, **info.to_dict()}


class ValidateTokenBody(BaseModel):
    token: str


@router.post("/validate-token")
async def license_validate_token(body: ValidateTokenBody) -> dict:
    """Valida um VECTORA_TOKEN ad-hoc, antes de existir qualquer conta.

    Usado pelo wizard de onboarding pra checar o token Pro no passo VPS
    ANTES do signup de verdade acontecer. Só permitido no primeiro acesso
    (instância ainda sem nenhum usuário) — mesma guarda de
    ``/auth/setup-local``. Se válido e ``tier == "pro"``, persiste (o
    ``/auth/signup`` seguinte já herda o token configurado); se inválido ou
    Free, não persiste em disco (só fica no ambiente do processo atual).
    """
    from backend.rbac.auth import has_users

    if await has_users():
        raise HTTPException(
            status_code=409,
            detail="Instância já configurada — validação avulsa só vale no primeiro acesso.",
        )

    token = body.token.strip()
    if not token:
        return {"valid": False, "error": "token_required"}

    os.environ["VECTORA_TOKEN"] = token
    clear_license_cache()
    try:
        info = await validate_license_async(force=True)
    except LicenseError as exc:
        return {"valid": False, "error": str(exc)}

    if info.tier != "pro":
        return {"valid": False, "error": "not_pro_tier", **info.to_dict()}

    write_token_to_config(token)
    return {"valid": True, **info.to_dict()}


class ConnectBody(BaseModel):
    """Credenciais da conta vectora.company para conectar a licença."""

    email: str
    password: str


def _connect_url() -> str:
    return os.getenv("VECTORA_LICENSE_CONNECT_URL", DEFAULT_CONNECT_URL).strip()


@router.post("/connect")
async def license_connect(body: ConnectBody, request: Request) -> dict:
    """Login com a conta vectora.company → obtém e ativa o VECTORA_TOKEN.

    Chama ``services.vectora.company/license/agent-login`` (email + senha).
    Em caso de sucesso ela devolve ``{token, tier, status}``; o token é
    persistido em ``config.toml [license]`` e validado imediatamente (popula
    o cache que o banner e o /license/status leem).
    """
    _require_root(request)
    email = body.email.strip()
    if "@" not in email or not body.password:
        raise HTTPException(status_code=422, detail="Credenciais incompletas.")

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                _connect_url(),
                json={"email": email, "password": body.password},
            )
    except httpx.HTTPError as exc:
        logger.warning("license/connect: falha de network — %s", exc)
        raise HTTPException(
            status_code=503,
            detail="vectora.company indisponível. Tente novamente em instantes.",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Email ou senha incorretos.")
    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="Conta sem token de licença. Acesse o dashboard para gerar um.",
        )
    if resp.status_code >= 400:
        logger.warning("license/connect: edge function respondeu %s", resp.status_code)
        raise HTTPException(status_code=502, detail="Erro no servidor de licenças.")

    data = resp.json()
    token = str(data.get("token", "")).strip()
    if not token:
        raise HTTPException(
            status_code=502, detail="Resposta inválida do servidor de licenças."
        )

    # Persiste e ativa o token novo; o cache antigo é descartado para a
    # validação refletir a conta recém-conectada.
    os.environ["VECTORA_TOKEN"] = token
    write_token_to_config(token)
    clear_license_cache()

    try:
        info = await validate_license_async(force=True)
    except LicenseError as exc:
        return {"connected": True, "valid": False, "error": str(exc)}
    return {"connected": True, "valid": True, **info.to_dict()}


def _oauth_secret() -> str:
    secret = os.getenv("VECTORA_OAUTH_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="OAuth não configurado. Defina VECTORA_OAUTH_SECRET.",
        )
    return secret


@router.post("/oauth/init")
async def license_oauth_init() -> dict:
    """Gera um state para o device flow OAuth com vectora.company.

    Retorna o state e a URL que o frontend deve abrir no browser.
    O state expira em 5 minutos.
    """
    _oauth_secret()  # falha cedo se não configurado
    state = str(uuid.uuid4())
    _oauth_states[state] = time.monotonic() + _OAUTH_TTL
    company_url = os.getenv("VECTORA_COMPANY_URL", "https://vectora.company")
    auth_url = f"{company_url}/auth/device?state={state}"
    return {"state": state, "auth_url": auth_url}


@router.get("/oauth/poll")
async def license_oauth_poll(state: str) -> dict:
    """Consulta o gateway para ver se o token OAuth chegou.

    O frontend faz polling a cada 2s. Quando o token chega:
    - salva-o em config + env
    - valida a licença
    - retorna ``{ok: true}``
    """
    if not state or state not in _oauth_states:
        raise HTTPException(status_code=400, detail="state inválido ou expirado.")
    if time.monotonic() > _oauth_states[state]:
        del _oauth_states[state]
        raise HTTPException(status_code=410, detail="state expirado.")

    secret = _oauth_secret()
    gateway_url = f"{_GATEWAY_URL}/oauth/token/{state}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                gateway_url,
                headers={"Authorization": f"Bearer {secret}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("license/oauth/poll: falha de network — %s", exc)
        return {"pending": True}

    if resp.status_code == 202:
        return {"pending": True}

    if resp.status_code != 200:
        logger.warning("license/oauth/poll: gateway respondeu %s", resp.status_code)
        return {"pending": True}

    data = resp.json()
    token = str(data.get("token", "")).strip()
    if not token:
        return {"pending": True}

    del _oauth_states[state]
    os.environ["VECTORA_TOKEN"] = token
    write_token_to_config(token)
    clear_license_cache()

    try:
        info = await validate_license_async(force=True)
    except LicenseError as exc:
        return {"ok": True, "valid": False, "error": str(exc)}
    return {"ok": True, "valid": True, **info.to_dict()}


def _portal_url() -> str:
    return os.getenv("VECTORA_LICENSE_PORTAL_URL", DEFAULT_PORTAL_URL).strip()


def _vectora_token() -> str:
    token = os.getenv("VECTORA_TOKEN", "").strip()
    if not token:
        raise LicenseError(
            "VECTORA_TOKEN não configurado — não há licença para gerenciar."
        )
    return token


@router.post("/portal")
async def license_portal() -> dict:
    """Cria sessão de Customer Portal (Stripe INTL ou Asaas BR).

    Repassa o ``VECTORA_TOKEN`` para ``services.vectora.company/license/portal``,
    que decide o provedor pela moeda da assinatura. Retorna ``{url: str}``
    para o desktop abrir via ``shell.openExternal()`` ou o web abrir em nova
    aba.
    """
    try:
        token = _vectora_token()
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = {"token": token}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(_portal_url(), json=payload)
    except httpx.HTTPError as exc:
        logger.warning("license/portal: falha de network — %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Servidor de billing indisponível. Tente novamente em instantes.",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Token inválido.")
    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="Sem assinatura ativa para gerenciar.",
        )
    if resp.status_code >= 500:
        raise HTTPException(
            status_code=502,
            detail="Erro no servidor de billing.",
        )
    if resp.status_code >= 400:
        import contextlib
        import json

        detail = "Erro ao criar sessão de portal."
        with contextlib.suppress(json.JSONDecodeError, AttributeError, TypeError):
            detail = resp.json().get("message", detail)
        raise HTTPException(status_code=resp.status_code, detail=detail)

    data = resp.json()
    url = data.get("url", "").strip()
    if not url:
        raise HTTPException(
            status_code=502,
            detail="Resposta inválida do servidor de billing.",
        )
    return {"url": url}
