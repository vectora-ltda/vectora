"""Context Graph — grafo de conhecimento/contexto nativo do Vectora.

**Nome.** "Grafo" é ambíguo no mundo de IA (grafos de execução de agente,
grafos de estado, etc.). Este é um **grafo de contexto**: estrutura e relações
extraídas de um corpus (código/docs/papers) — quem chama quem, implements/cites,
god nodes, o "porquê" das decisões. Por isso o nome específico `context_graph`
(e a workbench "Context Graph"), interna e externamente.

**Pipeline.** Extração AST via tree-sitter, build NetworkX, clustering
Leiden/Louvain, analyze, report e export. A extração semântica (pass 3) passa
pelo LLM ativo do Vectora (`load_native_llm` + `agenerate` async em
`semantic.py`), com storage por workspace.

**Relação com o RAG (GraphRAG).** Complementa, não substitui, o RAG vetorial
(LanceDB) do Vectora. O RAG acha trechos por similaridade; o context graph dá
relações estruturais. A união (`graph_query`): vector search acha os nós-semente,
o grafo expande a vizinhança → contexto rico e barato em tokens.

Lazy imports (``__getattr__``) — mesmo padrão de ``backend.mcp`` — evitam
puxar tree-sitter/NetworkX no import do pacote quando só um submódulo
específico é necessário.
"""

from __future__ import annotations

__all__ = [
    "affected_summary",
    "build_workspace_graph",
    "explain_node",
    "path_between",
    "query_nodes",
]


def __getattr__(name: str) -> object:
    if name == "build_workspace_graph":
        from backend.context_graph.pipeline import build_workspace_graph

        return build_workspace_graph
    if name in (
        "query_nodes",
        "explain_node",
        "path_between",
        "affected_summary",
    ):
        from backend.context_graph import query

        return getattr(query, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
