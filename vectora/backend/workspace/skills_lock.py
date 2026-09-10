"""Versioned skill lockfile and dependency resolution primitives."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from threading import Lock
from typing import cast

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_LOCKS: dict[Path, Lock] = {}
_LOCKS_GUARD = Lock()


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> Version:
        match = _SEMVER.fullmatch(value)
        if not match:
            raise ValueError(f"versão SemVer inválida: {value}")
        major, minor, patch, prerelease, _build = match.groups()
        identifiers = tuple(prerelease.split(".")) if prerelease else ()
        if any(
            identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0"
            for identifier in identifiers
        ):
            raise ValueError(f"versão SemVer inválida: {value}")
        return cls(int(major), int(minor), int(patch), identifiers)

    def __lt__(self, other: object) -> bool:  # noqa: PLR0911
        if not isinstance(other, Version):
            return NotImplemented
        base = (self.major, self.minor, self.patch)
        other_base = (other.major, other.minor, other.patch)
        if base != other_base:
            return base < other_base
        if not self.prerelease and not other.prerelease:
            return False
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)

    def __str__(self) -> str:
        suffix = f"-{'.'.join(self.prerelease)}" if self.prerelease else ""
        return f"{self.major}.{self.minor}.{self.patch}{suffix}"


def satisfies(version: Version, constraint: str) -> bool:
    """Support exact versions and common caret/tilde ranges."""
    value = constraint
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
    """Resolve por busca com retrocesso até atingir um ponto fixo."""

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

    def search(
        selected: dict[str, tuple[str, dict[str, str]]],
        constraints: dict[str, list[tuple[str, str]]],
        order: list[str],
    ) -> tuple[list[str], dict[str, tuple[str, dict[str, str]]]] | None:
        pending = sorted(
            skill_id
            for skill_id in (set(candidates) | set(constraints))
            if skill_id not in selected
            or not all(
                satisfies(Version.parse(selected[skill_id][0]), constraint)
                for _, constraint in constraints.get(skill_id, [])
            )
        )
        if not pending:
            return order, selected
        skill_id = pending[0]
        required = constraints.get(skill_id, [])
        last_error: ValueError | None = None
        for _version, candidate in options(skill_id):
            version, requirements = candidate
            if not all(
                satisfies(Version.parse(version), constraint)
                for _, constraint in required
            ):
                continue
            next_selected = dict(selected)
            next_selected[skill_id] = candidate
            next_constraints = {key: list(value) for key, value in constraints.items()}
            cycle = False
            for dependency, constraint in sorted(requirements.items()):
                if dependency == skill_id:
                    cycle = True
                    break
                next_constraints.setdefault(dependency, []).append(
                    (skill_id, constraint)
                )
            if cycle:
                continue
            try:
                result = search(next_selected, next_constraints, [*order, skill_id])
            except ValueError as exc:
                # Esta versão pode tornar uma dependência incompatível; tente
                # o próximo candidato antes de falhar a resolução inteira.
                last_error = exc
                continue
            if result is not None:
                return result
        details = ", ".join(
            f"{source}: {constraint}" for source, constraint in required
        )
        if details:
            raise ValueError(f"versão incompatível: {skill_id} ({details})")
        if last_error is not None:
            raise last_error
        return None

    result = search({}, {}, [])
    if result is None:
        raise ValueError("dependências de skills incompatíveis")
    _order, selected = result
    ordered: list[str] = []
    visiting: set[str] = set()

    def emit(skill_id: str) -> None:
        if skill_id in ordered:
            return
        if skill_id in visiting:
            raise ValueError(f"ciclo de skills: {skill_id}")
        visiting.add(skill_id)
        for dependency in sorted(selected[skill_id][1]):
            if dependency not in selected:
                raise ValueError(f"dependência ausente: {dependency}")
            emit(dependency)
        visiting.remove(skill_id)
        ordered.append(skill_id)

    for skill_id in sorted(selected):
        emit(skill_id)
    return ordered


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
    normalized_ids: dict[str, str] = {}
    for skill_id, entry in entries.items():
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("id de skill inválido no lockfile")
        normalized_id = skill_id.strip()
        if normalized_id in normalized_ids:
            raise ValueError("ids de skill duplicados no lockfile")
        normalized_ids[normalized_id] = skill_id
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise ValueError("entrada de skill inválida no lockfile")
        version = entry.get("version")
        if not isinstance(version, str):
            raise ValueError(f"versão ausente para skill {skill_id}")
        Version.parse(version)
        for field in ("source", "revision", "integrity"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} inválido para skill {skill_id}")
        requirements = entry.get("requires_skills", {})
        if not isinstance(requirements, dict):
            raise ValueError(f"dependências inválidas para skill {skill_id}")
        for dep, constraint in requirements.items():
            if not isinstance(dep, str) or not dep.strip():
                raise ValueError(f"dependências inválidas para skill {skill_id}")
            if not isinstance(constraint, str) or not constraint.strip():
                raise ValueError(f"dependências inválidas para skill {skill_id}")
            try:
                Version.parse(constraint)
            except ValueError as exc:
                raise ValueError(
                    f"dependência não resolvida para skill {skill_id}"
                ) from exc
    for skill_id, entry in entries.items():
        requirements = entry.get("requires_skills", {})
        if not isinstance(requirements, dict):
            raise ValueError(f"dependências inválidas para skill {skill_id}")
        for dependency, constraint in requirements.items():
            normalized_dependency = dependency.strip()
            dependency_entry = entries.get(
                normalized_ids.get(normalized_dependency, "")
            )
            if (
                dependency_entry is None
                or dependency_entry.get("version") != constraint.strip()
            ):
                raise ValueError(
                    f"dependência não fechada para skill {skill_id}: "
                    f"{normalized_dependency}"
                )


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
