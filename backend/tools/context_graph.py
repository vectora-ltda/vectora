"""Tools do Context Graph para o agente.

Cada tool é defensiva (§11): captura exceção e devolve string tipada — nunca
propaga. Registradas em nodes/tools.py (modo Dev).

Toda a lógica de query/explain/path/affected vive em services/context_graph/query.py
(fonte única de verdade), compartilhada com api/handlers/context_graph.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


def _load_graph_data(workspace_id: str) -> tuple[dict | None, str | None]:
    """Carrega graph.json do workspace. Retorna (data, error_msg)."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return None, "Workspace não encontrado."
    graph_file = Path(ws.cwd) / ".vectora/context-graph/graph.json"
    if not graph_file.exists():
        return None, "Grafo não encontrado. Execute build_knowledge_graph primeiro."
    try:
        return json.loads(graph_file.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"Falha ao ler graph.json: {exc}"


@vtool(extras=ToolExtras())
async def build_knowledge_graph(
    ctx: ToolContext,
    model: str = "",
    mode: str = "semantic",
) -> str:
    """Constrói o grafo de contexto do workspace ativo (build completo).

    Extrai nós (funções, classes, conceitos), arestas (calls, imports, references)
    e produz um relatório com god nodes, conexões surpreendentes e perguntas sugeridas.

    Args:
        model: modelo LLM no formato "provider:model" (vazio = padrão do sistema).
        mode: "semantic" (AST + LLM) ou "ast" (só árvore sintática, sem LLM).

    Returns:
        Resumo do grafo com contagem de nós/arestas, god nodes e próximos passos.
    """
    try:
        if not ctx.workspace_id:
            return "Erro: nenhum workspace ativo. Abra um workspace primeiro."

        from backend.context_graph.pipeline import build_workspace_graph

        result = await build_workspace_graph(ctx.workspace_id, model=model, mode=mode)
        if result.error:
            return f"Erro no build do grafo: {result.error}"

        lines = [
            f"Grafo construído: {result.node_count} nós, {result.edge_count} arestas."
        ]
        if result.god_nodes:
            lines.append(
                f"God nodes (mais conectados): {', '.join(result.god_nodes[:5])}."
            )
        if result.suggested_questions:
            lines.append("Perguntas sugeridas:")
            lines.extend(f"  - {q}" for q in result.suggested_questions[:3])
        lines.append(f"Relatório em: {result.report_path}")
        return "\n".join(lines)

    except Exception as exc:
        logger.exception("graph: falha em build_knowledge_graph")
        return f"Erro ao construir o grafo: {exc}"


@vtool(extras=ToolExtras())
async def graph_update(ctx: ToolContext, model: str = "") -> str:
    """Atualiza o grafo de contexto incrementalmente (só arquivos novos/modificados).

    Mais rápido que build_knowledge_graph para workspaces grandes porque compara
    o manifesto SHA256 anterior com o estado atual — reprocessa só o que mudou.

    Args:
        model: modelo LLM no formato "provider:model" (vazio = padrão do sistema).

    Returns:
        Resumo: nós/arestas no grafo atualizado e god nodes.
    """
    try:
        if not ctx.workspace_id:
            return "Erro: nenhum workspace ativo. Abra um workspace primeiro."

        from backend.context_graph.pipeline import build_workspace_graph

        result = await build_workspace_graph(
            ctx.workspace_id, model=model, mode="semantic", update=True
        )
        if result.error:
            return f"Erro na atualização do grafo: {result.error}"

        lines = [
            f"Grafo atualizado: {result.node_count} nós, {result.edge_count} arestas."
        ]
        if result.god_nodes:
            lines.append(f"God nodes: {', '.join(result.god_nodes[:5])}.")
        return "\n".join(lines)

    except Exception as exc:
        logger.exception("graph: falha em graph_update")
        return f"Erro ao atualizar o grafo: {exc}"


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="workspace",
        destructive=True,
        icon="square-x",
    )
)
async def graph_cancel_build(ctx: ToolContext) -> str:
    """Cancela um build (ou update) do Context Graph em andamento, disparado
    via ``POST /workspaces/{id}/context-graph/build`` (REST, ex.: pelo
    usuário na aba Context Graph).

    Não cancela um build rodando dentro desta mesma chamada de
    ``build_knowledge_graph``/``graph_update`` (essas rodam síncronas até o
    fim, sem uma task em segundo plano para cancelar).

    Returns:
        Confirmação de cancelamento, ou aviso claro se não havia build
        em andamento neste workspace.
    """
    try:
        if not ctx.workspace_id:
            return "Erro: nenhum workspace ativo. Abra um workspace primeiro."

        from backend.api.handlers.context_graph import _active_builds, _graph_dir

        task = _active_builds.pop(ctx.workspace_id, None)
        if task is None or task.done():
            return "Nenhum build em andamento neste workspace."
        task.cancel()

        d = _graph_dir(ctx.workspace_id)
        if d is not None:
            status_file = d / "build_status.json"
            if status_file.exists():
                status_file.unlink()
        return "Build cancelado."

    except Exception as exc:
        logger.exception("graph: falha em graph_cancel_build")
        return f"Erro ao cancelar o build: {exc}"


