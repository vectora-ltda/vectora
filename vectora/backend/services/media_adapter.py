"""Autorização e invocação única das tools nativas de mídia."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.rbac.tool_policy import is_allowed
from backend.settings import settings
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolSpec

MEDIA_TOOL_NAMES = frozenset(
    {"generate_image", "text_to_speech", "generate_video", "analyze_video"}
)


def _validated_video_path(path: str, context: ToolContext) -> str | None:
    """Accept video input only from the current thread's media directory."""
    if not context.thread_id:
        return None
    root = (settings.vectora_home / "artifacts" / context.thread_id / "media").resolve()
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or resolved.is_symlink():
        return None
    return str(resolved)


def media_specs(user_id: str = "local") -> list[ToolSpec]:
    """Return only media specs allowed for the authenticated principal."""
    import backend.tools.media

    return [
        spec
        for name in MEDIA_TOOL_NAMES
        if is_allowed(user_id, name) and (spec := TOOL_REGISTRY.get(name)) is not None
    ]


async def invoke_media(
    name: str, arguments: dict[str, Any], context: ToolContext
) -> str:
    """Validate policy, arguments and invoke one registered media tool."""
    if name not in MEDIA_TOOL_NAMES:
        return f"Error: tool de mídia desconhecida: {name}"
    if not is_allowed(context.user_id, name):
        return f"Error: tool de mídia desabilitada: {name}"
    if name == "analyze_video":
        video_path = _validated_video_path(str(arguments.get("path", "")), context)
        if video_path is None:
            return "Error: vídeo deve estar na mídia da sessão atual e não pode ser symlink."
        arguments = {**arguments, "path": video_path}
    # Importa o módulo que registra as quatro tools antes de consultar o
    # registry; o mesmo bootstrap é usado por media_specs().
    import backend.tools.media

    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return f"Error: tool de mídia indisponível: {name}"
    return await spec.ainvoke(arguments, context)
