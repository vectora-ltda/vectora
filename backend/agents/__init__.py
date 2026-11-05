"""Vectora Agents — catálogo de SOULs (specs de subagent do deep-agent).

O catálogo (nome, descrição, prompt, tools) vive em ``backend.agents.souls``
e é consumido por ``agent_factory._subagent_specs()`` em ``create_deep_agent``.
Import direto de ``backend.agents.souls`` (não deste ``__init__``) — mantém
o carregamento de ``nodes.tools`` (pesado) lazy pra quem só precisa de
``backend.agents._identity`` (ex.: CLI, contexto sem grafo).
"""

from __future__ import annotations
