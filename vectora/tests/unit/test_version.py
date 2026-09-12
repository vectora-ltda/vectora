"""Tests for src/version.py"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict

from backend.version import __version__

_MONOREPO_ROOT = Path(__file__).resolve().parents[3]


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
    "downgrade". Sem release-please-config.json::extra-files sincronizando
    os dois, esse teste é o único jeito de pegar a divergência antes do
    build de produção."""
    pyproject: dict[str, dict[str, str]] = tomllib.loads(
        (_MONOREPO_ROOT / "vectora" / "pyproject.toml").read_text(encoding="utf-8")
    )
    frontend_pkg: dict[str, str] = json.loads(
        (_MONOREPO_ROOT / "vectora" / "frontend" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert pyproject["project"]["version"] == frontend_pkg["version"], (
        "vectora/pyproject.toml e vectora/frontend/package.json com versões "
        "diferentes — o instalador publicado terá o número de versão errado."
    )


def test_release_please_config_sincroniza_todos_os_arquivos_de_versao() -> None:
    """Sem essas entradas, release-please bumpa só o manifest e o teste
    acima (pyproject.toml vs frontend/package.json) volta a falhar na
    release seguinte — o mesmo vale pra services/company ficarem pra trás.

    release-please-config.json rastreia o monorepo INTEIRO como um único
    pacote (chave "." — path é interpretado literalmente pelo release-please,
    não é um nome arbitrário; uma chave "vectora" faria o path virar
    `vectora/`, restringindo commits contados só àquela pasta). Os paths de
    extra-files, por isso, são relativos à raiz do repo.

    Compara o CONJUNTO inteiro (==), não só "in" pra cada path — remover uma
    entrada (ex.: company/package.json parar de ser sincronizado) precisa
    quebrar este teste, não só a adição de uma nova passar despercebida."""
    config: _ReleasePleaseConfig = json.loads(
        (_MONOREPO_ROOT / "release-please-config.json").read_text(encoding="utf-8")
    )
    extra_files: list[_ExtraFile] = config["packages"]["."].get("extra-files", [])
    paths: set[str] = {entry["path"] for entry in extra_files}
    uv_entry = next(
        entry for entry in extra_files if entry["path"] == "vectora/uv.lock"
    )
    assert uv_entry.get("type") == "generic"
    lock_text = (_MONOREPO_ROOT / "vectora" / "uv.lock").read_text(encoding="utf-8")
    package_blocks = re.split(
        r"(?=^\[\[package\]\]\s*$)", lock_text, flags=re.MULTILINE
    )
    vectora_block = next(
        block
        for block in package_blocks
        if re.search(r'^name = "vectora"\s*$', block, re.MULTILINE)
    )
    lock_version = re.search(
        r'^version = "([^"]+)"\s+# x-release-please-version\s*$',
        vectora_block,
        re.MULTILINE,
    )
    assert lock_version is not None, (
        'O bloco [[package]] de "vectora" precisa marcar sua própria linha '
        "version com # x-release-please-version."
    )
    pyproject = tomllib.loads(
        (_MONOREPO_ROOT / "vectora" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert lock_version.group(1) == pyproject["project"]["version"]
    assert paths == {
        "vectora/pyproject.toml",
        "vectora/frontend/package.json",
        "services/package.json",
        "company/package.json",
        "vectora/uv.lock",
    }
