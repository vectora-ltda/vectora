"""Deterministic symbol indexing and conservative cross-file resolution helpers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ImportedSymbol:
    """A Python imported name that can be used as deterministic resolution evidence."""

    local_name: str
    imported_name: str
    module_stem: str
    source_file: str
    source_location: str


def normalise_callable_label(label: str) -> str:
    """Normalize a node label into the key used for call resolution."""

    return label.strip().strip("()").lstrip(".").lower()


def node_is_resolvable_symbol(node: dict[str, Any]) -> bool:
    """Return True when a node is suitable for deterministic symbol lookup.

    Requires ``file_type == "code"`` as the positive gate — only code-class
    nodes participate as call targets. ``_EXCLUDED_FILE_TYPES`` is kept as
    defensive-in-depth against legacy data, but the primary guard is the
    positive code check. Document/paper/image/concept nodes (e.g. a Markdown
    heading whose label happens to match a code identifier) MUST NOT become
    callees for a raw code call.
    """

    if node.get("file_type") != "code":
        return False
    label = str(node.get("label", "")).strip()
    if not label:
        return False
    if label.endswith((".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs")):
        return False
    return bool(normalise_callable_label(label))


def existing_edge_pairs(edges: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    """Return all existing source/target/relation edge triples.

    Includes relation so that a prior "contains" or "method" edge does not
    suppress a semantically distinct "calls" edge between the same endpoints (#F5).
    """

    triples: set[tuple[str, str, str]] = set()
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        relation = edge.get("relation", "")
        if source and target:
            triples.add((str(source), str(target), str(relation)))
    return triples


def _module_stem(module_name: str | None) -> str:
    """Return the final module component used to match Context Graph source stems."""

    if not module_name:
        return ""
    return module_name.strip(".").split(".")[-1]


def parse_python_import_aliases(path: Path) -> dict[str, ImportedSymbol]:
    """Parse deterministic Python import aliases from one source file.

    Supported forms:
        from helper import transform
        from helper import transform as tx
        from .helper import transform

    The function deliberately does not resolve plain ``import helper`` member
    calls because current raw call records do not preserve the receiver name from
    ``helper.transform()``. That can be added later only after raw call facts are
    extended to include the receiver expression.
    """

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return {}

    aliases: dict[str, ImportedSymbol] = {}
    source_file = str(path)

    # Only top-level `from ... import ...` statements count as file-wide
    # evidence. Nested/function-local imports do NOT — they're only valid
    # inside their lexical scope, and our raw-call records don't currently
    # carry enough scope info to match the import site safely. Walking
    # ast.walk(tree) would incorrectly justify calls in other scopes.
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module_stem = _module_stem(node.module)
        if not module_stem:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            aliases[local_name] = ImportedSymbol(
                local_name=local_name,
                imported_name=alias.name,
                module_stem=module_stem,
                source_file=source_file,
                source_location=f"L{getattr(node, 'lineno', 1)}",
            )

    return aliases


def _node_source_stem(node: dict[str, Any]) -> str:
    """Return the stem of a node's source file."""

    source_file = str(node.get("source_file", ""))
    if not source_file:
        return ""
    return Path(source_file).stem


def build_python_symbol_index(
    nodes: list[dict[str, Any]],
) -> dict[tuple[str, str], list[str]]:
    """Build ``(module_stem, normalized_symbol_name) -> node_ids``.

    This index is stricter than the global label index. It uses both the module
    stem and the symbol label, which allows import evidence to resolve calls that
    global label uniqueness alone cannot safely resolve.
    """

    index: dict[tuple[str, str], list[str]] = {}
    for node in nodes:
        if not node_is_resolvable_symbol(node):
            continue
        source_stem = _node_source_stem(node)
        if not source_stem:
            continue
        label = normalise_callable_label(str(node.get("label", "")))
        if not label:
            continue
        node_id = node.get("id")
        if not node_id:
            continue
        index.setdefault((source_stem, label), []).append(str(node_id))
    return index


def find_unique_python_symbol(
    symbol_index: dict[tuple[str, str], list[str]],
    imported: ImportedSymbol,
) -> str | None:
    """Resolve one imported symbol to exactly one Context Graph node id."""

    candidates = symbol_index.get(
        (imported.module_stem, imported.imported_name.lower()), []
    )
    if len(candidates) == 1:
        return candidates[0]
    return None
