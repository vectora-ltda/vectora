"""Tests for src/version.py"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict

from backend.version import __version__

_MONOREPO_ROOT = Path(__file__).resolve().parents[2]
# O updater TOML do release-please embrulha cada valor como
# {start, end, value}; por isso o filtro compara `@.name.value`, não `@.name`.
_UV_LOCK_JSONPATH = "$.package[?(@.name.value==='vectora')].version"


class _ExtraFile(TypedDict):
    type: str
    path: str
    jsonpath: NotRequired[str]


# Chaves com hífen ("release-type", "extra-files", ...) não são
# identificadores Python válidos — sintaxe funcional do TypedDict em vez da
# de classe.
_ReleasePleasePackage = TypedDict(
    "_ReleasePleasePackage",
    {
        "release-type": str,
        "package-name": str,
        "changelog-path": str,
        "extra-files": list[_ExtraFile],
    },
    total=False,
)


class _ReleasePleaseConfig(TypedDict):
    packages: dict[str, _ReleasePleasePackage]


def test_version_is_string() -> None:
    assert isinstance(__version__, str)


def test_version_semver() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", __version__), f"Not semver: {__version__}"


def test_pyproject_version_bate_com_frontend_package_json() -> None:
    """Regressão do bug real (2026-08-30): electron-builder deriva o nome do
    instalador e o conteúdo do latest.yml de frontend/package.json, não de
    pyproject.toml — se os dois divergirem, o instalador publicado mente sua
    própria versão e o electron-updater recusa a atualização real como
    "downgrade". Sem release-please-config.development.json::extra-files sincronizando
    os dois, esse teste é o único jeito de pegar a divergência antes do
    build de produção."""
    pyproject: dict[str, dict[str, str]] = tomllib.loads(
        (_MONOREPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    frontend_pkg: dict[str, str] = json.loads(
        (_MONOREPO_ROOT / "frontend" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert pyproject["project"]["version"] == frontend_pkg["version"], (
        "pyproject.toml e frontend/package.json com versões "
        "diferentes — o instalador publicado terá o número de versão errado."
    )


def test_release_please_config_sincroniza_todos_os_arquivos_de_versao() -> None:
    """Sem essas entradas, release-please bumpa só o manifest e o teste
    acima (pyproject.toml vs frontend/package.json) volta a falhar na
    release seguinte — o mesmo vale pra services/company ficarem pra trás.

    release-please-config.development.json rastreia o monorepo INTEIRO como um único
    pacote (chave "." — path é interpretado literalmente pelo release-please,
    não é um nome arbitrário; uma chave "vectora" faria o path virar
    `vectora/`, restringindo commits contados só àquela pasta). Os paths de
    extra-files, por isso, são relativos à raiz do repo.

    Compara o CONJUNTO inteiro (==), não só "in" pra cada path — remover uma
    entrada (ex.: company/package.json parar de ser sincronizado) precisa
    quebrar este teste, não só a adição de uma nova passar despercebida."""
    config: _ReleasePleaseConfig = json.loads(
        (_MONOREPO_ROOT / "release-please-config.development.json").read_text(
            encoding="utf-8"
        )
    )
    extra_files: list[_ExtraFile] = config["packages"]["."].get("extra-files", [])
    paths: set[str] = {entry["path"] for entry in extra_files}
    uv_entry = next(
        entry for entry in extra_files if entry["path"] == "uv.lock"
    )
    assert uv_entry.get("type") == "toml"
    assert uv_entry.get("jsonpath") == _UV_LOCK_JSONPATH
    lock = tomllib.loads(
        (_MONOREPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    )
    vectora_packages = [
        package for package in lock["package"] if package["name"] == "vectora"
    ]
    assert len(vectora_packages) == 1
    pyproject = tomllib.loads(
        (_MONOREPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert vectora_packages[0]["version"] == pyproject["project"]["version"]
    assert paths == {
        "pyproject.toml",
        "frontend/package.json",
        "services/package.json",
        "company/package.json",
        "uv.lock",
    }


def test_release_please_separa_bump_de_desenvolvimento_e_manutencao() -> None:
    """A linha master avança minor antes de 1.0 e a manutenção avança patch."""
    development = json.loads(
        (_MONOREPO_ROOT / "release-please-config.development.json").read_text(
            encoding="utf-8"
        )
    )
    maintenance = json.loads(
        (_MONOREPO_ROOT / "release-please-config.maintenance.json").read_text(
            encoding="utf-8"
        )
    )

    assert development["bump-minor-pre-major"] is True
    assert development["bump-patch-for-minor-pre-major"] is False
    assert "release-as" not in development
    development_manifest = json.loads(
        (_MONOREPO_ROOT / ".release-please-manifest.development.json").read_text(
            encoding="utf-8"
        )
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", development_manifest["."])
    assert maintenance["bump-minor-pre-major"] is True
    assert maintenance["bump-patch-for-minor-pre-major"] is True


def test_active_lines_do_not_propose_same_tag_for_fixes() -> None:
    """Uma correção da manutenção não pode colidir com o release minor de master."""
    maintenance_manifest = json.loads(
        (_MONOREPO_ROOT / ".release-please-manifest.maintenance.json").read_text(
            encoding="utf-8"
        )
    )
    development_config = json.loads(
        (_MONOREPO_ROOT / "release-please-config.development.json").read_text(
            encoding="utf-8"
        )
    )
    maintenance_config = json.loads(
        (_MONOREPO_ROOT / "release-please-config.maintenance.json").read_text(
            encoding="utf-8"
        )
    )
    development_manifest = json.loads(
        (_MONOREPO_ROOT / ".release-please-manifest.development.json").read_text(
            encoding="utf-8"
        )
    )
    development_version = development_manifest["."]
    maintenance_major, maintenance_minor, maintenance_patch = map(
        int, maintenance_manifest["."].split(".")
    )
    maintenance_version = (
        f"{maintenance_major}.{maintenance_minor}.{maintenance_patch + 1}"
    )
    assert maintenance_config["bump-patch-for-minor-pre-major"] is True
    assert development_version != maintenance_version
