"""Resolve o spec de uma SOUL específica pelo nome, fora do fluxo síncrono
de delegação do orchestrator (``delegate_to_subagent``).

``backend/scheduling/background_tasks.py::run_task`` usa ``_spec_for`` (via
o catálogo importado direto) para montar um ``ToolRegistry`` isolado só com
as tools da SOUL pedida quando uma task tem ``trigger_config.subagent_type``
— o LLM não tem a opção de responder fora do escopo daquela SOUL.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _spec_for(subagent_type: str) -> Any:
    from backend.agents.souls import SOUL_CATALOG

    soul = SOUL_CATALOG.get(subagent_type)
    if soul is None:
        raise ValueError(
            f"subagent_type inválido: {subagent_type!r}. Válidos: {SUBAGENT_TYPES}"
        )
    return soul


#: Nomes das SOULs do catálogo, espelhados aqui como literal — não pode vir
#: de ``SOUL_CATALOG`` em import-time: ``backend/tools/background.py`` importa
#: este nome no topo do módulo e é importado por ``backend/nodes/tools.py``,
#: que ``souls.py`` importa de volta (ciclo nodes.tools → background →
#: subagent_runner → souls → nodes.tools). Testado contra o catálogo real em
#: ``tests/unit/test_subagent_runner.py``.
SUBAGENT_TYPES = (
    "coder",
    "search",
    "reviewer",
    "tester",
    "devops",
    "writer-docs",
    "data-analyst",
    "security-auditor",
    "browser-qa",
    "planner",
)
