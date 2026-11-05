"""Handler do serviço AuthService — autenticação e identidade via REST.

Endpoints:
    POST /auth/signup               — cria conta (primeiro vira root)
    POST /auth/signin               — login com email + senha
    POST /auth/refresh              — rotaciona tokens
    POST /auth/signout              — logout (revoga refresh token)
    GET  /auth/me                   — dados do usuário autenticado
    POST /auth/change-password      — troca senha (requer auth)
    GET  /auth/has-users            — setup wizard: false = primeiro acesso
    GET  /auth/users                — lista usuários (admin/root only)
    POST /auth/users/{id}/role      — muda role (root only)
    GET  /auth/audit                — audit log (admin/root only)
    GET  /auth/envs                 — env overrides do usuário autenticado
    POST /auth/envs                 — define env override
    DELETE /auth/envs/{key}         — remove env override
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from backend.api.schemas import (
    AuditEntry,
    ChangePasswordRequest,
    EnvOverrideRequest,
    HasUsersResponse,
    InviteValidationResponse,
    PasswordResetConfirmBody,
    PasswordResetRequestBody,
    RefreshRequest,
    SetupLocalRequest,
    SetupLocalResponse,
    SigninRequest,
    SignoutRequest,
    SignupRequest,
    TokenResponse,
    UpdateProfileRequest,
    UpdateRoleRequest,
    UserListResponse,
    UsernameAvailableResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Cookie names
_ACCESS_COOKIE = "vectora_access"
_REFRESH_COOKIE = "vectora_refresh"
_COOKIE_MAX_AGE_ACCESS = 15 * 60  # 15 min
_COOKIE_MAX_AGE_REFRESH = 7 * 24 * 3600  # 7 dias


def _set_auth_cookies(
    response: Response, access_token: str, refresh_token: str
) -> None:
    """Grava cookies httpOnly nos dois tokens.

    SameSite=Lax (não Strict): cobre primary navigation + first-party
    submissões de formulário, que é o que a UI precisa. Strict quebrava
    login no celular via Tailscale porque alguns navegadores tratam o
    Set-Cookie pós-POST de cross-origin (frontend → proxy Hono → FastAPI)
    como segunda hop e descartavam o cookie com Strict. HttpOnly continua
    sendo a defesa contra XSS, não SameSite.

    Secure permanece False em HTTP. Em produção atrás de HTTPS reverso, o
    cookie ganha Secure via header `X-Forwarded-Proto: https` traduzido
    pelo proxy (ou config explícita por env).
    """
    response.set_cookie(
        _ACCESS_COOKIE,
        access_token,
        max_age=_COOKIE_MAX_AGE_ACCESS,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    response.set_cookie(
        _REFRESH_COOKIE,
        refresh_token,
        max_age=_COOKIE_MAX_AGE_REFRESH,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(_ACCESS_COOKIE)
    response.delete_cookie(_REFRESH_COOKIE)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _token_exp(access_token: str) -> int | None:
    """Decodifica o `exp` (epoch seconds) de um access token recém-emitido.

    Devolvido em `TokenResponse.user.token_expires_at` para o frontend
    agendar o aviso "sessão expira em breve" sem precisar decodificar o JWT
    bruto (impossível: viaja em cookie httpOnly, opaco para o JS).
    Decodificação best-effort — token acabou de ser assinado por nós, então
    falha aqui só indicaria bug; não deve quebrar o fluxo de auth.
    """
    from backend.rbac.auth import decode_access_token

    try:
        exp = decode_access_token(access_token).get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoints públicos (sem auth)
# ---------------------------------------------------------------------------


@router.get("/has-users", response_model=HasUsersResponse)
async def has_users_endpoint() -> HasUsersResponse:
    """Retorna false quando a instância ainda não tem nenhum usuário.

    Usado pelo frontend para decidir se mostra signup ou signin na primeira
    visita. Após o primeiro signup, retorna sempre true.
    """
    from backend.rbac.auth import has_users

    return HasUsersResponse(exists=await has_users())


@router.get("/username-available", response_model=UsernameAvailableResponse)
async def username_available_endpoint(username: str) -> UsernameAvailableResponse:
    """Checa disponibilidade de um username para o wizard de criação de conta.

    Rota pública (o wizard consulta antes do login) — ``/auth/*`` já é whitelist
    no AuthMiddleware. Devolve a forma normalizada, se está livre, e uma
    sugestão (o próprio normalizado quando livre; ``base#NNNN`` quando em uso).
    """
    from backend.rbac.auth import suggest_username, username_taken
    from backend.rbac.username import normalize_username

    normalized = normalize_username(username)
    taken = await username_taken(normalized)
    suggestion = normalized if not taken else await suggest_username(normalized)
    return UsernameAvailableResponse(
        normalized=normalized, available=not taken, suggestion=suggestion
    )


@router.post("/setup-local", response_model=SetupLocalResponse)
async def setup_local_endpoint(body: SetupLocalRequest) -> SetupLocalResponse:
    """Configura a instância pra modo local (uso no próprio PC, sem conta).

    Só permitido no primeiro acesso (instância ainda sem nenhum usuário) —
    evita que a rota pública sirva de vetor pra desligar auth numa instância
    multi-usuário já configurada. Persiste ``auth_required=false`` e o
    nome/empresa do usuário local em ``app_settings`` (SQLite,
    `backend/workspace/runtime_settings.py`) — nunca no ``.env``, que fica só
    pra segredos. Não cria linha na tabela `users` — daqui pra frente o
    `AuthMiddleware` libera todas as rotas e os handlers usam o fallback
    `"local"` (ver `chat.py::_user_id_from_request`) como user_id.
    """
    from backend.rbac.auth import has_users
    from backend.workspace.runtime_settings import runtime_settings

    if await has_users():
        raise HTTPException(
            status_code=409,
            detail="Instância já configurada — modo local só vale no primeiro acesso.",
        )

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nome é obrigatório.")
    company = body.company.strip()

    username = ""
    if body.username.strip():
        from backend.rbac.username import normalize_username

        username = normalize_username(body.username.strip())

    runtime_settings.auth_required = False
    runtime_settings.set_local_user(name, company, username=username)

    logger.info(
        "auth/setup-local: instância configurada em modo local (auth desabilitado)"
    )
    return SetupLocalResponse(ok=True)


@router.get("/invite/{token}", response_model=InviteValidationResponse)
async def validate_invite_endpoint(token: str) -> InviteValidationResponse:
    """Valida um token de convite para a página de signup pré-verificar."""
    from backend.rbac import auth as auth_svc

    info = await auth_svc.validate_invite(token)
    if info is None:
        return InviteValidationResponse(valid=False)
    return InviteValidationResponse(valid=True, email=info["email"], role=info["role"])


@router.post("/signup", response_model=TokenResponse)
async def signup_endpoint(
    body: SignupRequest,
    response: Response,
) -> TokenResponse:
    """Cria conta.

    Camadas de autorização:
    1. Sem usuários ainda → signup do root permitido.
    2. Token de convite válido → permitido com a role do convite (consumido).
    3. Caso contrário → 403.

    Retorna tokens via JSON e também os grava em cookies httpOnly.
    """
    from backend.rbac import auth as auth_svc

    invite_role = None
    invite_token = body.invite_token.strip()

    if await auth_svc.has_users():
        invite = await auth_svc.validate_invite(invite_token) if invite_token else None
        if invite is None:
            raise HTTPException(
                status_code=403,
                detail="Signup público desabilitado. Contate o administrador.",
            )
        invite_role = invite["role"]

    try:
        user, access_token, refresh_token = await auth_svc.signup(
            body.email,
            body.password,
            role=invite_role,
            name=body.name,
            username=body.username,
        )
    except auth_svc.UsernameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if invite_token:
        await auth_svc.consume_invite(invite_token, user.id)

    _set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.from_user(user, token_expires_at=_token_exp(access_token)),
    )


@router.post("/signin", response_model=TokenResponse)
async def signin_endpoint(
    body: SigninRequest,
    request: Request,
    response: Response,
) -> TokenResponse:
    from backend.rbac import auth as auth_svc

    ip = _client_ip(request)
    try:
        user, access_token, refresh_token = await auth_svc.signin(
            body.email, body.password, ip=ip
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.from_user(user, token_expires_at=_token_exp(access_token)),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_endpoint(
    body: RefreshRequest,
    request: Request,
    response: Response,
) -> TokenResponse:
    """Rotaciona tokens usando o refresh token do body ou do cookie."""
    from backend.rbac import auth as auth_svc

    token = body.refresh_token or request.cookies.get(_REFRESH_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="Refresh token não fornecido.")
    try:
        user, access_token, new_refresh = await auth_svc.refresh_tokens(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_auth_cookies(response, access_token, new_refresh)
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        user=UserResponse.from_user(user, token_expires_at=_token_exp(access_token)),
    )


@router.post("/signout")
async def signout_endpoint(
    body: SignoutRequest,
    request: Request,
    response: Response,
) -> dict:
    from backend.rbac import auth as auth_svc

    token = body.refresh_token or request.cookies.get(_REFRESH_COOKIE, "")
    if token:
        await auth_svc.signout(token)
    _clear_auth_cookies(response)
    return {}


# ---------------------------------------------------------------------------
# Endpoints protegidos (requerem auth — via middleware)
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse)
async def me_endpoint(request: Request) -> UserResponse:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    # Repassa o `exp` do access token (anexado pelo AuthMiddleware) para o
    # frontend agendar o aviso de renovação de sessão.
    token_exp = getattr(request.state, "token_exp", None)
    return UserResponse.from_user(user, token_expires_at=token_exp)


@router.patch("/me", response_model=UserResponse)
async def update_me_endpoint(
    body: UpdateProfileRequest, request: Request
) -> UserResponse:
    """Atualiza campos do próprio perfil. Atualmente: apenas ``name``."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")

    from backend.rbac import auth as auth_svc

    if body.name is not None:
        try:
            updated = await auth_svc.update_profile(user.id, name=body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return UserResponse.from_user(updated)

    return UserResponse.from_user(user)


@router.get("/usage")
async def get_usage(request: Request) -> dict:
    """Consumo de requisições do usuário na janela de rate limit."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    from backend.services.usage import usage_tracker

    return usage_tracker.usage(user.id)


# ---------------------------------------------------------------------------
# Chaves SSH por usuário (workspaces remotos)
# ---------------------------------------------------------------------------


@router.get("/ssh-keys")
async def list_user_ssh_keys(request: Request) -> dict:
    """Lista os ``key_id``s armazenados para o usuário autenticado."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    from backend.secrets.ssh_keys import list_ssh_keys

    return {"keys": list_ssh_keys(user.id)}


@router.post("/ssh-keys")
async def upload_user_ssh_key(request: Request) -> dict:
    """Faz upload de uma chave privada SSH (multipart/form-data).

    O ID retornado é determinístico (sha256[:12] do conteúdo); subir
    a mesma chave duas vezes é idempotente.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    from starlette.datastructures import UploadFile

    form = await request.form()
    file = form.get("key")
    if not isinstance(file, UploadFile):
        raise HTTPException(
            status_code=400, detail="Arquivo 'key' obrigatório (multipart)."
        )
    content = await file.read()
    if not content or len(content) < 64:
        raise HTTPException(status_code=400, detail="Chave inválida ou vazia.")
    from backend.secrets.ssh_keys import add_ssh_key

    key_id = add_ssh_key(user.id, content)
    return {"key_id": key_id}


@router.delete("/ssh-keys/{key_id}")
async def delete_user_ssh_key(request: Request, key_id: str) -> dict:
    """Remove uma chave do storage do usuário."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    from backend.secrets.ssh_keys import remove_ssh_key

    ok = remove_ssh_key(user.id, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Chave não encontrada.")
    return {"status": "deleted"}


@router.get("/ws-token")
async def get_ws_token(request: Request) -> dict:
    """Devolve o access token (do cookie) para uso em WebSockets cross-origin.

    O cookie é httpOnly — o JS do browser não consegue lê-lo. Para conectar ao
    WebSocket do terminal, expomos o token via JSON para que o front passe na
    query string da conexão (cookies não trafegam em WS cross-origin).
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    token = request.cookies.get(_ACCESS_COOKIE, "")
    if not token:
        # Sem cookie: pode ter vindo via Bearer header — devolve o que veio.
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sem token disponível.")
    return {"token": token}


@router.post("/password-reset/request")
async def password_reset_request_endpoint(body: PasswordResetRequestBody) -> dict:
    """Gera um token de recuperação de senha, se o email existir.

    Resposta sempre `{"ok": True}` independente do email existir ou não —
    nunca revela por HTTP se uma conta existe (evita enumeração). O token
    de verdade só é gravado no audit log/passado adiante por quem chama
    `request_password_reset` internamente (envio de email fica fora desta
    sprint — sem infra de email transacional configurada; ver docstring
    de `auth_svc.request_password_reset`)."""
    from backend.rbac import auth as auth_svc

    await auth_svc.request_password_reset(body.email)
    return {"ok": True}


@router.post("/password-reset/confirm")
async def password_reset_confirm_endpoint(body: PasswordResetConfirmBody) -> dict:
    from backend.rbac import auth as auth_svc

    try:
        await auth_svc.confirm_password_reset(body.token, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/change-password")
async def change_password_endpoint(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
) -> dict:
    from backend.rbac import auth as auth_svc

    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    try:
        await auth_svc.change_password(user.id, body.old_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Limpa cookies — próximo request precisa fazer signin novamente
    _clear_auth_cookies(response)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UserListResponse)
async def list_users(request: Request) -> UserListResponse:
    from backend.rbac.auth import get_db_for_audit
    from backend.rbac.permissions import require_min_role

    user = getattr(request.state, "user", None)
    require_min_role(user, "admin")

    db = await get_db_for_audit()
    async with db.execute(
        "SELECT id, email, role, created_at, last_login_at FROM users ORDER BY created_at"
    ) as cur:
        rows = await cur.fetchall()

    users = [
        UserResponse(
            id=r["id"],
            email=r["email"],
            role=r["role"],
            created_at=r["created_at"],
            last_login_at=r["last_login_at"],
        )
        for r in rows
    ]
    return UserListResponse(users=users)


@router.post("/users/{user_id}/role")
async def update_role(user_id: str, body: UpdateRoleRequest, request: Request) -> dict:
    from backend.rbac.auth import get_db_for_audit
    from backend.rbac.permissions import require_min_role

    caller = getattr(request.state, "user", None)
    require_min_role(caller, "root")

    db = await get_db_for_audit()
    await db.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, user_id))
    await db.commit()
    return {"ok": True}


@router.get("/audit", response_model=list[AuditEntry])
async def get_audit_log(
    request: Request,
    limit: int = 100,
    action: str | None = None,
    user_id: str | None = None,
) -> list[AuditEntry]:
    import json

    from backend.rbac.auth import get_db_for_audit
    from backend.rbac.permissions import require_min_role

    caller = getattr(request.state, "user", None)
    require_min_role(caller, "admin")

    db = await get_db_for_audit()
    query = "SELECT * FROM audit WHERE 1=1"
    params: list = []
    if action:
        query += " AND action = ?"
        params.append(action)
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(min(limit, 500))

    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()

    return [
        AuditEntry(
            id=r["id"],
            user_id=r["user_id"],
            action=r["action"],
            target_type=r["target_type"],
            target_id=r["target_id"],
            timestamp=r["timestamp"],
            ip=r["ip"] or "",
            success=bool(r["success"]),
            metadata=json.loads(r["metadata_json"] or "{}"),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Env overrides
# ---------------------------------------------------------------------------


@router.get("/envs")
async def get_envs(request: Request) -> dict:
    from backend.rbac import auth as auth_svc

    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    overrides = await auth_svc.get_env_overrides(user.id)
    # Mascara valores para exibição — nunca retorna o valor real para o cliente
    masked = dict.fromkeys(overrides, "••••••••")
    return {"envs": masked, "keys": list(overrides.keys())}


@router.post("/envs")
async def set_env(body: EnvOverrideRequest, request: Request) -> dict:
    from backend.rbac.subscription import require_pro
    from backend.services.env_keys import CONNECT_ENV_KEYS, RUNTIME_ENV_KEYS

    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    # Salvar credencial de Connect exige tier pro; remover não — o usuário
    # sempre pode apagar a própria credencial, mesmo sem Pro.
    if body.key.upper() in CONNECT_ENV_KEYS:
        require_pro()
    # Override por usuário via adapter declarativo — encapsula o acesso à
    # tabela (incluindo o fallback do usuário virtual "local").
    from backend.config.adapters import UserRowAdapter

    await UserRowAdapter(body.key).set(user.id, body.value)

    # Keys de LLM/search (GOOGLE_API_KEY etc.) precisam valer na PRÓXIMA
    # chamada ao provider, não só no próximo boot — env_overrides_json é
    # lido só pra integrações OAuth, nunca chega em os.environ. Aplica
    # também pelo mesmo caminho de /admin/api-keys (single-tenant por
    # instância — seguro aplicar globalmente).
    key_upper = body.key.upper()
    if key_upper in RUNTIME_ENV_KEYS:
        from backend.services.env_keys import apply_llm_env_key, default_env_file

        apply_llm_env_key(default_env_file(), key_upper, body.value)

    # Credencial de mensageria nova/alterada -> liga (ou religa) o adapter
    # correspondente na hora. Sem isso o usuário salvaria o token e o bot só
    # apareceria no próximo boot, sem nenhum sinal de que faltava reiniciar.
    if key_upper in CONNECT_ENV_KEYS:
        await _sync_connect_adapters()

    return {"ok": True}


async def _sync_connect_adapters() -> None:
    """Melhor esforço: falha em reconciliar adapters nunca faz o salvamento
    da credencial em si parecer que deu errado."""
    try:
        from backend.services.connect.manager import sync_adapters

        await sync_adapters()
    except Exception:
        logger.exception("auth: falha ao reconciliar adapters de Connect")


@router.delete("/envs/{key}")
async def delete_env(key: str, request: Request) -> dict:
    from backend.services.env_keys import CONNECT_ENV_KEYS, RUNTIME_ENV_KEYS

    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    from backend.config.adapters import UserRowAdapter

    await UserRowAdapter(key).delete(user.id)

    # Credencial removida -> derruba o adapter correspondente. Sem isto o bot
    # continuaria no ar respondendo mensagens com uma credencial que o usuário
    # já revogou na UI.
    # Remover do banco sem limpar `os.environ` deixaria a credencial revogada
    # ainda valendo até o próximo boot — vale pra qualquer key aplicada em
    # runtime, não só as de mensageria.
    key_upper = key.upper()
    if key_upper in RUNTIME_ENV_KEYS:
        from backend.services.env_keys import apply_llm_env_key, default_env_file

        apply_llm_env_key(default_env_file(), key_upper, "")
    if key_upper in CONNECT_ENV_KEYS:
        await _sync_connect_adapters()

    return {"ok": True}
