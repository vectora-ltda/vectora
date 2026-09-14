import json
import zipfile
from pathlib import Path

import pytest

from backend.services.vext import inspect_vext


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
    "files, message",
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
