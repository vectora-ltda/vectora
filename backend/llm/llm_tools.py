"""Cache de LLM bindado por usuário — invalidado em troca de tools/policy.

O deep-agent binda as tools internamente em ``create_deep_agent``; este cache
existe para que ``agent_factory`` possa invalidar entradas por usuário quando a
config MCP (``plugins.tools_version``) ou a política de tools
(``tool_policy.policy_version``) mudam, sem reiniciar o processo.
"""

from __future__ import annotations

from typing import Any

#: cache: (user_id, mcp_version, policy_version) -> LLM bindado
_bound_cache: dict[tuple[str, int, int], Any] = {}
