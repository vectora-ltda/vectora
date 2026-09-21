"""SSO/OIDC — login via provedor de identidade externo (`backend.rbac.oidc`).

Endpoints:
    GET /auth/oidc/status    — se um IDP está configurado (frontend decide
                                se mostra o botão "Entrar com SSO")
    GET /auth/oidc/login     — inicia o handshake, redireciona pro IDP
    GET /auth/oidc/callback  — recebe `code`+`state` de volta do IDP,
                                completa o login, grava cookies, redireciona
                                pro app

Rotas sob `/auth/*` — já públicas via `_PUBLIC_PREFIXES`
(`backend/api/middleware/auth.py`), mesmo tratamento de `/auth/signin`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

if TYPE_CHECKING:
    from backend.rbac.oidc import OIDCConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["auth"])

_CALLBACK_PATH = "/auth/oidc/callback"


def _load_config() -> OIDCConfig | None:
    from backend.config.registry import get_field

    client_id_field = get_field("oidc_client_id")
    client_secret_field = get_field("oidc_client_secret")
    issuer_field = get_field("oidc_issuer_url")
    if client_id_field is None or client_secret_field is None or issuer_field is None:
        return None

    client_id = str(client_id_field.get() or "")
    client_secret = str(client_secret_field.get() or "")
    issuer_url = str(issuer_field.get() or "")
    if not client_id or not client_secret or not issuer_url:
        return None

    from backend.rbac.oidc import OIDCConfig

    return OIDCConfig(
        client_id=client_id, client_secret=client_secret, issuer_url=issuer_url
    )


def _redirect_uri(request: Request) -> str:
    return str(request.base_url).rstrip("/") + _CALLBACK_PATH


@router.get("/status")
async def oidc_status() -> dict:
    return {"enabled": _load_config() is not None}


@router.get("/login")
async def oidc_login(request: Request) -> RedirectResponse:
    from backend.rbac import oidc as oidc_svc

    config = _load_config()
    if config is None:
        raise HTTPException(status_code=404, detail="SSO não configurado.")

    try:
        discovery = await oidc_svc.discover(config.issuer_url)
    except oidc_svc.OIDCError as exc:
        logger.warning("oidc: falha na descoberta do IDP: %s", exc)
        raise HTTPException(
            status_code=502, detail="Falha ao conectar ao provedor SSO."
        ) from exc

    authorization_url = oidc_svc.start_login(
        discovery, config, redirect_uri=_redirect_uri(request)
    )
    return RedirectResponse(authorization_url, status_code=302)


@router.get("/callback")
async def oidc_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    from backend.api.handlers.auth import _set_auth_cookies
    from backend.rbac import auth as auth_svc
    from backend.rbac import oidc as oidc_svc

    if error:
        logger.warning("oidc: IDP retornou erro no callback: %s", error)
        raise HTTPException(
            status_code=401, detail=f"Login SSO recusado pelo provedor: {error}"
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="callback OIDC sem code/state.")

    config = _load_config()
    if config is None:
        raise HTTPException(status_code=404, detail="SSO não configurado.")

    try:
        discovery = await oidc_svc.discover(config.issuer_url)
        claims = await oidc_svc.complete_login(
            discovery, config, state=state, code=code
        )
    except oidc_svc.OIDCError as exc:
        logger.warning("oidc: falha ao completar login: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="IDP não devolveu claim 'email'.")
    if claims.get("email_verified") is not True:
        raise HTTPException(
            status_code=401,
            detail="IDP não confirmou que o endereço de email foi verificado.",
        )

    _, access_token, refresh_token = await auth_svc.provision_or_login_sso(
        email, name=claims.get("name", "")
    )

    response = RedirectResponse("/", status_code=302)
    _set_auth_cookies(response, access_token, refresh_token)
    return response
