"""Derivação confiável da origem de cobrança das operações de mídia."""

from __future__ import annotations

from dataclasses import replace

from backend.settings import PROVIDER_API_KEY_ENV
from backend.tools.context import ToolContext


def _provider_for_context(context: ToolContext) -> str:
    """Retorna o provider ativo normalizado para o mapa de credenciais."""
    raw_provider = context.model.partition(":")[0].strip()
    if not raw_provider:
        try:
            from backend.workspace.runtime_settings import runtime_settings

            raw_provider = runtime_settings.active_provider
        except Exception:
            raw_provider = ""
    normalized = raw_provider.replace("_", "-")
    return normalized if normalized in PROVIDER_API_KEY_ENV else raw_provider


async def apply_media_billing_source(context: ToolContext) -> ToolContext:
    """Marca BYOK somente quando a credencial do usuário é confiável.

    O usuário virtual ``local`` e providers sem credencial externa nunca
    recebem a isenção. A origem anterior também é removida para impedir que
    um contexto reutilizado conserve uma marca BYOK obsoleta.
    """
    extra = dict(context._extra)
    extra.pop("media_billing_source", None)
    if context.user_id == "local":
        return replace(context, _extra=extra)

    provider = _provider_for_context(context)
    key_name = PROVIDER_API_KEY_ENV.get(provider)
    if key_name:
        try:
            from backend.rbac.auth import get_env_overrides

            overrides = await get_env_overrides(context.user_id)
        except Exception:
            overrides = {}
        if overrides.get(key_name):
            extra["media_billing_source"] = "byok"
    return replace(context, _extra=extra)
