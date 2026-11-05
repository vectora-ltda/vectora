"""Sanitizador de fragmentos semânticos do Context Graph.

Porta de context graph/semantic_cleanup.py (MIT, Safi Shamsi). Remove nós-prosa do
LLM (rationale/concept sentence-like) antes de entrar no grafo. Chamado em
semantic.py após _parse_llm_json para garantir qualidade do grafo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_RATIONALE_MIN_CHARS = 80
_RATIONALE_MIN_WORDS = 8

MAX_SEMANTIC_FRAGMENT_BYTES = 25 * 1024 * 1024
MAX_SEMANTIC_FRAGMENT_NODES = 10_000
MAX_SEMANTIC_FRAGMENT_EDGES = 100_000
MAX_SEMANTIC_FRAGMENT_HYPEREDGES = 10_000
MAX_SEMANTIC_HYPEREDGE_NODES = 256
MAX_SEMANTIC_ID_LENGTH = 256
VALID_SEMANTIC_FILE_TYPES = frozenset(
    {"code", "document", "paper", "image", "rationale", "concept"}
)
_SEMANTIC_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def validate_semantic_fragment(fragment: object) -> list[str]:
    """Retorna erros de validação para um fragmento semântico não-confiável.

    Lista vazia = válido. Rejeita JSON malformado, IDs inválidos, tamanhos
    acima dos limites e file_types não reconhecidos.
    """
    if not isinstance(fragment, dict):
        return ["fragment must be a JSON object"]

    errors: list[str] = []
    try:
        payload = json.dumps(fragment, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return [f"fragment is not JSON-serializable: {exc}"]

    if len(payload) > MAX_SEMANTIC_FRAGMENT_BYTES:
        errors.append(
            f"payload is {len(payload)} bytes; max is {MAX_SEMANTIC_FRAGMENT_BYTES}"
        )

    nodes = fragment.get("nodes", [])
    edges = fragment.get("edges", [])
    if not isinstance(nodes, list):
        errors.append("nodes must be a list")
        nodes = []
    elif len(nodes) > MAX_SEMANTIC_FRAGMENT_NODES:
        errors.append(
            f"nodes has {len(nodes)} entries; max is {MAX_SEMANTIC_FRAGMENT_NODES}"
        )

    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []
    elif len(edges) > MAX_SEMANTIC_FRAGMENT_EDGES:
        errors.append(
            f"edges has {len(edges)} entries; max is {MAX_SEMANTIC_FRAGMENT_EDGES}"
        )

    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"nodes[{i}] must be an object")
            continue
        _validate_semantic_id(errors, f"nodes[{i}].id", node.get("id"))
        file_type = node.get("file_type")
        if file_type is not None and file_type not in VALID_SEMANTIC_FILE_TYPES:
            errors.append(
                f"nodes[{i}].file_type {file_type!r} is not one of "
                f"{sorted(VALID_SEMANTIC_FILE_TYPES)}"
            )

    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"edges[{i}] must be an object")
            continue
        _validate_semantic_id(errors, f"edges[{i}].source", edge.get("source"))
        _validate_semantic_id(errors, f"edges[{i}].target", edge.get("target"))

    hyperedges = fragment.get("hyperedges", [])
    if hyperedges is None:
        hyperedges = []
    if not isinstance(hyperedges, list):
        errors.append("hyperedges must be a list")
    else:
        if len(hyperedges) > MAX_SEMANTIC_FRAGMENT_HYPEREDGES:
            errors.append(
                f"hyperedges has {len(hyperedges)} entries; "
                f"max is {MAX_SEMANTIC_FRAGMENT_HYPEREDGES}"
            )
        for i, he in enumerate(hyperedges):
            if not isinstance(he, dict):
                errors.append(f"hyperedges[{i}] must be an object")
                continue
            _validate_semantic_id(errors, f"hyperedges[{i}].id", he.get("id"))
            he_nodes = he.get("nodes")
            if not isinstance(he_nodes, list):
                errors.append(f"hyperedges[{i}].nodes must be a list")
                continue
            if len(he_nodes) > MAX_SEMANTIC_HYPEREDGE_NODES:
                errors.append(
                    f"hyperedges[{i}].nodes has {len(he_nodes)} entries; "
                    f"max is {MAX_SEMANTIC_HYPEREDGE_NODES}"
                )
            for j, ref in enumerate(he_nodes):
                _validate_semantic_id(errors, f"hyperedges[{i}].nodes[{j}]", ref)

    return errors


def load_validated_semantic_fragment(path: Path) -> tuple[dict | None, list[str]]:
    """Carrega e valida fragmento semântico, rejeitando arquivos gigantes antes do parse."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, [f"could not stat {path}: {exc}"]
    if size > MAX_SEMANTIC_FRAGMENT_BYTES:
        return None, [f"payload is {size} bytes; max is {MAX_SEMANTIC_FRAGMENT_BYTES}"]
    try:
        fragment = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc}"]
    except OSError as exc:
        return None, [f"could not read {path}: {exc}"]
    errors = validate_semantic_fragment(fragment)
    return (None, errors) if errors else (fragment, [])


