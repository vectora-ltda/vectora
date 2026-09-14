import json
import zipfile
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from backend.services.vext import VextManifest, inspect_vext


def _package(path: Path, manifest: dict[str, object], files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("vectora-extension.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)


def test_inspect_vext_validates_manifest_and_entrypoint(tmp_path: Path) -> None:
    path = tmp_path / "hello.vext"
    _package(
        path,
        {
            "id": "hello.tool",
            "name": "Hello",
            "version": "1.0.0",
            "api_version": 1,
            "entrypoint": "main.py",
            "permissions": ["workspace.read"],
        },
        {"main.py": "def run(): return 'ok'"},
    )

    package = inspect_vext(path)
    assert package.manifest.id == "hello.tool"
    assert package.files == ["main.py", "vectora-extension.json"]


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"../escape.py": "x"}, "caminho inseguro"),
        ({"main.py": "x"}, "entrypoint não existe"),
    ],
)
def test_inspect_vext_rejeita_conteudo_inseguro(
    tmp_path: Path, files: dict[str, str], message: str
) -> None:
    path = tmp_path / "invalid.vext"
    _package(
        path,
        {
            "id": "hello.tool",
            "name": "Hello",
            "version": "1.0.0",
            "api_version": 1,
            "entrypoint": "missing.py",
        },
        files,
    )
    with pytest.raises(ValueError, match=message):
        inspect_vext(path)


def test_inspect_vext_rejeita_permissao_desconhecida(tmp_path: Path) -> None:
    path = tmp_path / "permission.vext"
    _package(
        path,
        {
            "id": "hello.tool",
            "name": "Hello",
            "version": "1.0.0",
            "api_version": 1,
            "entrypoint": "main.py",
            "permissions": ["network.unrestricted"],
        },
        {"main.py": "x"},
    )
    with pytest.raises(ValueError, match="manifesto inválido"):
        inspect_vext(path)


@pytest.mark.parametrize(
    "name",
    ["/main.py", "C:/main.py", "dir//main.py", "./main.py", "dir/./main.py"],
)
def test_inspect_vext_rejeita_caminhos_nao_canonicos(tmp_path: Path, name: str) -> None:
    path = tmp_path / "unsafe.vext"
    _package(
        path,
        {
            "id": "hello.tool",
            "name": "Hello",
            "version": "1.0.0",
            "api_version": 1,
            "entrypoint": "main.py",
        },
        {name: "x", "main.py": "x"},
    )
    with pytest.raises(ValueError, match="caminho inseguro"):
        inspect_vext(path)


def test_inspect_vext_rejeita_nomes_duplicados(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.vext"
    with zipfile.ZipFile(path, "w") as archive:
        manifest = {
            "id": "hello.tool",
            "name": "Hello",
            "version": "1.0.0",
            "api_version": 1,
            "entrypoint": "main.py",
        }
        archive.writestr("vectora-extension.json", json.dumps(manifest))
        archive.writestr("vectora-extension.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="nomes duplicados"):
        inspect_vext(path)


def test_inspect_vext_rejeita_symlink_marcado_como_diretorio(tmp_path: Path) -> None:
    path = tmp_path / "symlink.vext"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("link/")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"")
    with pytest.raises(ValueError, match="links simbólicos"):
        inspect_vext(path)


@pytest.mark.parametrize("version", ["1.0.0-alpha+build.1", "0.0.0", "1.2.3-rc.1"])
def test_manifesto_aceita_semver_completo(version: str) -> None:
    manifest = VextManifest(
        id="hello.tool",
        name="Hello",
        version=version,
        api_version=1,
        entrypoint="main.py",
    )
    assert manifest.version == version


@pytest.mark.parametrize("version", ["01.0.0", "1.02.0", "1.0.03", "1.0.0-", "1.0"])
def test_manifesto_rejeita_semver_invalido(version: str) -> None:
    with pytest.raises(ValueError, match="versão deve seguir SemVer"):
        VextManifest(
            id="hello.tool",
            name="Hello",
            version=version,
            api_version=1,
            entrypoint="main.py",
        )


def test_manifesto_rejeita_campos_desconhecidos() -> None:
    with pytest.raises(ValueError, match="extra_forbidden"):
        VextManifest.model_validate(
            {
                "id": "hello.tool",
                "name": "Hello",
                "version": "1.0.0",
                "api_version": 1,
                "entrypoint": "main.py",
                "permission": ["workspace.read"],
            }
        )


def test_inspect_vext_normaliza_erro_de_leitura_do_manifesto(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "corrupt.vext"
    _package(
        path,
        {
            "id": "hello.tool",
            "name": "Hello",
            "version": "1.0.0",
            "api_version": 1,
            "entrypoint": "main.py",
        },
        {"main.py": "x"},
    )

    def fail_read(*args: object, **kwargs: object) -> bytes:
        raise zipfile.BadZipFile("corrupt")

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_read)
    with pytest.raises(ValueError, match="manifesto inválido"):
        inspect_vext(path)


@pytest.mark.parametrize("path", [None, ""])
def test_inspect_vext_rejeita_caminho_vazio(path: str | None) -> None:
    with pytest.raises(ValueError, match=r"pacote \.vext inválido"):
        inspect_vext(path)
