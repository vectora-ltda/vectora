"""Mapeamento de nome interno de nó/SOUL → label legível para o usuário.

Usado pelo frontend para mostrar progresso semântico durante o streaming:
  "Analisando..." em vez de "model"
  "Executando ferramentas…" em vez de "tools"

Os nós são os do motor nativo (``backend/engine/conversation_loop.py``): o
nó do modelo, o nó de tools, o agente principal (``vectora``) e as 10 SOULs
do catálogo de delegação (``backend/agents/souls.py::SOUL_CATALOG``).
"""

from __future__ import annotations

NODE_LABELS: dict[str, str] = {
    "model": "Analisando...",
    "tools": "Executando ferramentas…",
    "vectora": "Processando…",
    "coder": "Escrevendo código…",
    "search": "Pesquisando…",
    "reviewer": "Revisando código…",
    "tester": "Escrevendo e rodando testes…",
    "devops": "Mexendo em infraestrutura/CI…",
    "writer-docs": "Escrevendo documentação…",
    "data-analyst": "Analisando dados…",
    "security-auditor": "Auditando segurança…",
    "browser-qa": "Testando no navegador…",
    "planner": "Planejando…",
}

# Labels para quando o agente principal delega a um sub-agent via `task`.
_ROUTING_LABELS: dict[str, str] = {
    "search": "Roteando para busca…",
    "coder": "Roteando para agente de código…",
    "reviewer": "Roteando para revisão…",
    "tester": "Roteando para testes…",
    "devops": "Roteando para DevOps…",
    "writer-docs": "Roteando para documentação…",
    "data-analyst": "Roteando para análise de dados…",
    "security-auditor": "Roteando para auditoria de segurança…",
    "browser-qa": "Roteando para QA no navegador…",
    "planner": "Roteando para planejamento…",
}

_GENERIC_LABEL = "Processando…"


def get_node_label(node: str) -> str:
    """Retorna o label legível para um nó do grafo."""
    return NODE_LABELS.get(node, _GENERIC_LABEL)


def get_routing_label(delegate_to: str) -> str:
    """Retorna o label de roteamento quando o agente delega a um sub-agent."""
    return _ROUTING_LABELS.get(delegate_to, f"Roteando para {delegate_to}…")
