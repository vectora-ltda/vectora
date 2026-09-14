"""Validação e inspeção segura de pacotes de extensão ``.vext``.

O formato é deliberadamente nativo do Vectora: um ZIP com um manifesto
``vectora-extension.json`` na raiz. Esta camada valida o contrato sem executar
código, permitindo que a instalação e o host sejam adicionados depois com a
mesma fronteira de segurança.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

from backend.workspace.skills_lock import Version
from pydantic import BaseModel, Field, field_validator


def _validate_package_path(value: str) -> str:
    """Require an unambiguous, portable relative POSIX archive path."""
    if (
        not value
        or "\\" in value
        or value.startswith("/")
        or PureWindowsPath(value).drive
        or value != PurePosixPath(value).as_posix()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("caminho deve ser um caminho POSIX relativo canônico")
    return value



MANIFEST_NAME: Final = "vectora-extension.json"
MAX_PACKAGE_BYTES: Final = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES: Final = 200 * 1024 * 1024
MAX_FILES: Final = 2_000
SUPPORTED_API_VERSION: Final = 1
ALLOWED_PERMISSIONS: Final = frozenset({"workspace.read", "workspace.write"})


class VextManifest(BaseModel):
    """Contrato versionado do manifesto de uma extensão Vectora."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1)
    api_version: int = Field(ge=1)
    entrypoint: str = Field(min_length=1, max_length=240)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        Version.parse(value)
        return value

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        return _validate_package_path(value)

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - ALLOWED_PERMISSIONS)
        if unknown:
            raise ValueError(f"permissões não suportadas: {', '.join(unknown)}")
        return sorted(set(values))


class VextPackage(BaseModel):
    """Resultado validado de uma inspeção sem executar a extensão."""

    manifest: VextManifest
    files: list[str]
    size_bytes: int


def inspect_vext(path: str | Path) -> VextPackage:
    """Valida manifesto, limites e caminhos de um pacote ``.vext``."""
    package = Path(path)
    if package.suffix.lower() != ".vext":
        raise ValueError("o pacote deve usar a extensão .vext")
    size = package.stat().st_size
    if size > MAX_PACKAGE_BYTES:
        raise ValueError("pacote excede o limite de tamanho")
    try:
        archive = zipfile.ZipFile(package)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("pacote .vext inválido") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError("pacote contém arquivos demais")
        total = sum(info.file_size for info in infos)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("conteúdo descompactado excede o limite")
        names: list[str] = []
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            # Check mode bits before allowing directory-shaped entries through.
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("pacote não pode conter links simbólicos")
            name = _validate_package_path(info.filename)
            if name in entries:
                raise ValueError("pacote contém entradas duplicadas")
            entries[name] = info
            if not info.is_dir():
                names.append(name)
        if MANIFEST_NAME not in entries or MANIFEST_NAME not in names:
            raise ValueError(f"manifesto ausente: {MANIFEST_NAME}")
        try:
            raw_manifest = json.loads(archive.read(entries[MANIFEST_NAME]))
            manifest = VextManifest.model_validate(raw_manifest)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("manifesto inválido") from exc
        if manifest.api_version > SUPPORTED_API_VERSION:
            raise ValueError("versão da API da extensão não suportada")
        if manifest.entrypoint not in names:
            raise ValueError("entrypoint não existe no pacote")
        return VextPackage(manifest=manifest, files=sorted(names), size_bytes=size)
