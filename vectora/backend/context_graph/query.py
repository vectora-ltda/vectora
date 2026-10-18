"""Serviço compartilhado de query do Context Graph.

Fonte única de verdade para query/explain/path/affected — usado tanto pelas
tools do agente (tools/context_graph.py) quanto pelos handlers REST
(api/handlers/context_graph.py), eliminando duplicação de lógica.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import networkx as nx
from networkx.readwrite import json_graph

from backend.vtypes.context_graph import GraphDocument, GraphEdge, GraphNode

logger = logging.getLogger(__name__)


def load_graph_nx(graph_file: Path) -> nx.DiGraph:
    """Carrega graph.json como DiGraph NetworkX (directed=True).

    Normaliza edges→links para compatibilidade com node_link_graph.
    """
    raw = json.loads(graph_file.read_text(encoding="utf-8"))
    raw = {**raw, "directed": True}
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    try:
        g = json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        g = json_graph.node_link_graph(raw)
    if not isinstance(g, nx.DiGraph):
        g = g.to_directed()
    return g


def _nx_from_data(data: dict[str, Any]) -> nx.DiGraph:
    raw = {**data, "directed": True}
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    try:
        g = json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        g = json_graph.node_link_graph(raw)
    if not isinstance(g, nx.DiGraph):
        g = g.to_directed()
    return g


def query_nodes(
    data: GraphDocument | Mapping[str, Any],
    question: str,
    top_k: int = 10,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Consulta nós do grafo por pergunta livre.

    Tenta resolve_seed primeiro (resolução robusta); fallback para substring de label/id.
    Retorna (matched_nodes, neighborhood_edges).
    """
    from .affected import resolve_seed

    nodes = cast("list[GraphNode]", data.get("nodes", []))
    edges = cast("list[GraphEdge]", data.get("edges", []))

    if not nodes:
        return [], []

    g = _nx_from_data(data)
    seed = resolve_seed(g, question)

    if seed is not None:
        seed_nodes = [n for n in nodes if n.get("id") == seed]
    else:
        q_lower = question.lower()
        seed_nodes = [
            n
            for n in nodes
            if q_lower in str(n.get("label", "")).lower()
            or q_lower in str(n.get("id", "")).lower()
        ][:top_k]

    if not seed_nodes:
        return [], []

    matched_ids = {n.get("id") for n in seed_nodes}
    neighborhood_edges = [
        e
        for e in edges
        if e.get("source") in matched_ids or e.get("target") in matched_ids
    ]
    return seed_nodes, neighborhood_edges


def explain_node(
    data: GraphDocument | Mapping[str, Any],
    node_id: str,
    depth: int = 1,
) -> tuple[GraphNode | None, list[GraphNode], list[GraphEdge]]:
    """Explica um nó mostrando vizinhança e conexões até `depth` saltos.

    Aceita ID exato ou label via resolve_seed.
    Retorna (target_node, neighbor_nodes, connected_edges).
    """
    from .affected import resolve_seed

    nodes = cast("list[GraphNode]", data.get("nodes", []))
    edges = cast("list[GraphEdge]", data.get("edges", []))
    nodes_by_id = {n.get("id"): n for n in nodes if n.get("id")}

    g = _nx_from_data(data)
    resolved = resolve_seed(g, node_id)
    actual_id = resolved if resolved is not None else node_id

    target = nodes_by_id.get(actual_id)
    if target is None:
        return None, [], []

    frontier = {actual_id}
    visited = {actual_id}
    for _ in range(depth):
        new_frontier: set[str] = set()
        for nid in frontier:
            for src, tgt, _ in g.in_edges(nid, data=True):
                if str(src) not in visited:
                    new_frontier.add(str(src))
            for src, tgt, _ in g.out_edges(nid, data=True):
                if str(tgt) not in visited:
                    new_frontier.add(str(tgt))
        visited |= new_frontier
        frontier = new_frontier

    neighbor_ids = visited - {actual_id}
    connected_edges = [
        e
        for e in edges
        if (e.get("source") in visited and e.get("target") in visited)
        and (e.get("source") == actual_id or e.get("target") == actual_id)
    ]
    neighbor_nodes = [nodes_by_id[nid] for nid in neighbor_ids if nid in nodes_by_id]

    return target, neighbor_nodes, connected_edges


def path_between(
    data: dict[str, Any],
    source: str,
    target: str,
) -> tuple[list[dict], list[dict]]:
    """Caminho mais curto entre dois nós. Aceita IDs ou labels via resolve_seed.

    Retorna (path_nodes, path_edges). Retorna ([], []) se caminho não existe.
    """
    from .affected import resolve_seed

    nodes: list[dict] = data.get("nodes", [])
    edges: list[dict] = data.get("edges", [])

    g = _nx_from_data(data)

    src_id = resolve_seed(g, source) or (source if source in g else None)
    tgt_id = resolve_seed(g, target) or (target if target in g else None)

    if src_id is None or tgt_id is None:
        return [], []

    try:
        ug = g.to_undirected()
        path_ids: list[str] = nx.shortest_path(ug, src_id, tgt_id)
    except (nx.NetworkXNoPath, nx.NodeNotFound, nx.NetworkXError):
        return [], []

    path_id_set = set(path_ids)
    path_nodes = [n for n in nodes if n.get("id") in path_id_set]
    path_edges = [
        e
        for e in edges
        if e.get("source") in path_id_set and e.get("target") in path_id_set
    ]
    return path_nodes, path_edges


def affected_summary(
    data: dict[str, Any],
    node_query: str,
    depth: int = 2,
) -> str:
    """Formata texto de impacto de mudança em um nó para o LLM/API.

    Usa resolve_seed + affected_nodes BFS. Retorna texto estruturado.
    """
    from .affected import format_affected

    g = _nx_from_data(data)
    return format_affected(g, node_query, depth=depth)
