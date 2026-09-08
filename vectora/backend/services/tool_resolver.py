"""Resolução do toolset por usuário.

Combina as tools built-in permitidas (consultando ``tool_policy``) com as tools
dos servidores MCP do usuário (``plugins.get_user_mcp_tools``). Usado pelos
agents (para bindar no LLM) e pelo ToolNode dinâmico (para executar), a cada
request, a partir do ``user_id`` do RunnableConfig.

Sem ``user_id`` (CLI/root local) → ALL_TOOLS direto, sem consulta de política
nem MCP — preserva o comportamento local.
"""

from __future__ import annotations

import logging

from backend.nodes.tools import ALL_TOOL_SPECS
from backend.rbac import tool_policy
from backend.tools.registry import ToolRegistry, ToolSpec
from backend.workspace.plugins import get_user_mcp_tools
from backend.workspace.skills import list_skill_paths

logger = logging.getLogger(__name__)


async def resolve_tools(
    user_id: str | None,
    *,
    workspace_id: str = "",
    project_root: str | None = None,
    runtime_id: str | None = None,
) -> list[ToolSpec]:
    """Retorna o toolset efetivo do usuário (built-ins permitidas + MCP)."""
    if not user_id or user_id == "local":
        return list(ALL_TOOL_SPECS)

    builtins = [t for t in ALL_TOOL_SPECS if tool_policy.is_allowed(user_id, t.name)]

    try:
        if not workspace_id and project_root is None and runtime_id is None:
            mcp_tools = await get_user_mcp_tools(user_id)
        else:
            mcp_tools = await get_user_mcp_tools(
                user_id,
                workspace_id=workspace_id,
                project_root=project_root,
                runtime_id=runtime_id,
            )
    except Exception:
        logger.warning("tool_resolver: MCP indisponível para %s", user_id)
        mcp_tools = []

    # Force resolution of the same scoped skill set used by the agent factory;
    # this keeps the toolset and prompt context on one precedence contract.
    list_skill_paths(
        user_id,
        workspace_id=workspace_id,
        project_root=project_root,
        runtime_id=runtime_id,
    )
    return [*builtins, *mcp_tools]


async def resolve_registry(
    user_id: str | None,
    base: ToolRegistry,
    *,
    workspace_id: str = "",
    project_root: str | None = None,
    runtime_id: str | None = None,
) -> ToolRegistry:
    """Copia um registry nativo e acrescenta MCPs scoped autorizados.

    O registry base é cacheado pelo agente; a cópia evita contaminar outras
    sessões quando servidores MCP são alterados durante um turno.
    """
    registry = ToolRegistry()
    for spec in base.all():
        registry.register(spec)
    if not user_id or user_id == "local":
        return registry
    for spec in await get_user_mcp_tools(
        user_id,
        workspace_id=workspace_id,
        project_root=project_root,
        runtime_id=runtime_id,
    ):
        if spec.name not in registry:
            registry.register(spec)
    return registry
