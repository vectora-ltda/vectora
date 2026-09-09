"""Versioned skill lockfile and dependency resolution primitives."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


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
    candidates: dict[str, tuple[str, dict[str, str]]],
) -> list[str]:
    """Resolve a deterministic dependency graph and reject missing/cycles."""
    resolved: list[str] = []
    visiting: list[str] = []

    def visit(skill_id: str) -> None:
        if skill_id in visiting:
            cycle = " -> ".join([*visiting, skill_id])
            raise ValueError(f"ciclo de skills: {cycle}")
        if skill_id in resolved:
            return
        candidate = candidates.get(skill_id)
        if candidate is None:
            raise ValueError(f"dependência ausente: {skill_id}")
        version, requirements = candidate
        parsed = Version.parse(version)
        visiting.append(skill_id)
        for dependency, constraint in sorted(requirements.items()):
            dep = candidates.get(dependency)
            if dep is None:
                raise ValueError(f"dependência ausente: {skill_id} requer {dependency}")
            if not satisfies(Version.parse(dep[0]), constraint):
                raise ValueError(
                    f"versão incompatível: {skill_id} requer {dependency} {constraint}"
                )
            visit(dependency)
        visiting.pop()
        if skill_id not in resolved:
            resolved.append(skill_id)

    for skill_id in sorted(candidates):
        visit(skill_id)
    return resolved


def write_lockfile(path: Path, entries: dict[str, dict[str, object]]) -> None:
    """Write a stable, versioned lockfile atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": 1,
        "skills": {key: entries[key] for key in sorted(entries)},
    }
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
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def read_lockfile(path: Path) -> dict[str, object]:
    """Read and validate the lockfile shape without executing skill content."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format_version") != 1 or not isinstance(
        payload.get("skills"), dict
    ):
        raise ValueError("skills.lock.json inválido")
    return payload
