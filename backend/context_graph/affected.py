"""Resolução de seed e análise de nós afetados no grafo de contexto.

Porta de context graph/affected.py (MIT, Safi Shamsi) adaptada para o namespace
do Vectora: sem dependência de GRAPH_OUT, sem CLI, sem context graph.*.
"""

from __future__ import annotations

import unicodedata
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

DEFAULT_AFFECTED_RELATIONS = (
    "calls",
    "references",
    "imports",
    "imports_from",
    "re_exports",
    "inherits",
    "extends",
    "implements",
    "uses",
    "mixes_in",
    "embeds",
)


@dataclass(frozen=True)
class AffectedHit:
    node_id: str
    depth: int
    via_relation: str


def _node_label(graph: nx.Graph, node_id: str) -> str:
    data = graph.nodes[node_id]
    return str(data.get("label") or node_id)


def _format_location(data: dict) -> str:
    source_file = data.get("source_file") or "-"
    source_location = data.get("source_location")
    if source_location:
        return f"{source_file}:{source_location}"
    return str(source_file)


def _bare_name(label: str) -> str:
    label = _normalize_label(label)
    return label.removesuffix("()")


def _normalize_label(label: str) -> str:
    return unicodedata.normalize("NFC", label).casefold()


def resolve_seed(graph: nx.Graph, query: str) -> str | None:
    """Resolve uma query de texto para um node_id único no grafo.

    Tenta em sequência: ID exato → label normalizado → bare_name (sem "()")
    → source_file → substring de label. Retorna None se ambíguo ou não encontrado.
    """
    if query in graph:
        return query
    query_lower = _normalize_label(query)
    exact_label_matches = [
        str(node_id)
        for node_id, data in graph.nodes(data=True)
        if _normalize_label(str(data.get("label", ""))) == query_lower
    ]
    if len(exact_label_matches) == 1:
        return exact_label_matches[0]
    query_bare = _bare_name(query_lower)
    bare_name_matches = [
        str(node_id)
        for node_id, data in graph.nodes(data=True)
        if _bare_name(str(data.get("label", ""))) == query_bare
    ]
    if len(bare_name_matches) == 1:
        return bare_name_matches[0]
    exact_source_matches = [
        str(node_id)
        for node_id, data in graph.nodes(data=True)
        if _normalize_label(str(data.get("source_file", ""))) == query_lower
    ]
    if len(exact_source_matches) == 1:
        return exact_source_matches[0]
    contains_matches = [
        str(node_id)
        for node_id, data in graph.nodes(data=True)
        if query_lower in _normalize_label(str(data.get("label", "")))
    ]
    if len(contains_matches) == 1:
        return contains_matches[0]
    return None


def affected_nodes(
    graph: nx.Graph,
    seed: str,
    *,
    relations: Iterable[str] = DEFAULT_AFFECTED_RELATIONS,
    depth: int = 2,
) -> list[AffectedHit]:
    """BFS sobre arestas de entrada para encontrar nós afetados por mudança em seed.

    Percorre apenas arestas cujas relações estão em `relations` (calls, imports, etc.)
    até profundidade `depth`. Retorna lista de AffectedHit com distância e relação.
    """
    relation_set = set(relations)
    seen = {seed}
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    hits: list[AffectedHit] = []

    while queue:
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        if isinstance(graph, nx.DiGraph):
            incoming = graph.in_edges(current, data=True)
        else:
            incoming = (
                (source, target, edge_data)
                for source, target, edge_data in graph.edges(data=True)
                if target == current
            )
        for source, _, data in incoming:
            relation = str(data.get("relation", ""))
            if relation not in relation_set:
                continue
            source = str(source)
            if source in seen:
                continue
            seen.add(source)
            hit = AffectedHit(source, current_depth + 1, relation)
            hits.append(hit)
            queue.append((source, current_depth + 1))

    return hits


def format_affected(
    graph: nx.Graph,
    query: str,
    *,
    relations: Iterable[str] = DEFAULT_AFFECTED_RELATIONS,
    depth: int = 2,
) -> str:
    """Formata texto de impacto para o LLM: seed → lista de nós afetados com relação."""
    relation_list = tuple(relations)
    seed = resolve_seed(graph, query)
    if seed is None:
        return f"No unique node match for {query}"

    hits = affected_nodes(graph, seed, relations=relation_list, depth=depth)
    lines = [
        f"Affected nodes for {_node_label(graph, seed)}",
        f"Relations: {', '.join(relation_list)}",
        f"Depth: {depth}",
    ]
    if not hits:
        lines.append("No affected nodes found.")
        return "\n".join(lines)

    for hit in hits:
        data = graph.nodes[hit.node_id]
        lines.append(
            f"- {_node_label(graph, hit.node_id)} [{hit.via_relation}] {_format_location(data)}"
        )
    return "\n".join(lines)


def load_graph(path: Path) -> nx.Graph:
    """Carrega graph.json como DiGraph NetworkX, normalizando edges/links."""
    import json

    from networkx.readwrite import json_graph

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw = {**raw, "directed": True}
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    try:
        return json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        return json_graph.node_link_graph(raw)
