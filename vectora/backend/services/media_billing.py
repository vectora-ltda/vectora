"""Resolve a origem confiável da cobrança de operações de mídia."""

from __future__ import annotations

from backend.settings import PROVIDER_API_KEY_ENV
from backend.vtypes.context import VectoraContext


async def resolve_media_billing_source(user_id: str, model: str) -> str | None:
    """Retorna ``"byok"`` quando o usuário tem uma chave de provider própria.

    A decisão usa somente o override persistido para o usuário autenticado e o
    provider do modelo ativo. Nunca lê argumentos de tool nem aceita um valor
    informado pelo modelo.
    """
    if not user_id or user_id == "local":
        return None

    provider = model.partition(":")[0].replace("_", "-")
    env_name = PROVIDER_API_KEY_ENV.get(provider)
    if not env_name:
        return None

    try:
        from backend.rbac.auth import get_env_overrides

        overrides = await get_env_overrides(user_id)
    except Exception:
        return None

    credential = overrides.get(env_name)
    return "byok" if isinstance(credential, str) and credential.strip() else None


async def apply_media_billing_source(ctx: VectoraContext) -> None:
    """Atualiza o marcador interno do contexto antes da execução das tools."""
    ctx._extra.pop("media_billing_source", None)
    source = await resolve_media_billing_source(ctx.user_id, ctx.model)
    if source is not None:
        ctx._extra["media_billing_source"] = source
