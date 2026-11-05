"""Estado escopado por workspace/usuário.

``workspace.py`` (registro de workspaces isolados por diretório),
``workspace_config.py`` (parser de ``.vectora/config.toml``),
``profiles.py`` (preferências de usuário), ``runtime_settings.py``
(configuração mutável em runtime), ``plugins.py`` (registro de servidores
MCP por usuário) e ``skills.py`` (registro de skills por usuário).

Lazy imports (``__getattr__``) — mesmo padrão de ``backend.mcp`` — evitam
puxar filesystem watchers/TOML parsing no import do pacote quando só um
submódulo específico é necessário.
"""

from __future__ import annotations

__all__ = [
    "runtime_settings",
    "workspace_registry",
]


def __getattr__(name: str) -> object:
    if name == "workspace_registry":
        from backend.workspace.workspace import workspace_registry

        return workspace_registry
    if name == "runtime_settings":
        from backend.workspace.runtime_settings import runtime_settings

        return runtime_settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
