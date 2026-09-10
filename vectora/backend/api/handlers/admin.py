"""Handler do painel de administração.

Endpoints (todos exigem role admin ou root):
    GET    /admin/users                      — lista todos os usuários
    GET    /admin/users/{user_id}            — detalhes de um usuário
    PATCH  /admin/users/{user_id}/role       — muda role de um usuário
    DELETE /admin/users/{user_id}            — remove usuário (root only)
    GET    /admin/tools                      — tools disponíveis e status
    POST   /admin/tools/{tool_name}/toggle   — habilita/desabilita tool globalmente
    GET    /admin/system                     — versão, status dos serviços, métricas
    GET    /admin/config                     — configurações globais do servidor
    PATCH  /admin/config                     — atualiza configurações globais
    GET    /admin/agents/resolve             — dump do NativeAgent resolvido (tools/prompt/subagentes)
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Helpers de autorização
# ---------------------------------------------------------------------------

_ADMIN_ROLES = {"root", "admin"}
_ROOT_ONLY_ROLES = {"root"}


def require_admin(user: Any) -> None:
    """Lança HTTPException 403 se user não é admin ou root."""
    role = getattr(user, "role", None)
    if role not in _ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Acesso negado. Role '{role}' não tem permissão de administrador.",
        )


def require_root(user: Any) -> None:
    """Lança HTTPException 403 se user não é root."""
    role = getattr(user, "role", None)
    if role not in _ROOT_ONLY_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acesso negado. Apenas root pode executar esta ação.",
        )


def _get_user(request: Request) -> Any:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


# ---------------------------------------------------------------------------
# Helpers de informação do sistema
# ---------------------------------------------------------------------------


def _build_system_info() -> dict[str, Any]:
    """Constrói o dicionário de informações do sistema."""
    try:
        from backend.version import __version__, get_build_version

        version = __version__
        build_version = get_build_version()
    except Exception:
        version = "unknown"
        build_version = "unknown"

    return {
        "version": version,
        "build_version": build_version,
        "python_version": sys.version,
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UpdateRoleBody(BaseModel):
    role: str


class ToolToggleBody(BaseModel):
    enabled: bool


class CreateInviteBody(BaseModel):
    role: str = "member"
    email: str | None = None
    ttl_hours: int = 24


class ToolOverrideBody(BaseModel):
    disabled: list[str] = []


class PatchConfigBody(BaseModel):
    allow_public_signup: bool | None = None
    default_model: str | None = None
    max_recursion: int | None = None
    # `vectora_token`: token de licença do produto. Persiste em
    # `~/.vectora/config.toml`; aplica imediatamente no `os.environ`,
    # mas o `LicenseStatusInfo` cacheado só renova no próximo
    # `validate_license_*` (TTL 6h ou após restart).
    vectora_token: str | None = None


class CreateSafeRootBody(BaseModel):
    path: str
    label: str = ""


class UpdateSafeRootBody(BaseModel):
    label: str


# ---------------------------------------------------------------------------
# Endpoints de administração
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_users(request: Request) -> dict:
    """Lista todos os usuários cadastrados com stats básicos."""
    user = _get_user(request)
    require_admin(user)

    try:
        from backend.rbac import auth as auth_svc

        users = await auth_svc.list_users()
        return {
            "users": [
                {
                    "id": u.id,
                    "email": u.email,
                    "role": u.role,
                    "created_at": str(u.created_at),
                    "last_login_at": str(u.last_login_at)
                    if getattr(u, "last_login_at", None)
                    else None,
                }
                for u in users
            ],
            "total": len(users),
        }
    except Exception as exc:
        logger.exception("list_users failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/users/{user_id}")
async def get_user_detail(request: Request, user_id: str) -> dict:
    """Detalhes de um usuário específico."""
    user = _get_user(request)
    require_admin(user)

    try:
        from backend.rbac import auth as auth_svc

        target = await auth_svc.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        overrides = await auth_svc.get_env_overrides(user_id)
        # Nunca expõe valores — apenas as chaves
        env_keys = list(overrides.keys())

        return {
            "id": target.id,
            "email": target.email,
            "role": target.role,
            "created_at": str(target.created_at),
            "last_login_at": str(target.last_login_at)
            if getattr(target, "last_login_at", None)
            else None,
            "env_keys": env_keys,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get_user_detail failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/users/{user_id}/role")
async def update_user_role(
    request: Request, user_id: str, body: UpdateRoleBody
) -> dict:
    """Muda o role de um usuário. Apenas root pode promover a root."""
    user = _get_user(request)
    require_admin(user)

    # Promover para root exige ser root
    if body.role == "root":
        require_root(user)

    valid_roles = {"root", "admin", "member", "viewer"}
    if body.role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Role inválido. Valores válidos: {sorted(valid_roles)}",
        )

    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.update_user_role(user_id, body.role)
        return {"status": "updated", "user_id": user_id, "role": body.role}
    except Exception as exc:
        logger.exception("update_user_role failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: str) -> dict:
    """Remove um usuário. Apenas root pode deletar usuários."""
    user = _get_user(request)
    require_root(user)

    if user.id == user_id:
        raise HTTPException(status_code=400, detail="Você não pode deletar a si mesmo.")

    try:
        from backend.rbac import auth as auth_svc

        await auth_svc.delete_user(user_id)
        return {"status": "deleted", "user_id": user_id}
    except Exception as exc:
        logger.exception("delete_user failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/users/{user_id}/tools")
async def get_user_tools(request: Request, user_id: str) -> dict:
    """Política de tools de um usuário (override ABAC)."""
    user = _get_user(request)
    require_admin(user)
    from backend.nodes.tools import ALL_TOOL_NAMES
    from backend.rbac import tool_policy

    return {
        "disabled": tool_policy.get_disabled(user_id),
        "available": sorted(ALL_TOOL_NAMES),
    }


@router.post("/users/{user_id}/tools")
async def set_user_tools(
    request: Request, user_id: str, body: ToolOverrideBody
) -> dict:
    """Define as tools desabilitadas de um usuário (admin override)."""
    user = _get_user(request)
    require_admin(user)
    from backend.nodes.tools import ALL_TOOL_NAMES
    from backend.rbac import tool_policy

    valid = ALL_TOOL_NAMES
    unknown = [n for n in body.disabled if n not in valid]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Tools desconhecidas: {sorted(unknown)}"
        )
    tool_policy.set_disabled(user_id, body.disabled)
    return {"status": "ok", "user_id": user_id, "disabled": body.disabled}


@router.post("/invites")
async def create_invite(request: Request, body: CreateInviteBody) -> dict:
    """Gera um link de convite de signup com token expirável.

    Convidar membro adicional (2º+ usuário) é feature de time — exige plano
    Pro. O primeiro usuário (root) não passa por aqui: nasce direto no
    signup inicial (``backend/services/auth.py::signup``), sempre livre.
    """
    import os

    from backend.rbac.subscription import require_pro

    require_pro()

    user = _get_user(request)
    require_admin(user)

    valid_roles = {"admin", "member", "viewer"}
    if body.role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Role inválido para convite. Válidos: {sorted(valid_roles)}",
        )

    try:
        from typing import cast

        from backend.rbac import auth as auth_svc
        from backend.rbac.auth import Role

        token, expires_at = await auth_svc.create_invite(
            user.id,
            role=cast("Role", body.role),
            email=body.email,
            ttl_hours=body.ttl_hours,
        )
        frontend = os.environ.get("VECTORA_FRONTEND_URL", "http://localhost:3000")
        url = f"{frontend.rstrip('/')}/auth/signup?invite={token}"
        return {"token": token, "url": url, "expires_at": expires_at}
    except Exception as exc:
        logger.exception("create_invite failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/invites")
async def list_invites(request: Request) -> dict:
    """Lista convites pendentes (não usados, não expirados)."""
    user = _get_user(request)
    require_admin(user)

    try:
        from backend.rbac import auth as auth_svc

        invites = await auth_svc.list_invites()
        return {"invites": invites, "total": len(invites)}
    except Exception as exc:
        logger.exception("list_invites failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/invites/{token_hash}")
async def revoke_invite(request: Request, token_hash: str) -> dict:
    """Revoga um convite pendente pelo seu hash."""
    user = _get_user(request)
    require_admin(user)

    try:
        from backend.rbac import auth as auth_svc

        removed = await auth_svc.revoke_invite(token_hash)
        if not removed:
            raise HTTPException(status_code=404, detail="Convite não encontrado.")
        return {"status": "revoked", "token_hash": token_hash}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("revoke_invite failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tools")
async def list_tools_admin(request: Request) -> dict:
    """Lista todas as tools com status de habilitação global.

    "enabled" reflete ``tool_policy.GLOBAL_SCOPE`` — o mesmo kill-switch
    aplicado na montagem do ``NativeAgent`` (``agent_factory._native_tool_registry``),
    não um valor decorativo.
    """
    user = _get_user(request)
    require_admin(user)

    try:
        from backend.nodes.tools import ALL_TOOL_SPECS
        from backend.rbac import tool_policy

        disabled_global = set(tool_policy.get_disabled(tool_policy.GLOBAL_SCOPE))
        tools = [
            {
                "name": spec.name,
                "description": spec.description or "",
                "category": spec.extras.category,
                "destructive": bool(spec.extras.destructive),
                "enabled": spec.name not in disabled_global,
            }
            for spec in ALL_TOOL_SPECS
        ]
        return {"tools": tools, "total": len(tools)}
    except Exception as exc:
        logger.exception("list_tools_admin failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/agents/resolve")
async def resolve_agent(
    request: Request,
    user_id: str = "",
    chat_mode: bool = False,
    workspace_id: str = "",
) -> dict:
    """Introspecção do ``NativeAgent`` que o motor monta hoje pra uma
    combinação ``(user_id, chat_mode, workspace_id)`` — equivalente nativo
    ao ``dsh --dump-config`` do deepseek-harness (sem expor mecanismo de
    plugin nenhum, só o que ``agent_factory`` já monta): tools resolvidas,
    system prompt final, catálogo de subagentes. Existe pra não precisar
    vasculhar logs pra descobrir por que uma tool não está disponível pra
    um usuário/sessão específica.
    """
    admin_user = _get_user(request)
    require_admin(admin_user)

    try:
        from backend.services.agent_factory import get_native_agent

        agent = await get_native_agent(
            user_id=user_id or None,
            chat_mode=chat_mode,
            workspace_id=workspace_id or None,
        )
        return {
            "user_id": user_id or "local",
            "chat_mode": chat_mode,
            "workspace_id": workspace_id or None,
            "tools": [
                {
                    "name": spec.name,
                    "category": spec.extras.category,
                    "destructive": bool(spec.extras.destructive),
                    "render_hint": spec.extras.render_hint,
                }
                for spec in sorted(agent.tool_registry.all(), key=lambda s: s.name)
            ],
            "subagents": [
                {
                    "name": name,
                    "description": spec.description,
                    "tools": sorted(t.name for t in spec.tools),
                }
                for name, spec in sorted(agent.subagent_catalog.items())
            ],
            "system_prompt": agent.system_prompt,
            "system_prompt_length": len(agent.system_prompt),
        }
    except Exception as exc:
        logger.exception("resolve_agent failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tools/{tool_name}/toggle")
async def toggle_tool(request: Request, tool_name: str, body: ToolToggleBody) -> dict:
    """Habilita ou desabilita uma tool globalmente (kill-switch, todas as sessões).

    Persiste em ``tool_policy.GLOBAL_SCOPE`` — o próximo ``get_native_agent()``
    (qualquer usuário) reconstrói o ``NativeAgent`` sem a tool, via invalidação
    de cache em ``agent_factory._check_global_tools_version``.
    """
    user = _get_user(request)
    require_admin(user)

    from backend.nodes.tools import ALL_TOOL_NAMES
    from backend.rbac import tool_policy

    valid = ALL_TOOL_NAMES
    if tool_name not in valid:
        raise HTTPException(status_code=404, detail=f"Tool desconhecida: {tool_name}")

    disabled = set(tool_policy.get_disabled(tool_policy.GLOBAL_SCOPE))
    if body.enabled:
        disabled.discard(tool_name)
    else:
        disabled.add(tool_name)
    tool_policy.set_disabled(tool_policy.GLOBAL_SCOPE, sorted(disabled))

    logger.info(
        "admin: tool '%s' %s globalmente por user_id=%s",
        tool_name,
        "habilitada" if body.enabled else "desabilitada",
        user.id,
    )
    return {"status": "ok", "tool": tool_name, "enabled": body.enabled}


@router.get("/system")
async def system_info(request: Request) -> dict:
    """Retorna versão, status dos serviços e métricas básicas."""
    user = _get_user(request)
    require_admin(user)

    info = _build_system_info()

    # Status dos serviços
    services: dict[str, str] = {}
    try:
        import aiosqlite

        services["sqlite"] = "ok"
    except Exception:
        services["sqlite"] = "unavailable"

    try:
        import lancedb

        services["lancedb"] = "ok"
    except Exception:
        services["lancedb"] = "unavailable"

    # Métricas recentes do tracer
    recent_spans: list = []
    try:
        from backend.persistence.tracer import tracer

        recent_spans = await tracer.get_recent(n=20)
    except Exception:
        pass

    return {
        **info,
        "services": services,
        "recent_spans_count": len(recent_spans),
    }


@router.get("/config")
async def get_server_config(request: Request) -> dict:
    """Retorna as configurações globais do servidor."""
    user = _get_user(request)
    require_admin(user)

    try:
        from backend.settings import settings

        raw_token = os.environ.get("VECTORA_TOKEN", "").strip()
        # Mostra prefixo + sufixo para o operador conferir sem expor o segredo.
        masked = (
            f"{raw_token[:7]}•••{raw_token[-4:]}"
            if len(raw_token) > 14
            else ("•" * 8 if raw_token else "")
        )

        return {
            "default_model": getattr(settings, "default_model", ""),
            "max_recursion": getattr(settings, "max_recursion", 50),
            "allow_public_signup": getattr(settings, "allow_public_signup", False),
            "db_dsn": getattr(settings, "db_dsn", ""),
            "vectora_token_masked": masked,
            "vectora_token_configured": bool(raw_token),
        }
    except Exception as exc:
        logger.exception("get_server_config failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/config")
async def patch_server_config(request: Request, body: PatchConfigBody) -> dict:
    """Atualiza configurações globais do servidor em runtime."""
    user = _get_user(request)
    require_root(user)

    # Persiste em ~/.vectora/config.toml [server] — sobrevive a restart, sem
    # depender de Postgres (ver write_config_section em src/services/license.py).
    updated: dict[str, Any] = {}
    toml_values: dict[str, str | int | bool | None] = {}
    try:
        from backend.settings import settings

        if body.allow_public_signup is not None:
            settings.allow_public_signup = body.allow_public_signup  # type: ignore[attr-defined]
            updated["allow_public_signup"] = body.allow_public_signup
            toml_values["allow_public_signup"] = body.allow_public_signup

        if body.default_model is not None:
            settings.default_model = body.default_model  # type: ignore[attr-defined]
            updated["default_model"] = body.default_model
            toml_values["default_model"] = body.default_model

        if body.max_recursion is not None:
            settings.max_recursion = body.max_recursion  # type: ignore[attr-defined]
            updated["max_recursion"] = body.max_recursion
            toml_values["max_recursion"] = body.max_recursion

        if toml_values:
            from backend.services.license import write_config_section

            write_config_section("server", toml_values)

        if body.vectora_token is not None:
            token = body.vectora_token.strip()
            os.environ["VECTORA_TOKEN"] = token
            # Persiste em ~/.vectora/config.toml na seção [license] para
            # que o próximo boot do binário aplique sem o operador
            # precisar exportar a env var manualmente.
            from backend.services.license import (
                clear_license_cache,
                write_token_to_config,
            )

            write_token_to_config(token)
            # Cache antigo refletia o token anterior — descarta para a
            # próxima validação (POST /license/validate) partir do zero.
            clear_license_cache()
            updated["vectora_token_configured"] = bool(token)

        logger.info("admin: config patched by root user_id=%s: %s", user.id, updated)
        return {"status": "updated", "updated": updated}
    except Exception as exc:
        logger.exception("patch_server_config failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Ordem de fallback de providers LLM — persiste em settings.json
# ---------------------------------------------------------------------------


class FallbackOrderBody(BaseModel):
    order: list[str] = []


@router.get("/model/fallback-order")
async def get_fallback_order(request: Request) -> dict:
    """Lê a ordem de fallback de providers LLM (lista de 'provider:model')."""
    require_admin(_get_user(request))
    from backend.workspace.runtime_settings import runtime_settings

    return {"fallback_order": runtime_settings.fallback_order}


@router.patch("/model/fallback-order")
async def patch_fallback_order(request: Request, body: FallbackOrderBody) -> dict:
    """Define a ordem de fallback de LLM; devolve a lista normalizada persistida."""
    require_admin(_get_user(request))
    from backend.llm.provider_fallback import _provider_has_key
    from backend.workspace.runtime_settings import runtime_settings

    valid: list[str] = []
    for raw in body.order:
        model = raw.strip()
        provider, separator, model_name = model.partition(":")
        if (
            separator
            and provider
            and model_name.strip()
            and _provider_has_key(provider.replace("-", "_"))
        ):
            valid.append(model)
    runtime_settings.set_fallback_order(valid)
    return {"status": "updated", "fallback_order": runtime_settings.fallback_order}


class ImageFallbackModelBody(BaseModel):
    model: str = ""


@router.get("/model/image-fallback")
async def get_image_fallback_model(request: Request) -> dict:
    """Modelo usado automaticamente quando o ativo não processa imagem e a
    mensagem tem anexo (`backend/api/handlers/chat.py::
    _resolve_image_fallback_model`). String vazia = sem fallback
    configurado (comportamento antigo: bloqueia o envio)."""
    require_admin(_get_user(request))
    from backend.config.registry import get_field

    field = get_field("image_fallback_model")
    value = str(field.get() or "") if field is not None else ""
    return {"model": value}


@router.patch("/model/image-fallback")
async def patch_image_fallback_model(
    request: Request, body: ImageFallbackModelBody
) -> dict:
    """Define (ou limpa, com string vazia) o modelo de fallback de imagem."""
    require_admin(_get_user(request))
    from backend.api.handlers.chat import (
        _coerce_capability_state,
        _model_supports_vision,
    )
    from backend.config.registry import get_field
    from backend.settings import CapabilityState

    model = body.model.strip()
    if model:
        provider, separator, model_name = model.partition(":")
        from backend.llm.provider_fallback import _provider_has_key

        if (
            not separator
            or not provider
            or not model_name.strip()
            or not _provider_has_key(provider.replace("-", "_"))
        ):
            raise HTTPException(
                status_code=422, detail="Modelo ou provider indisponível"
            )
        state = _coerce_capability_state(await _model_supports_vision(model))
        if state is not CapabilityState.SUPPORTED:
            raise HTTPException(
                status_code=422,
                detail="O modelo precisa declarar suporte conhecido a imagens",
            )
    field = get_field("image_fallback_model")
    if field is not None:
        field.set(model)
    return {"status": "updated", "model": model}


class MediaModelsBody(BaseModel):
    """String vazia **limpa** a escolha (volta a valer a env var); `None`
    significa "não mexe nesta chave"."""

    ollama_image_model: str | None = None
    ollama_tts_model: str | None = None
    openrouter_image_model: str | None = None
    openrouter_tts_model: str | None = None
    ollama_video_model: str | None = None
    openrouter_video_model: str | None = None


@router.get("/media-models")
async def get_media_models(request: Request) -> dict:
    """Modelos de imagem/TTS escolhidos para os providers de gateway.

    Devolve o valor efetivo (UI vence env), para o seletor mostrar o que o
    agente realmente vai usar — não o que está só no banco.
    """
    require_admin(_get_user(request))
    from backend.settings import configured_gateway_model

    return {
        "models": {
            f"{provider}_{capability}_model": configured_gateway_model(
                provider, capability
            )
            for provider in ("ollama", "openrouter")
            for capability in ("image", "tts", "video")
        }
    }


@router.patch("/media-models")
async def patch_media_models(request: Request, body: MediaModelsBody) -> dict:
    """Grava a escolha de modelo por provider+capacidade."""
    require_admin(_get_user(request))
    from backend.settings import configured_gateway_model
    from backend.workspace.runtime_settings import runtime_settings

    runtime_settings.set_media_settings(**body.model_dump())
    return {
        "status": "updated",
        "models": {
            f"{provider}_{capability}_model": configured_gateway_model(
                provider, capability
            )
            for provider in ("ollama", "openrouter")
            for capability in ("image", "tts", "video")
        },
    }


class TimezoneBody(BaseModel):
    timezone: str = ""


@router.get("/timezone")
async def get_timezone(request: Request) -> dict:
    """Timezone IANA do usuário + a lista de zonas válidas para o seletor.

    A lista sai de `zoneinfo.available_timezones()` (stdlib, ~600 entradas) em
    vez de uma cópia estática no frontend — copiar a lista significaria ela
    divergir da que o backend aceita, e o usuário só descobriria ao salvar.
    """
    require_admin(_get_user(request))
    from zoneinfo import available_timezones

    from backend.workspace.runtime_settings import runtime_settings

    return {
        "timezone": runtime_settings.user_timezone,
        "available": sorted(available_timezones()),
    }


@router.patch("/timezone")
async def patch_timezone(request: Request, body: TimezoneBody) -> dict:
    """Define o timezone do usuário.

    Timezone inválido é rejeitado com 422 em vez de aceito e degradado: aqui o
    usuário escolheu explicitamente, então salvar outra coisa faria os
    agendamentos dispararem num fuso que ele não pediu, sem aviso.
    """
    require_admin(_get_user(request))
    from zoneinfo import ZoneInfo

    from backend.workspace.runtime_settings import runtime_settings

    clean = body.timezone.strip()
    if clean:
        try:
            ZoneInfo(clean)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"timezone inválido: {clean!r}"
            ) from exc

    runtime_settings.set_user_timezone(clean)
    return {"status": "updated", "timezone": runtime_settings.user_timezone}


# ---------------------------------------------------------------------------
# Pastas Seguras (SafeRoot)
# ---------------------------------------------------------------------------


@router.get("/safe-roots")
async def list_safe_roots_admin(request: Request) -> dict:
    """Lista as raízes confiáveis configuradas (admin)."""
    user = _get_user(request)
    require_admin(user)
    from backend.rbac.safe_roots import get_safe_root_registry

    registry = get_safe_root_registry()
    return {
        "roots": [r.model_dump() for r in registry.all_roots()],
    }


@router.post("/safe-roots")
async def create_safe_root(request: Request, body: CreateSafeRootBody) -> dict:
    """Adiciona uma nova raiz confiável. Idempotente por path."""
    user = _get_user(request)
    require_admin(user)
    from pathlib import Path as _Path

    from backend.rbac.safe_roots import get_safe_root_registry

    target = _Path(body.path).expanduser()
    try:
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Path inválido: {exc}") from exc
    if not target.exists() or not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Caminho não existe ou não é diretório: {target}",
        )

    registry = get_safe_root_registry()
    root = registry.add(str(target), body.label, str(user.id))
    logger.info(
        "admin: safe-root adicionado por user_id=%s path=%s label=%s",
        user.id,
        root.path,
        root.label,
    )
    return {"status": "created", "root": root.model_dump()}


@router.patch("/safe-roots/{root_id}")
async def update_safe_root(
    request: Request,
    root_id: str,
    body: UpdateSafeRootBody,
) -> dict:
    """Renomeia uma raiz confiável (label). Builtin aceita rename."""
    user = _get_user(request)
    require_admin(user)
    from backend.rbac.safe_roots import get_safe_root_registry

    registry = get_safe_root_registry()
    updated = registry.update_label(root_id, body.label)
    if updated is None:
        raise HTTPException(status_code=404, detail="Raiz não encontrada")
    return {"status": "updated", "root": updated.model_dump()}


@router.delete("/safe-roots/{root_id}")
async def delete_safe_root(request: Request, root_id: str) -> dict:
    """Remove uma raiz confiável. Recusa se for builtin."""
    user = _get_user(request)
    require_admin(user)
    from backend.rbac.safe_roots import get_safe_root_registry

    registry = get_safe_root_registry()
    root = registry.get(root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Raiz não encontrada")
    if root.builtin:
        raise HTTPException(
            status_code=400,
            detail="Raiz builtin não pode ser removida.",
        )
    ok = registry.remove(root_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Falha ao remover")
    logger.info("admin: safe-root removido por user_id=%s path=%s", user.id, root.path)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# API Keys — GET/PATCH/test (Google, Cohere, Tavily)
# ---------------------------------------------------------------------------

_API_KEY_FIELDS: dict[str, str] = {
    "google": "GOOGLE_API_KEY",
    "cohere": "COHERE_API_KEY",
    "tavily": "TAVILY_API_KEY",
}

# provider → chave do schema declarativo (backend.config.registry) que
# encapsula o acesso à env var via EnvAdapter. Os 3 providers expostos aqui
# têm field registrado em category="integrations"; leitura/escrita passam
# pelo registry, não por os.environ/apply_llm_env_key direto — mesma fonte
# usada por CLI e pelos demais handlers.
_API_KEY_REGISTRY_KEYS: dict[str, str] = {
    "google": "google_api_key",
    "cohere": "cohere_api_key",
    "tavily": "tavily_api_key",
}


def _mask_key(value: str) -> str:
    """Mostra prefixo + sufixo para conferência sem expor o segredo."""
    if not value:
        return ""
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:6]}•••{value[-4:]}"


class PatchApiKeysBody(BaseModel):
    google_api_key: str | None = None
    cohere_api_key: str | None = None
    tavily_api_key: str | None = None


class TestApiKeyBody(BaseModel):
    provider: str  # "google" | "cohere" | "tavily"
    api_key: str


@router.get("/api-keys")
async def get_api_keys(request: Request) -> dict:
    """Retorna status e valores mascarados das API keys de LLM/search.

    A leitura delega aos fields do schema declarativo (`EnvAdapter`) — a
    mesma fonte usada por CLI — em vez de ler `os.environ` diretamente.
    A enumeração de providers continua fixa (`_API_KEY_FIELDS`), só o
    mecanismo de acesso muda.
    """
    require_admin(_get_user(request))
    from backend.config.registry import get_field

    result: dict[str, dict[str, str | bool]] = {}
    for provider in _API_KEY_FIELDS:
        registry_key = _API_KEY_REGISTRY_KEYS.get(provider)
        raw = ""
        if registry_key:
            field = get_field(registry_key)
            if field is not None:
                raw = str(field.get() or "").strip()
        result[provider] = {
            "configured": bool(raw),
            "masked": _mask_key(raw),
        }
    return result


@router.patch("/api-keys")
async def patch_api_keys(request: Request, body: PatchApiKeysBody) -> dict:
    """Salva API keys em ~/.vectora/.env e atualiza os.environ em runtime."""
    require_admin(_get_user(request))
    from backend.config.registry import set_value

    updated: list[str] = []
    mapping = {
        "GOOGLE_API_KEY": body.google_api_key,
        "COHERE_API_KEY": body.cohere_api_key,
        "TAVILY_API_KEY": body.tavily_api_key,
    }
    for env_var, value in mapping.items():
        if value is None:
            continue
        # Escrita pelo field do registry (same EnvAdapter), não direto — a
        # mesma fonte usada por CLI. A key de lookup do registry é
        # minúscula, igual à env var sem o sufixo _API_KEY (ex.: GOOGLE_API_
        # KEY → google_api_key).
        registry_key = _API_KEY_REGISTRY_KEYS.get(
            next(
                (p for p, e in _API_KEY_FIELDS.items() if e == env_var),
                "",
            )
        )
        if registry_key is None:
            raise HTTPException(
                status_code=400, detail=f"Chave não registrada: {env_var}"
            )
        set_value(registry_key, value)
        updated.append(env_var)
    logger.info(
        "admin: api-keys atualizadas por user_id=%s: %s", _get_user(request).id, updated
    )
    return {"status": "updated", "updated": updated}


@router.post("/api-keys/test")
async def test_api_key(request: Request, body: TestApiKeyBody) -> dict:
    """Testa uma API key chamando o provider e retorna ok/error."""
    require_admin(_get_user(request))
    import time

    provider = body.provider.lower().strip()
    raw_key = body.api_key.strip()
    # Sentinela: usar a env já configurada (para chaves pré-preenchidas).
    if raw_key == "__use_env__":
        env_var = _API_KEY_FIELDS.get(provider, "")
        raw_key = os.environ.get(env_var, "").strip()
    api_key = raw_key
    if not api_key:
        return {"ok": False, "error": "Chave vazia"}

    start = time.monotonic()

    async def _test_google() -> tuple[bool, str]:
        try:
            from backend.llm.google.chat_client import GoogleChatClient
            from backend.llm.google.client import GoogleGenAIClient
            from backend.settings import settings as _s
            from backend.vtypes.message import MessageRole, text_message

            llm = GoogleChatClient(
                model=_s.google_model,
                client=GoogleGenAIClient(api_key=api_key),
            )
            await llm.agenerate([text_message(MessageRole.USER, "hi")])
            return True, ""
        except Exception as exc:
            return False, str(exc)

    async def _test_cohere() -> tuple[bool, str]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.cohere.com/v2/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code == 200:
                return True, ""
            try:
                err_msg = resp.json().get("message", "")
            except Exception:
                err_msg = ""
            return False, err_msg or f"HTTP {resp.status_code}"
        except Exception as exc:
            return False, str(exc)

    async def _test_tavily() -> tuple[bool, str]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"query": "test", "max_results": 1},
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 200:
                    return True, ""
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            return False, str(exc)

    testers = {"google": _test_google, "cohere": _test_cohere, "tavily": _test_tavily}
    tester = testers.get(provider)
    if tester is None:
        return {"ok": False, "error": f"Provider desconhecido: {provider}"}

    ok, error = await tester()
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return {"ok": ok, "error": error, "latency_ms": elapsed_ms}


# ---------------------------------------------------------------------------
# Storage endpoints
# ---------------------------------------------------------------------------


def _env_file() -> Path:
    p = settings.vectora_home / ".env"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/storage")
async def get_storage_status(request: Request) -> dict:
    """Retorna o status de saúde de todos os backends de storage.

    Inclui checkpointer, store (protocolo ``StoreBackend``), LanceDB e Postgres (se configurado).
    Usado pela aba "Storage" do painel Admin.

    Returns:
        ``{"checkpointer": {...}, "store": {...}, "lancedb": {...}, "postgres": {...}}``
    """
    user = _get_user(request)
    require_admin(user)

    from backend.storage.factory import storage_health

    return await storage_health()


@router.get("/storage/defaults")
async def get_storage_defaults(request: Request) -> dict:
    """Config default de cada serviço self-hosted para pré-preencher o wizard.

    Fonte única: ``dev_stack.connection_defaults()`` — as mesmas credenciais
    que o ``docker compose up`` cria. O Setup Wizard usa isto para preencher
    URL, API key (Qdrant) e o comando self-hosted, deixando a conexão
    automática sem digitação.

    Returns:
        ``{"redis": {"url": "...", "start_command": "..."}, ...}``
    """
    user = _get_user(request)
    require_admin(user)

    from backend.storage.dev_stack import connection_defaults

    return connection_defaults()


@router.post("/storage/test")
async def test_storage_connection(request: Request) -> dict:
    """Testa a conexão ao backend de storage especificado no body.

    Body (JSON):
        ``{"backend": "postgres", "dsn": "postgresql://..."}``
        ou ``{"backend": "qdrant", "url": "https://...", "api_key": "..."}``
        ou ``{"backend": "sqlite", "path": "/caminho/para/db.sqlite"}``
        ou ``{"backend": "redis", "url": "redis://..."}``

    Campos opcionais ``self_hosted`` (bool) e ``start_command`` (str): se a
    primeira tentativa falhar e ambos estiverem presentes, executa
    ``start_command`` (ex: ``docker compose up -d postgres``) e tenta
    novamente uma vez. Aplica-se apenas a serviços self-hosted — não usar com
    serviços terceirizados (Supabase, Upstash, Qdrant Cloud etc.).

    Returns:
        ``{"ok": true, "latency_ms": 12, "started": false}`` ou
        ``{"ok": false, "error": "...", "started": bool}``
    """
    user = _get_user(request)
    require_admin(user)

    import asyncio
    import time

    body = await request.json()
    backend = body.get("backend", "sqlite")

    async def _attempt() -> dict:  # noqa: PLR0911
        t0 = time.monotonic()
        try:
            if backend == "postgres":
                import asyncpg

                dsn = body.get("dsn") or ""
                if not dsn:
                    return {"ok": False, "error": "DSN não fornecido"}
                dsn_norm = dsn.replace("postgresql+asyncpg://", "postgresql://")
                conn = await asyncpg.connect(dsn_norm)
                await conn.execute("SELECT 1")
                await conn.close()

            elif backend == "qdrant":
                from qdrant_client import QdrantClient

                url = body.get("url") or ""
                api_key = body.get("api_key")
                if not url:
                    return {"ok": False, "error": "URL não fornecida"}
                client = QdrantClient(url=url, api_key=api_key)
                client.get_collections()

            elif backend == "sqlite":
                import aiosqlite

                path = body.get("path") or ""
                if not path:
                    return {"ok": False, "error": "Path não fornecido"}
                async with aiosqlite.connect(path) as conn:
                    await conn.execute("SELECT 1")

            elif backend == "redis":
                import redis.asyncio as aredis

                url = body.get("url") or ""
                if not url:
                    return {"ok": False, "error": "URL não fornecida"}
                client = aredis.from_url(url)
                try:
                    await client.ping()
                finally:
                    await client.aclose()

            else:
                return {"ok": False, "error": f"Backend desconhecido: {backend!r}"}

            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            return {"ok": True, "latency_ms": latency_ms}

        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    result = await _attempt()
    result["started"] = False

    if not result["ok"] and body.get("self_hosted") and body.get("start_command"):
        import shlex
        import subprocess  # nosec B404

        try:
            subprocess.Popen(  # noqa: S603, ASYNC220  # nosec B603 — comando configurado pelo operador no Setup Wizard
                shlex.split(body["start_command"]),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            result["started"] = True
            await asyncio.sleep(2)
            retry = await _attempt()
            retry["started"] = True
            return retry
        except Exception as exc:
            result["error"] = f"{result.get('error')} (falha ao iniciar serviço: {exc})"

    return result


@router.patch("/storage")
async def update_storage_config(request: Request) -> dict:
    """Atualiza configurações de storage em runtime.

    Body (JSON) — campos opcionais:
        ``storage_mode``: ``"lite"`` | ``"complete"``
        ``postgres_dsn``: DSN asyncpg
        ``redis_url``:    URL de conexão Redis
        ``qdrant_url``:   URL do cluster Qdrant
        ``qdrant_api_key``: API key Qdrant
        ``services``: ``{"postgres"|"redis"|"qdrant": {"self_hosted": bool,
        "start_command": str | null}}`` — config de auto-start usada por
        ``POST /admin/storage/test`` (apenas serviços self-hosted).

    Nota: alterações são aplicadas ao runtime_settings; persistem via
    ``~/.vectora/settings.json`` se o ``RuntimeSettings`` suportar save.

    Returns:
        ``{"status": "updated", "storage_mode": "complete"}``
    """
    user = _get_user(request)
    require_admin(user)

    body = await request.json()

    # Postgres/Redis/Qdrant são o modo completo, recurso Pro. O gate cobre
    # tanto o flip do modo quanto os campos de conexão: sem os dois, o não-Pro
    # deixa tudo configurado e só falta virar a chave. Voltar pra `lite` nunca
    # é bloqueado — prender no modo completo quem perdeu a licença deixaria a
    # instalação sem saída.
    _touches_complete = (
        body.get("storage_mode") == "complete"
        or "services" in body
        or any(
            k in body
            for k in ("postgres_dsn", "redis_url", "qdrant_url", "qdrant_api_key")
        )
    )
    if _touches_complete:
        from backend.rbac.subscription import require_pro

        require_pro()

    from backend.workspace.runtime_settings import runtime_settings

    updated: dict[str, object] = {}

    if "storage_mode" in body:
        mode = body["storage_mode"]
        if mode not in ("lite", "complete"):
            raise HTTPException(
                status_code=422,
                detail="storage_mode deve ser 'lite' ou 'complete'",
            )
        runtime_settings.storage_mode = mode

        from backend.settings import settings as _s

        _s.storage_mode = mode  # type: ignore[assignment]
        updated["storage_mode"] = mode

    if "postgres_dsn" in body:
        from backend.cli.keys import upsert_env_key
        from backend.settings import settings as _s

        _s.postgres_dsn = body["postgres_dsn"]
        upsert_env_key(_env_file(), "POSTGRES_DSN", body["postgres_dsn"])
        updated["postgres_dsn"] = "***"

    if "qdrant_url" in body:
        from backend.cli.keys import upsert_env_key
        from backend.settings import settings as _s

        _s.qdrant_url = body["qdrant_url"]
        upsert_env_key(_env_file(), "QDRANT_URL", body["qdrant_url"])
        updated["qdrant_url"] = body["qdrant_url"]

    if "qdrant_api_key" in body:
        from backend.cli.keys import upsert_env_key
        from backend.settings import settings as _s

        _s.qdrant_api_key = body["qdrant_api_key"]
        upsert_env_key(_env_file(), "QDRANT_API_KEY", body["qdrant_api_key"])
        updated["qdrant_api_key"] = "***"

    if "redis_url" in body:
        from backend.cli.keys import upsert_env_key
        from backend.settings import settings as _s

        _s.redis_url = body["redis_url"]
        upsert_env_key(_env_file(), "REDIS_URL", body["redis_url"])
        updated["redis_url"] = "***"

    if "services" in body and isinstance(body["services"], dict):
        for service, cfg in body["services"].items():
            if service not in ("postgres", "redis", "qdrant") or not isinstance(
                cfg, dict
            ):
                continue
            runtime_settings.set_service_startup(
                service,
                self_hosted=bool(cfg.get("self_hosted", False)),
                start_command=cfg.get("start_command") or None,
            )
        updated["services"] = list(body["services"])

    logger.info("admin: storage config atualizado por %s: %s", user.id, list(updated))
    return {"status": "updated", **updated}


# ---------------------------------------------------------------------------
# Tokens de serviço (máquina-a-máquina) — backend.rbac.token_auth
# ---------------------------------------------------------------------------


class CreateServiceTokenBody(BaseModel):
    name: str
    scopes: list[str] = []


@router.post("/service-tokens")
async def create_service_token_endpoint(
    request: Request, body: CreateServiceTokenBody
) -> dict:
    """Cria um token de serviço. Apenas root — credencial de máquina tem o
    mesmo peso de criar um usuário novo. O token cru só aparece nesta
    resposta, nunca mais é recuperável."""
    user = _get_user(request)
    require_root(user)

    from backend.rbac import token_auth
    from backend.rbac.auth import _get_db

    db = await _get_db()
    token_obj, raw_token = await token_auth.create_service_token(
        db, body.name, body.scopes, created_by=user.id
    )
    logger.info("admin: service token '%s' criado por %s", body.name, user.id)
    return {"token": token_obj.model_dump(), "raw_token": raw_token}


@router.get("/service-tokens")
async def list_service_tokens_endpoint(request: Request) -> dict:
    """Lista tokens de serviço (revogados inclusos) — nunca o token cru."""
    user = _get_user(request)
    require_root(user)

    from backend.rbac import token_auth
    from backend.rbac.auth import _get_db

    db = await _get_db()
    tokens = await token_auth.list_service_tokens(db)
    return {"tokens": [t.model_dump() for t in tokens]}


@router.delete("/service-tokens/{token_id}")
async def revoke_service_token_endpoint(request: Request, token_id: str) -> dict:
    """Revoga um token de serviço. Idempotente — revogar duas vezes não é
    erro, só a segunda chamada devolve `revoked: false`."""
    user = _get_user(request)
    require_root(user)

    from backend.rbac import token_auth
    from backend.rbac.auth import _get_db

    db = await _get_db()
    revoked = await token_auth.revoke_service_token(db, token_id)
    logger.info(
        "admin: service token '%s' revogado por %s (revoked=%s)",
        token_id,
        user.id,
        revoked,
    )
    return {"revoked": revoked}