def _validate_semantic_id(errors: list[str], field: str, value: object) -> None:
    if not isinstance(value, str):
        errors.append(f"{field} must be a string")
        return
    if not value:
        errors.append(f"{field} must not be empty")
        return
    if len(value) > MAX_SEMANTIC_ID_LENGTH:
        errors.append(f"{field} is {len(value)} chars; max is {MAX_SEMANTIC_ID_LENGTH}")
    if "/" in value or "\\" in value or ".." in value:
        errors.append(f"{field} must not contain path separators or '..'")
    if not _SEMANTIC_ID_RE.fullmatch(value):
        errors.append(f"{field} contains unsupported characters")


def sanitize_semantic_fragment(fragment: dict) -> dict:
    """Remove nós-prosa do LLM e converte rationale_for em atributos.

    Quatro passes:
    1. Remove nós file_type=rationale|concept sentence-like
    2. Converte nós com aresta rationale_for em atributo rationale do target
    3. Filtra arestas que referenciam nós removidos
    4. Filtra hyperedges com < 2 membros sobreviventes
    """
    _invalid_ft = frozenset({"rationale", "concept"})

    nodes: list[dict] = fragment.get("nodes", [])
    edges: list[dict] = fragment.get("edges", [])
    hyperedges: list[dict] = fragment.get("hyperedges", []) or []

    node_by_id: dict[str, dict] = {}
    for n in nodes:
        nid = n.get("id", "")
        if nid:
            node_by_id[nid] = n

    rationale_for_sources: set[str] = set()
    for e in edges:
        if e.get("relation") == "rationale_for":
            src = e.get("source", "")
            if src:
                rationale_for_sources.add(src)

    rationale_candidates: list[dict] = []
    remove_ids: set[str] = set()
    keep_nodes: list[dict] = []
    for n in nodes:
        nid = n.get("id", "")
        if not nid:
            continue
        ft = n.get("file_type", "")
        label = n.get("label", "")
        if ft in _invalid_ft:
            if _is_sentence_like_rationale_label(label):
                rationale_candidates.append(n)
            remove_ids.add(nid)
            continue
        if nid in rationale_for_sources and _is_sentence_like_rationale_label(label):
            rationale_candidates.append(n)
            remove_ids.add(nid)
            continue
        keep_nodes.append(n)

    rationale_attrs: dict[str, list[str]] = {}
    for rn in rationale_candidates:
        rn_id = rn.get("id", "")
        text = rn.get("label", "").strip()
        for e in edges:
            if e.get("relation") != "rationale_for":
                continue
            if e.get("source") != rn_id:
                continue
            target_id = e.get("target", "")
            if target_id not in node_by_id or target_id in remove_ids:
                continue
            rationale_attrs.setdefault(target_id, []).append(text)

    for target_id, texts in rationale_attrs.items():
        if target_id in node_by_id and target_id not in remove_ids:
            _append_rationale_attr(node_by_id[target_id], texts)

    keep_edges: list[dict] = []
    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src in remove_ids or tgt in remove_ids:
            continue
        keep_edges.append(e)

    surviving_ids: set[str] = {n.get("id", "") for n in keep_nodes}
    surviving_ids.discard("")
    keep_hyperedges: list[dict] = []
    for he in hyperedges:
        if not isinstance(he, dict):
            continue
        he_nodes = he.get("nodes")
        if not isinstance(he_nodes, list):
            continue
        filtered = [
            ref for ref in he_nodes if isinstance(ref, str) and ref in surviving_ids
        ]
        if len(filtered) < 2:
            continue
        if len(filtered) != len(he_nodes):
            he = dict(he)
            he["nodes"] = filtered
        keep_hyperedges.append(he)

    fragment["nodes"] = keep_nodes
    fragment["edges"] = keep_edges
    fragment["hyperedges"] = keep_hyperedges
    return fragment


def _is_sentence_like_rationale_label(label: str) -> bool:
    """Retorna True se label parece prosa/rationale e não um nome de entidade.

    Heurística: ≥80 chars OU ≥8 palavras, E tem pontuação de fim de frase.
    """
    if not label:
        return False
    label = label.strip()
    if len(label) < _RATIONALE_MIN_CHARS:
        word_count = len(label.split())
        if word_count < _RATIONALE_MIN_WORDS:
            return False
    return bool(re.search(r"[.!?:]", label))


def _append_rationale_attr(node: dict, texts: list[str]) -> None:
    existing = node.get("rationale", "")
    new_text = "\n\n".join(texts).strip()
    if existing:
        node["rationale"] = existing + "\n\n" + new_text
    else:
        node["rationale"] = new_text
