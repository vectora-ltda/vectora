"""Autorização e invocação única das tools nativas de mídia."""

from __future__ import annotations

from typing import Any

from backend.rbac.tool_policy import is_allowed
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolSpec

MEDIA_TOOL_NAMES = frozenset(
    {"generate_image", "text_to_speech", "generate_video", "analyze_video"}
)


def media_specs() -> list[ToolSpec]:
    """Return the registered media specs without invoking providers."""
    import backend.tools.media

    return [
        spec
        for name in MEDIA_TOOL_NAMES
        if (spec := TOOL_REGISTRY.get(name)) is not None
    ]


async def invoke_media(
    name: str, arguments: dict[str, Any], context: ToolContext
) -> str:
    """Validate policy, arguments and invoke one registered media tool."""
    if name not in MEDIA_TOOL_NAMES:
        return f"Error: tool de mídia desconhecida: {name}"
    if not is_allowed(context.user_id, name):
        return f"Error: tool de mídia desabilitada: {name}"
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return f"Error: tool de mídia indisponível: {name}"
    return await spec.ainvoke(arguments, context)
