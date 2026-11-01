"""Deterministic package-manifest ingestion.

Package manifests (apm.yml, pyproject.toml, go.mod, pom.xml) declare a package
and its dependencies. Left to the LLM document path, the same package gets a
different file-anchored node id from its own manifest than from each dependent's
dependency reference, so it splits into duplicate nodes. This module parses
manifests deterministically and emits ONE canonical package node per package,
keyed by NAME, plus depends_on edges.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from defusedxml import ElementTree

from .ids import make_id

__all__ = [
    "PACKAGE_MANIFEST_NAMES",
    "extract_package_manifest",
    "is_package_manifest_path",
]

PACKAGE_MANIFEST_NAMES: dict[str, str] = {
    "apm.yml": "apm",
    "apm.yaml": "apm",
    "pyproject.toml": "python",
    "go.mod": "go",
    "pom.xml": "maven",
}

_MAX_MANIFEST_BYTES = 2_000_000


def is_package_manifest_path(path: Path) -> bool:
    return path.name.lower() in PACKAGE_MANIFEST_NAMES


def _pkg_id(name: str) -> str:
    return make_id("pkg", name)


def extract_package_manifest(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            return {"nodes": [], "edges": [], "error": "manifest too large to index"}
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"nodes": [], "edges": [], "error": f"manifest read error: {exc}"}

    eco = PACKAGE_MANIFEST_NAMES[path.name.lower()]
    try:
        info = _PARSERS[eco](text)
    except Exception as exc:
        return {"nodes": [], "edges": [], "error": f"manifest parse error: {exc}"}
    if not info or not info.get("name"):
        return {"nodes": [], "edges": []}

    name = info["name"]
    str_path = str(path)
    pkg_nid = _pkg_id(name)
    node: dict[str, Any] = {
        "id": pkg_nid,
        "label": name,
        "file_type": "code",
        "type": "package",
        "ecosystem": eco,
        "source_file": str_path,
        "source_location": "L1",
    }
    if info.get("version"):
        node["version"] = info["version"]
    nodes: list[dict] = [node]
    edges: list[dict] = []

    seen: set[str] = set()
    for dep in info.get("deps", []):
        if not dep:
            continue
        dep_nid = _pkg_id(dep)
        if dep_nid == pkg_nid or dep_nid in seen:
            continue
        seen.add(dep_nid)
        edges.append(
            {
                "source": pkg_nid,
                "target": dep_nid,
                "relation": "depends_on",
                "context": "dependency",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": str_path,
                "source_location": "L1",
                "weight": 1.0,
            }
        )
    return {"nodes": nodes, "edges": edges}


def _coerce_deps(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and item:
                out.append(str(next(iter(item))))
        return out
    return []


def _parse_apm(text: str) -> dict | None:
    try:
        import yaml
    except ImportError:
        return _parse_apm_fallback(text)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return None
    return {
        "name": data.get("name"),
        "version": data.get("version"),
        "deps": _coerce_deps(data.get("dependencies")),
    }


def _parse_apm_fallback(text: str) -> dict | None:
    name = None
    deps: list[str] = []
    in_deps = False
    for line in text.splitlines():
        if not in_deps:
            m = re.match(r'^name:\s*["\']?([^"\'\s#]+)', line)
            if m:
                name = m.group(1)
                continue
        if re.match(r"^dependencies:\s*$", line):
            in_deps = True
            continue
        if in_deps:
            dm = re.match(r'^\s*-\s*["\']?([^"\'\s#:]+)', line) or re.match(
                r"^\s{2,}([A-Za-z0-9._/@-]+)\s*:", line
            )
            if dm:
                deps.append(dm.group(1))
            elif re.match(r"^\S", line):
                in_deps = False
    return {"name": name, "version": None, "deps": deps} if name else None


def _pep508_name(spec: str) -> str:
    return re.split(r"[\s<>=!~;\[\(]", spec.strip(), maxsplit=1)[0]


def _parse_pyproject(text: str) -> dict | None:
    try:
        import tomllib as _toml
    except ImportError:
        try:
            import tomli as _toml
        except ImportError:
            return None
    data = _toml.loads(text)
    proj = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    poetry = (
        (data.get("tool", {}) or {}).get("poetry", {})
        if isinstance(data.get("tool"), dict)
        else {}
    )
    name = proj.get("name") or (
        poetry.get("name") if isinstance(poetry, dict) else None
    )
    if not name:
        return None
    deps: list[str] = [
        _pep508_name(s) for s in (proj.get("dependencies") or []) if isinstance(s, str)
    ]
    if isinstance(poetry, dict):
        for dep in poetry.get("dependencies") or {}:
            if str(dep).lower() != "python":
                deps.append(str(dep))
    return {
        "name": name,
        "version": proj.get("version")
        or (poetry.get("version") if isinstance(poetry, dict) else None),
        "deps": deps,
    }


def _parse_gomod(text: str) -> dict | None:
    name = None
    deps: list[str] = []
    in_block = False
    for line in text.splitlines():
        s = line.strip()
        if name is None:
            m = re.match(r"^module\s+(\S+)", s)
            if m:
                name = m.group(1)
                continue
        if re.match(r"^require\s*\(", s):
            in_block = True
            continue
        if in_block:
            if s.startswith(")"):
                in_block = False
                continue
            dm = re.match(r"^(\S+)\s+v\S+", s)
            if dm:
                deps.append(dm.group(1))
        else:
            dm = re.match(r"^require\s+(\S+)\s+v\S+", s)
            if dm:
                deps.append(dm.group(1))
    return {"name": name, "version": None, "deps": deps} if name else None


def _parse_pom(text: str) -> dict | None:
    text = re.sub(r'\sxmlns="[^"]*"', "", text, count=1)
    root = ElementTree.fromstring(text)
    aid = root.findtext("artifactId")
    gid = root.findtext("groupId")
    if not aid:
        return None
    name = f"{gid}:{aid}" if gid else aid
    deps: list[str] = []
    for dep in root.findall(".//dependencies/dependency"):
        da = dep.findtext("artifactId")
        dg = dep.findtext("groupId")
        if da:
            deps.append(f"{dg}:{da}" if dg else da)
    return {"name": name, "version": root.findtext("version"), "deps": deps}


_PARSERS = {
    "apm": _parse_apm,
    "python": _parse_pyproject,
    "go": _parse_gomod,
    "maven": _parse_pom,
}
