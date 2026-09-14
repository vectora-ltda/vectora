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
import zlib
from pathlib import Path, PurePosixPath
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.workspace.skills_lock import Version

MANIFEST_NAME: Final = "vectora-extension.json"
MAX_PACKAGE_BYTES: Final = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES: Final = 200 * 1024 * 1024
MAX_FILES: Final = 2_000
SUPPORTED_API_VERSION: Final = 1
ALLOWED_PERMISSIONS: Final = frozenset({"workspace.read", "workspace.write"})


class VextManifest(BaseModel):
    """Contrato versionado do manifesto de uma extensão Vectora."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    version: str
    api_version: int = Field(ge=1)
    entrypoint: str = Field(min_length=1, max_length=240)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls: type[VextManifest], value: str) -> str:
        try:
            _validate_archive_name(value)
        except ValueError as exc:
            raise ValueError("entrypoint deve ser um caminho relativo seguro") from exc
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls: type[VextManifest], value: str) -> str:
        try:
            Version.parse(value)
        except ValueError as exc:
            raise ValueError("versão deve seguir SemVer") from exc
        return value

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls: type[VextManifest], values: list[str]) -> list[str]:
        unknown = sorted(set(values) - ALLOWED_PERMISSIONS)
        if unknown:
            raise ValueError(f"permissões não suportadas: {', '.join(unknown)}")
        return sorted(set(values))


class VextPackage(BaseModel):
    """Resultado validado de uma inspeção sem executar a extensão."""

    manifest: VextManifest
    files: list[str]
    size_bytes: int


def _validate_archive_name(name: str) -> str:
    """Validate an archive member as a canonical relative POSIX path."""
    if not name or "\\" in name or name.startswith("/"):
        raise ValueError("caminho inseguro")
    if re.match(r"^[A-Za-z]:($|/)", name):
        raise ValueError("caminho inseguro")
    parts = name.rstrip("/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("caminho inseguro")
    canonical = PurePosixPath(*parts).as_posix()
    if canonical != name.rstrip("/"):
        raise ValueError("caminho inseguro")
    return name


def inspect_vext(path: str | Path | None) -> VextPackage:
    """Valida manifesto, limites e caminhos de um pacote ``.vext``."""
    if path is None or not str(path).strip():
        raise ValueError("pacote .vext inválido")
    try:
        package = Path(path)
        size = package.stat().st_size
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("pacote .vext inválido") from exc
    if package.suffix.lower() != ".vext":
        raise ValueError("o pacote deve usar a extensão .vext")
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
        seen_names: set[str] = set()
        file_infos: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            # Unix mode 0o120000 identifica symlink dentro de ZIPs.
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("pacote não pode conter links simbólicos")
            try:
                name = _validate_archive_name(info.filename)
            except ValueError as exc:
                raise ValueError("pacote contém caminho inseguro") from exc
            if name in seen_names:
                raise ValueError("pacote contém nomes duplicados")
            seen_names.add(name)
            if info.is_dir():
                continue
            names.append(name)
            file_infos[name] = info
        manifest_info = file_infos.get(MANIFEST_NAME)
        if manifest_info is None:
            raise ValueError(f"manifesto ausente: {MANIFEST_NAME}")
        try:
            raw_manifest = json.loads(archive.read(manifest_info))
            manifest = VextManifest.model_validate(raw_manifest)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            zipfile.BadZipFile,
            OSError,
            RuntimeError,
            NotImplementedError,
            EOFError,
            KeyError,
            zlib.error,
        ) as exc:
            raise ValueError("manifesto inválido") from exc
        if manifest.api_version > SUPPORTED_API_VERSION:
            raise ValueError("versão da API da extensão não suportada")
        if manifest.entrypoint not in names:
            raise ValueError("entrypoint não existe no pacote")
        return VextPackage(manifest=manifest, files=sorted(names), size_bytes=size)
