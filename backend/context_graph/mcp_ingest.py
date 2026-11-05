"""mcp_ingest — extrai configurações de servidores MCP para o grafo de contexto.

Lê .mcp.json / claude_desktop_config.json / mcp.json / mcp_servers.json e converte
o mapa mcpServers em nós e arestas compatíveis com o pipeline de extração.

Segurança: valores de variáveis de ambiente NUNCA são lidos, persistidos ou
emitidos — apenas os NOMES viram nós. Args posicionais também não são persistidos.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .ids import make_id as _shared_make_id
from .security import sanitize_label

MCP_CONFIG_FILENAMES: frozenset[str] = frozenset(
    {
        ".mcp.json",
        "claude_desktop_config.json",
        "mcp.json",
        "mcp_servers.json",
    }
)

_MAX_BYTES = 1_048_576  # 1 MiB
_MAX_SERVERS_PER_FILE = 200


def is_mcp_config_path(path: Path) -> bool:
    return path.name in MCP_CONFIG_FILENAMES


def extract_mcp_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            raw = fh.read(_MAX_BYTES + 1)
    except OSError as exc:
        return {"nodes": [], "edges": [], "error": f"mcp_ingest read error: {exc}"}

    if len(raw) > _MAX_BYTES:
        return {"nodes": [], "edges": [], "error": "mcp config too large to index"}

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"nodes": [], "edges": [], "error": f"mcp_ingest decode error: {exc}"}

    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"nodes": [], "edges": [], "error": f"mcp_ingest json error: {exc}"}

    if not isinstance(doc, dict):
        return {"nodes": [], "edges": [], "error": "mcp_ingest: root is not an object"}

    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        nested = doc.get("mcp")
        if isinstance(nested, dict):
            servers = nested.get("servers")
        if not isinstance(servers, dict):
            return {"nodes": [], "edges": [], "error": "mcp_ingest: no mcpServers map"}

    str_path = str(path)
    file_nid = _make_id(str_path)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    seen_edge_keys: set[tuple[str, str, str]] = set()

    _add_node(
        nodes,
        seen_node_ids,
        nid=file_nid,
        label=path.name,
        kind="mcp_config_file",
        source_file=str_path,
        line=1,
    )

    file_stem = _file_stem(path)
    server_count = 0
    for server_name, spec in servers.items():
        if not isinstance(server_name, str) or not server_name:
            continue
        if not isinstance(spec, dict):
            continue
        if server_count >= _MAX_SERVERS_PER_FILE:
            break
        server_count += 1
        _emit_server(
            server_name=server_name,
            spec=spec,
            file_nid=file_nid,
            file_stem=file_stem,
            source_file=str_path,
            nodes=nodes,
            edges=edges,
            seen_node_ids=seen_node_ids,
            seen_edge_keys=seen_edge_keys,
        )

    return {"nodes": nodes, "edges": edges}


def _emit_server(
    *,
    server_name: str,
    spec: dict[str, Any],
    file_nid: str,
    file_stem: str,
    source_file: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seen_node_ids: set[str],
    seen_edge_keys: set[tuple[str, str, str]],
) -> None:
    server_nid = _make_id(file_stem, "mcp_server", server_name)
    _add_node(
        nodes,
        seen_node_ids,
        nid=server_nid,
        label=server_name,
        kind="mcp_server",
        source_file=source_file,
        line=1,
    )
    _add_edge(
        edges,
        seen_edge_keys,
        source=file_nid,
        target=server_nid,
        relation="contains",
        source_file=source_file,
        line=1,
    )

    command = spec.get("command")
    if isinstance(command, str) and command.strip():
        cmd_label = command.strip()
        cmd_nid = _make_id("mcp_command", cmd_label)
        _add_node(
            nodes,
            seen_node_ids,
            nid=cmd_nid,
            label=cmd_label,
            kind="mcp_command",
            source_file=source_file,
            line=1,
        )
        _add_edge(
            edges,
            seen_edge_keys,
            source=server_nid,
            target=cmd_nid,
            relation="references",
            source_file=source_file,
            line=1,
            context="command",
        )

    args = spec.get("args")
    if isinstance(args, list):
        package = _detect_package_from_args(args)
        if package:
            pkg_nid = _make_id("mcp_package", package)
            _add_node(
                nodes,
                seen_node_ids,
                nid=pkg_nid,
                label=package,
                kind="mcp_package",
                source_file=source_file,
                line=1,
            )
            _add_edge(
                edges,
                seen_edge_keys,
                source=server_nid,
                target=pkg_nid,
                relation="references",
                source_file=source_file,
                line=1,
                context="package",
            )

    env = spec.get("env")
    if isinstance(env, dict):
        for env_name in env:
            if not isinstance(env_name, str) or not env_name:
                continue
            env_nid = _make_id("env_var", env_name)
            _add_node(
                nodes,
                seen_node_ids,
                nid=env_nid,
                label=env_name,
                kind="env_var",
                source_file=source_file,
                line=1,
            )
            _add_edge(
                edges,
                seen_edge_keys,
                source=server_nid,
                target=env_nid,
                relation="requires_env",
                source_file=source_file,
                line=1,
            )


_NPM_PKG_RE = re.compile(r"^@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*(?:@[\w.\-+]+)?$")
_PY_MCP_PKG_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]*-mcp(?:-[a-z0-9._-]+)?$|^mcp-[a-z0-9][a-z0-9._-]*$"
)
_ARG_FLAG_RE = re.compile(r"^-{1,2}\w")


def _detect_package_from_args(args: list[Any]) -> str | None:
    for raw in args:
        if not isinstance(raw, str):
            continue
        arg = raw.strip()
        if not arg or _ARG_FLAG_RE.match(arg):
            continue
        if _NPM_PKG_RE.match(arg):
            return _strip_version(arg)
        if _PY_MCP_PKG_RE.match(arg):
            return arg
    return None


def _strip_version(pkg: str) -> str:
    if pkg.startswith("@"):
        version_at = pkg.find("@", 1)
        return pkg if version_at == -1 else pkg[:version_at]
    version_at = pkg.find("@")
    return pkg if version_at == -1 else pkg[:version_at]


def _add_node(
    nodes: list[dict[str, Any]],
    seen: set[str],
    *,
    nid: str,
    label: str,
    kind: str,
    source_file: str,
    line: int,
) -> None:
    if not nid or nid in seen:
        return
    seen.add(nid)
    nodes.append(
        {
            "id": nid,
            "label": sanitize_label(label),
            "file_type": "code",
            "source_file": source_file,
            "source_location": f"L{line}",
            "metadata": {"mcp_kind": kind},
        }
    )


def _add_edge(
    edges: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    *,
    source: str,
    target: str,
    relation: str,
    source_file: str,
    line: int,
    context: str | None = None,
) -> None:
    if not source or not target or source == target:
        return
    key = (source, target, relation)
    if key in seen:
        return
    seen.add(key)
    edge: dict[str, Any] = {
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "source_file": source_file,
        "source_location": f"L{line}",
        "weight": 1.0,
    }
    if context:
        edge["context"] = context
    edges.append(edge)


def _make_id(*parts: str) -> str:
    return _shared_make_id(*parts)


def _file_stem(path: Path) -> str:
    parent = path.parent.name
    if parent and parent not in (".", ""):
        return f"{parent}.{path.stem}"
    return path.stem
