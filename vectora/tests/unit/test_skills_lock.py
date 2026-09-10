from concurrent.futures import ThreadPoolExecutor

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
    entry: dict[str, object] = {
        "version": "1.0.0",
        "source": "local",
        "revision": "r1",
        "integrity": "a" * 64,
    }
    write_lockfile(lock, {"app": entry, "base": {**entry, "version": "1.2.0"}})
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
    assert Version.parse("1.0.0-rc.1") < Version.parse("1.0.0")
    assert Version.parse("1.0.0+build.7") == Version.parse("1.0.0")


def test_resolve_seleciona_maior_candidato_que_satisfaz_todas_as_constraints() -> None:
    candidates = {
        "app": ("1.0.0", {"base": "^1.0.0"}),
        "base": [("1.1.0", {}), ("1.4.0", {}), ("2.0.0", {})],
    }

    assert resolve_dependencies(candidates) == ["base", "app"]


def test_resolve_revisita_dependencias_quando_a_versao_muda() -> None:
    candidates = {
        "a": ("1.0.0", {"b": "^1.0.0"}),
        "c": ("1.0.0", {"b": "^2.0.0"}),
        "b": [
            ("1.0.0", {"x": "1.0.0"}),
            ("2.0.0", {"y": "1.0.0"}),
        ],
        "x": ("1.0.0", {}),
        "y": ("1.0.0", {}),
    }

    with pytest.raises(ValueError, match="versão incompatível"):
        resolve_dependencies(candidates)


def test_lockfile_rejeita_entrada_nula_ou_campos_desconhecidos(tmp_path) -> None:
    path = tmp_path / "skills.lock.json"
    path.write_text(
        '{"format_version": 1, "skills": {"broken": null}}', encoding="utf-8"
    )
    with pytest.raises(ValueError):
        read_lockfile(path)

    with pytest.raises(ValueError):
        write_lockfile(path, {"ok": {"version": "1.0.0", "unknown": True}})

    path.write_text(
        '{"format_version": 1, "skills": {"ok": {'
        '"version": "1.0.0", "source": "local", "revision": "r1", '
        '"integrity": "a", "requires_skills": {"   ": "1.0.0"}}}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="dependências inválidas"):
        read_lockfile(path)


def test_escritores_concorrentes_publicam_lockfile_valido(tmp_path) -> None:
    path = tmp_path / "skills.lock.json"

    def publish(index: int) -> None:
        write_lockfile(
            path,
            {
                f"skill-{index}": {
                    "version": "1.0.0",
                    "source": "local",
                    "revision": str(index),
                    "integrity": "a" * 64,
                }
            },
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(publish, range(8)))

    payload = read_lockfile(path)
    assert payload["format_version"] == 1
    assert isinstance(payload["skills"], dict)
