"""Derivação confiável da origem de cobrança das operações de mídia."""

from __future__ import annotations

from backend.settings import PROVIDER_API_KEY_ENV
from backend.tools.context import ToolContext


def _provider_for_model(model: str) -> str:
    """Return the normalized provider prefix from a model identifier."""
    raw_provider = model.partition(":")[0].strip()
    normalized = raw_provider.replace("_", "-")
    return normalized if normalized in PROVIDER_API_KEY_ENV else raw_provider


async def resolve_media_billing_source(user_id: str, model: str) -> str | None:
    """Resolve a trusted BYOK marker for a user and model pair."""
    if user_id == "local":
        return None
    provider = _provider_for_model(model)
    key_name = PROVIDER_API_KEY_ENV.get(provider)
    if not key_name:
        return None
    try:
        from backend.rbac.auth import get_env_overrides

        overrides = await get_env_overrides(user_id)
    except Exception:
        overrides = {}
    return "byok" if overrides.get(key_name, "").strip() else None


async def apply_media_billing_source(context: ToolContext) -> ToolContext:
    """Marca BYOK somente quando a credencial do usuário é confiável.

    O usuário virtual ``local`` e providers sem credencial externa nunca
    recebem a isenção. A origem anterior também é removida para impedir que
    um contexto reutilizado conserve uma marca BYOK obsoleta.
    """
    extra = dict(context._extra)
    extra.pop("media_billing_source", None)
    source = await resolve_media_billing_source(context.user_id, context.model)
    if source is not None:
        extra["media_billing_source"] = source
    context._extra.clear()
    context._extra.update(extra)
    return context
