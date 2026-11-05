"""Tests for src/version.py"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import TypedDict

from backend.version import __version__

_MONOREPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_LINES = _MONOREPO_ROOT / ".github" / "release-lines.json"


class _ReleaseLine(TypedDict):
    branch: str
    milestone: str


class _ReleaseLines(TypedDict):
    version_files: list[str]
    development: _ReleaseLine
    maintenance: _ReleaseLine


def _release_lines() -> _ReleaseLines:
    return json.loads(_RELEASE_LINES.read_text(encoding="utf-8"))


def test_version_is_string() -> None:
    assert isinstance(__version__, str)


def test_version_semver() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", __version__), f"Not semver: {__version__}"


def test_pyproject_version_bate_com_frontend_package_json() -> None:
    """Regressão do bug real (2026-08-30): electron-builder deriva o nome do
    instalador e o conteúdo do latest.yml de frontend/package.json, não de
    pyproject.toml — se os dois divergirem, o instalador publicado mente sua
    própria versão e o electron-updater recusa a atualização real como
    "downgrade". Os dois estão em `.github/release-lines.json::version_files`;
    este teste pega a divergência antes do build de produção."""
    pyproject: dict[str, dict[str, str]] = tomllib.loads(
        (_MONOREPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    frontend_pkg: dict[str, str] = json.loads(
        (_MONOREPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["version"] == frontend_pkg["version"], (
        "pyproject.toml e frontend/package.json com versões "
        "diferentes — o instalador publicado terá o número de versão errado."
    )


def test_release_lines_sincroniza_todos_os_arquivos_de_versao() -> None:
    """`.github/release-lines.json` é a única fonte da automação de release.

    O primeiro item de `version_files` dá a versão atual e todos são reescritos
    a cada release. Compara o CONJUNTO inteiro (==): remover um arquivo da lista
    precisa quebrar este teste, não só a adição de um novo passar despercebida.
    """
    files = _release_lines()["version_files"]
    assert files == ["pyproject.toml", "frontend/package.json", "uv.lock"]

    version = tomllib.loads(
        (_MONOREPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    frontend = json.loads(
        (_MONOREPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((_MONOREPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    vectora_packages = [
        package for package in lock["package"] if package["name"] == "vectora"
    ]
    assert len(vectora_packages) == 1
    assert frontend["version"] == version
    assert vectora_packages[0]["version"] == version

    leftovers = sorted(
        path.name
        for pattern in ("release-please-config*.json", ".release-please-manifest*.json")
        for path in _MONOREPO_ROOT.glob(pattern)
    )
    assert leftovers == []


def test_linhas_de_release_nao_propoem_a_mesma_tag() -> None:
    """Manutenção sobe patch (milestone `X.Y.x`) e desenvolvimento sobe minor.

    A linha de desenvolvimento é sempre a minor seguinte à de manutenção, então
    um patch de manutenção nunca colide com a próxima minor.
    """
    lines = _release_lines()
    development = lines["development"]
    maintenance = lines["maintenance"]

    assert development["branch"] != maintenance["branch"]
    assert maintenance["branch"] == "master"
    maintenance_match = re.fullmatch(r"(\d+)\.(\d+)\.x", maintenance["milestone"])
    development_match = re.fullmatch(r"(\d+)\.(\d+)", development["milestone"])
    assert maintenance_match is not None
    assert development_match is not None
    major, minor = map(int, maintenance_match.groups())
    assert tuple(map(int, development_match.groups())) == (major, minor + 1)
    assert development["branch"] == f"release/{major}.{minor + 1}"
