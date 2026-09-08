import pytest

from backend.workspace.skills_lock import (
    Version,
    read_lockfile,
    resolve_dependencies,
    satisfies,
    write_lockfile,
)


def test_resolves_transitive_dependencies_deterministically(tmp_path) -> None:
    candidates = {
        "app": ("1.0.0", {"base": "^1.0.0"}),
        "base": ("1.2.0", {}),
    }
    assert resolve_dependencies(candidates) == ["base", "app"]
    lock = tmp_path / ".vectora" / "skills.lock.json"
    write_lockfile(lock, {"app": {"version": "1.0.0"}, "base": {"version": "1.2.0"}})
    assert read_lockfile(lock)["format_version"] == 1


def test_rejects_invalid_version_missing_dependency_and_cycle() -> None:
    with pytest.raises(ValueError):
        Version.parse("1.0")
    with pytest.raises(ValueError, match="dependência ausente"):
        resolve_dependencies({"app": ("1.0.0", {"missing": "^1.0.0"})})
    with pytest.raises(ValueError, match="ciclo"):
        resolve_dependencies(
            {"a": ("1.0.0", {"b": "1.0.0"}), "b": ("1.0.0", {"a": "1.0.0"})}
        )


def test_semver_constraints_are_strict() -> None:
    assert satisfies(Version.parse("1.2.3"), "^1.0.0")
    assert satisfies(Version.parse("1.2.3"), "~1.2.0")
    assert not satisfies(Version.parse("2.0.0"), "^1.0.0")