@vtool(extras=ToolExtras())
async def graph_query(question: str, ctx: ToolContext, top_k: int = 10) -> str:
    """Consulta o grafo de contexto por pergunta livre.

    Retorna nós relevantes e sua vizinhança, consumindo muito menos tokens do
    que ler os arquivos brutos (71x menos em média). Usa resolve_seed para
    resolução robusta antes de fallback para substring.

    Args:
        question: pergunta ou termo de busca (ex.: "quem chama authenticate?").
        top_k: número máximo de nós retornados na consulta (padrão 10).

    Returns:
        Nós encontrados com seus tipos, labels e arestas de conexão, em texto.
    """
    try:
        if not ctx.workspace_id:
            return "Erro: nenhum workspace ativo."

        data, err = _load_graph_data(ctx.workspace_id)
        if err or data is None:
            return err or "Erro: grafo de contexto não disponível."

        from backend.context_graph.query import query_nodes

        matched, rel_edges = query_nodes(data, question, top_k=top_k)
        if not matched:
            return f"Nenhum nó encontrado para: '{question}'."

        lines = [
            f"Encontrei {len(matched)} nó(s) para '{question}':",
            *[
                f"  [{n.get('file_type', '?')}] {n.get('label', n.get('id', ''))}"
                for n in matched
            ],
        ]
        if rel_edges:
            lines.append(f"\n{len(rel_edges)} arestas relacionadas:")
            lines.extend(
                f"  {e.get('source', '?')} --[{e.get('relation', e.get('label', '?'))}]--> {e.get('target', '?')}"
                for e in rel_edges[:15]
            )
        return "\n".join(lines)

    except Exception as exc:
        logger.exception("graph: falha em graph_query")
        return f"Erro na consulta do grafo: {exc}"


@vtool(extras=ToolExtras())
async def graph_explain(node_id: str, ctx: ToolContext) -> str:
    """Explica um nó do grafo de contexto mostrando sua vizinhança e conexões.

    Args:
        node_id: ID ou label do nó a explicar (ex.: "AuthService", "authenticate").

    Returns:
        Tipo, vizinhos e relações do nó, com o suficiente para entender seu papel.
    """
    try:
        if not ctx.workspace_id:
            return "Erro: nenhum workspace ativo."

        data, err = _load_graph_data(ctx.workspace_id)
        if err or data is None:
            return err or "Erro: grafo de contexto não disponível."

        from backend.context_graph.query import explain_node

        target, neighbors, connected = explain_node(data, node_id)
        if target is None:
            return f"Nó '{node_id}' não encontrado no grafo."

        nid = target.get("id")
        lines = [
            f"Nó: {target.get('label', nid)} (tipo: {target.get('file_type', '?')})"
        ]
        if target.get("docstring"):
            lines.append(f"Docstring: {str(target['docstring'])[:200]}")
        if target.get("rationale"):
            lines.append(f"Rationale: {str(target['rationale'])[:200]}")
        lines.append(f"\n{len(connected)} arestas | {len(neighbors)} vizinhos:")
        for e in connected[:20]:
            direction = "→" if e.get("source") == nid else "←"
            other = e.get("target") if e.get("source") == nid else e.get("source")
            lines.append(
                f"  {direction} [{e.get('relation', e.get('label', '?'))}] {other}"
            )
        return "\n".join(lines)

    except Exception as exc:
        logger.exception("graph: falha em graph_explain")
        return f"Erro ao explicar nó: {exc}"


@vtool(extras=ToolExtras())
async def graph_path(source: str, target: str, ctx: ToolContext) -> str:
    """Encontra o caminho mais curto entre dois nós no grafo de contexto.

    Args:
        source: ID ou label do nó de origem.
        target: ID ou label do nó de destino.

    Returns:
        Sequência de nós que conectam source a target, ou mensagem de erro se não existe.
    """
    try:
        if not ctx.workspace_id:
            return "Erro: nenhum workspace ativo."

        data, err = _load_graph_data(ctx.workspace_id)
        if err or data is None:
            return err or "Erro: grafo de contexto não disponível."

        from backend.context_graph.query import path_between

        path_nodes, _ = path_between(data, source, target)
        if not path_nodes:
            nodes = data.get("nodes", [])

            def _exists(q: str) -> bool:
                ql = q.lower()
                return any(
                    n.get("id") == q or ql in str(n.get("label", "")).lower()
                    for n in nodes
                )

            if not _exists(source):
                return f"Nó '{source}' não encontrado no grafo."
            if not _exists(target):
                return f"Nó '{target}' não encontrado no grafo."
            return f"Não existe caminho entre '{source}' e '{target}'."

        labels = [n.get("label", n.get("id", "?")) for n in path_nodes]
        return f"Caminho ({len(path_nodes)} nós): {' → '.join(labels)}"

    except Exception as exc:
        logger.exception("graph: falha em graph_path")
        return f"Erro ao buscar caminho: {exc}"


@vtool(extras=ToolExtras())
async def graph_affected(node_query: str, ctx: ToolContext, depth: int = 2) -> str:
    """Encontra todos os componentes afetados se o nó consultado mudar.

    Percorre as arestas de dependência (calls, imports, inherits, etc.) no grafo
    de contexto por BFS até a profundidade indicada para identificar o impacto
    em cascata de uma mudança.

    Args:
        node_query: ID, label ou nome parcial do nó a analisar.
        depth: profundidade de propagação (default 2).

    Returns:
        Lista de componentes afetados com relação e localização no código.
    """
    try:
        if not ctx.workspace_id:
            return "Erro: nenhum workspace ativo."

        data, err = _load_graph_data(ctx.workspace_id)
        if err or data is None:
            return err or "Erro: grafo de contexto não disponível."

        from backend.context_graph.query import affected_summary

        return affected_summary(data, node_query, depth=depth)

    except Exception as exc:
        logger.exception("graph: falha em graph_affected")
        return f"Erro ao calcular nós afetados: {exc}"
