"""Versioned skill lockfile and dependency resolution primitives."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import cast

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_LOCKS: dict[Path, Lock] = {}
_LOCKS_GUARD = Lock()


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> Version:
        match = _SEMVER.fullmatch(value.strip())
        if not match:
            raise ValueError(f"versão SemVer inválida: {value}")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def satisfies(version: Version, constraint: str) -> bool:
    """Support exact versions and common caret/tilde ranges."""
    value = constraint.strip()
    if value.startswith("^"):
        base = Version.parse(value[1:])
        if base.major > 0:
            upper = Version(base.major + 1, 0, 0)
        elif base.minor > 0:
            upper = Version(0, base.minor + 1, 0)
        else:
            upper = Version(0, 0, base.patch + 1)
        return base <= version < upper
    if value.startswith("~"):
        base = Version.parse(value[1:])
        return (
            version >= base
            and version.major == base.major
            and version.minor == base.minor
        )
    return version == Version.parse(value)


def resolve_dependencies(
    candidates: Mapping[str, object],
) -> list[str]:
    """Resolve uma versão determinística por skill e rejeita conflitos/ciclos."""
    resolved: list[str] = []
    visiting: list[str] = []
    selected: dict[str, tuple[str, dict[str, str]]] = {}
    constraints: dict[str, list[tuple[str, str]]] = {}

    def options(skill_id: str) -> list[tuple[Version, tuple[str, dict[str, str]]]]:
        raw = candidates.get(skill_id)
        if raw is None:
            raise ValueError(f"dependência ausente: {skill_id}")
        values = (
            [cast("tuple[str, dict[str, str]]", raw)]
            if isinstance(raw, tuple)
            else cast("list[tuple[str, dict[str, str]]]", raw)
        )
        return sorted(
            (
                (Version.parse(version), (version, requirements))
                for version, requirements in values
            ),
            reverse=True,
        )

    def choose(skill_id: str) -> tuple[str, dict[str, str]]:
        if skill_id in selected:
            version = Version.parse(selected[skill_id][0])
            if all(
                satisfies(version, constraint)
                for _, constraint in constraints.get(skill_id, [])
            ):
                return selected[skill_id]
            selected.pop(skill_id)
        required = constraints.get(skill_id, [])
        for version, candidate in options(skill_id):
            if all(satisfies(version, constraint) for _, constraint in required):
                selected[skill_id] = candidate
                return candidate
        details = ", ".join(
            f"{source}: {constraint}" for source, constraint in required
        )
        raise ValueError(f"versão incompatível: {skill_id} ({details})")

    def visit(skill_id: str) -> None:
        if skill_id in visiting:
            cycle = " -> ".join([*visiting, skill_id])
            raise ValueError(f"ciclo de skills: {cycle}")
        if skill_id in resolved:
            return
        version, requirements = choose(skill_id)
        Version.parse(version)
        visiting.append(skill_id)
        for dependency, constraint in sorted(requirements.items()):
            constraints.setdefault(dependency, []).append((skill_id, constraint))
            choose(dependency)
            visit(dependency)
        visiting.pop()
        if skill_id not in resolved:
            resolved.append(skill_id)

    for skill_id in sorted(candidates):
        visit(skill_id)
    return resolved


def write_lockfile(path: Path, entries: dict[str, dict[str, object]]) -> None:
    """Write a stable, versioned lockfile atomically."""
    validate_lock_entries(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(path.resolve(), Lock())
    payload = {
        "format_version": 1,
        "skills": {key: entries[key] for key in sorted(entries)},
    }
    with lock:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            )
            temporary.flush()
            import os

            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def validate_lock_entries(entries: dict[str, dict[str, object]]) -> None:
    """Valida entradas sem executar conteúdo das skills."""
    allowed = {"version", "source", "revision", "integrity", "requires_skills"}
    for skill_id, entry in entries.items():
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("id de skill inválido no lockfile")
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise ValueError("entrada de skill inválida no lockfile")
        version = entry.get("version")
        if not isinstance(version, str):
            raise ValueError(f"versão ausente para skill {skill_id}")
        Version.parse(version)
        for field in ("source", "revision", "integrity"):
            value = entry.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field} inválido para skill {skill_id}")
        requirements = entry.get("requires_skills", {})
        if not isinstance(requirements, dict) or any(
            not isinstance(dep, str)
            or not isinstance(constraint, str)
            or not constraint.strip()
            for dep, constraint in requirements.items()
        ):
            raise ValueError(f"dependências inválidas para skill {skill_id}")


def read_lockfile(path: Path) -> dict[str, object]:
    """Read and validate the lockfile shape without executing skill content."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("format_version") != 1
        or not isinstance(payload.get("skills"), dict)
    ):
        raise ValueError("skills.lock.json inválido")
    validate_lock_entries(payload["skills"])
    return payload
