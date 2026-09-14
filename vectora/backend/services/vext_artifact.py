"""Build and verify deterministic Vectora extension artifacts.

The package validator in :mod:`backend.services.vext` is intentionally kept
small and side-effect free.  This module adds the build boundary around it:
source projects are normalized into a production-only artifact, every payload
is hashed, and optional Ed25519 signatures cover the canonical manifest and
integrity record.  No extension code is imported or executed while building.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from backend.services.vext import (
    MANIFEST_NAME,
    MAX_FILES,
    MAX_PACKAGE_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    VextManifest,
    _validate_archive_name,
)

INTEGRITY_NAME: Final = "integrity.json"
SBOM_NAME: Final = "sbom.json"
SIGNATURE_MANIFEST_NAME: Final = "signature/manifest.json"
SIGNATURE_NAME: Final = "signature/signature.bin"
PUBLIC_KEY_NAME: Final = "signature/public.key"
BUILD_EPOCH: Final = (1980, 1, 1, 0, 0, 0)
EXCLUDED_PARTS: Final = frozenset({".git", "node_modules", "__pycache__", ".venv"})
DEFAULT_ROOTS: Final = (
    "frontend/dist",
    "backend/node/dist",
    "backend/python",
    "assets",
)
GENERATED_NAMES: Final = frozenset(
    {
        INTEGRITY_NAME,
        SBOM_NAME,
        SIGNATURE_MANIFEST_NAME,
        SIGNATURE_NAME,
        PUBLIC_KEY_NAME,
    }
)


def _reserved_member_conflict(name: str) -> bool:
    """Return whether a payload path collides with generated metadata."""
    return any(
        name == reserved
        or name.startswith(f"{reserved}/")
        or reserved.startswith(f"{name}/")
        for reserved in GENERATED_NAMES
    )


@dataclass(frozen=True, slots=True)
class VextBuildResult:
    """Metadata produced after a successful deterministic build."""

    path: Path
    manifest: VextManifest
    files: tuple[str, ...]
    content_digest: str
    signed: bool


def canonical_json(value: object) -> bytes:
    """Serialize JSON with a stable ordering and UTF-8 representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_manifest(source_dir: Path) -> tuple[VextManifest, dict[str, object]]:
    manifest_path = source_dir / MANIFEST_NAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = VextManifest.model_validate(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("manifesto de extensão inválido") from exc
    if manifest.api_version > 1:
        raise ValueError("versão da API da extensão não suportada")
    return manifest, raw


def _safe_source_file(source_dir: Path, relative: str) -> Path:
    """Resolve a source member without allowing paths outside the project."""
    _validate_archive_name(relative)
    candidate = (source_dir / PurePosixPath(relative)).resolve()
    root = source_dir.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("arquivo da extensão fora do projeto")
    if not candidate.is_file():
        raise ValueError(f"arquivo declarado não existe: {relative}")
    return candidate


def _collect_files(source_dir: Path, manifest: VextManifest) -> dict[str, bytes]:
    declared = set(manifest.files)
    declared.add(MANIFEST_NAME)
    collisions = sorted(name for name in declared if _reserved_member_conflict(name))
    if collisions:
        raise ValueError(
            f"arquivos reservados não podem ser payload: {', '.join(collisions)}"
        )
    if not manifest.files:
        declared.update(
            relative.as_posix()
            for root_name in DEFAULT_ROOTS
            for relative_path in (source_dir / root_name).rglob("*")
            if relative_path.is_file()
            for relative in [relative_path.relative_to(source_dir)]
            if not any(part in EXCLUDED_PARTS for part in relative.parts)
        )
        declared.add(manifest.entrypoint)
    if len(declared) > MAX_FILES - 1:
        raise ValueError("a extensão contém arquivos demais")
    collisions = sorted(name for name in declared if _reserved_member_conflict(name))
    if collisions:
        raise ValueError(
            f"arquivos reservados não podem ser payload: {', '.join(collisions)}"
        )
    payload: dict[str, bytes] = {}
    total_bytes = 0
    for relative in sorted(declared):
        path = _safe_source_file(source_dir, relative)
        data = path.read_bytes()
        total_bytes += len(data)
        if total_bytes > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("conteúdo descompactado excede o limite")
        payload[relative] = data
    if manifest.entrypoint not in payload:
        raise ValueError("entrypoint não foi incluído no artefato")
    if manifest.frontend_entrypoint and manifest.frontend_entrypoint not in payload:
        raise ValueError("frontend_entrypoint não foi incluído no artefato")
    if manifest.backend_entrypoint and manifest.backend_entrypoint not in payload:
        raise ValueError("backend_entrypoint não foi incluído no artefato")
    return payload


def _integrity_record(payload: dict[str, bytes]) -> dict[str, object]:
    files = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    digest = hashlib.sha256(canonical_json(files)).hexdigest()
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": files,
        "content_digest": digest,
    }


def _write_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, BUILD_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def build_vext(
    source_dir: str | Path,
    output_path: str | Path,
    *,
    signing_key: SigningKey | None = None,
) -> VextBuildResult:
    """Build a production-only, deterministic ``.vext`` artifact."""
    source = Path(source_dir).resolve()
    if not source.is_dir():
        raise ValueError("diretório da extensão não existe")
    manifest, raw_manifest = _read_manifest(source)
    payload = _collect_files(source, manifest)
    integrity = _integrity_record(payload)
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": [],
        "metadata": {
            "extension_id": manifest.id,
            "extension_version": manifest.version,
        },
    }
    signed = signing_key is not None
    signature_record: dict[str, object] | None = None
    if signing_key is not None:
        signed_data = canonical_json({"manifest": raw_manifest, "integrity": integrity})
        signature_record = {
            "algorithm": "ed25519",
            "public_key": base64.b64encode(bytes(signing_key.verify_key)).decode(
                "ascii"
            ),
            "signature": base64.b64encode(
                signing_key.sign(signed_data).signature
            ).decode("ascii"),
        }
    output = Path(output_path)
    if output.suffix.lower() != ".vext":
        raise ValueError("o artefato deve usar a extensão .vext")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output.stem}.", suffix=".vext", dir=output.parent, delete=False
    ) as temporary_handle:
        temporary = Path(temporary_handle.name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name, data in sorted(payload.items()):
                _write_entry(archive, name, data)
            _write_entry(archive, INTEGRITY_NAME, canonical_json(integrity))
            _write_entry(archive, SBOM_NAME, canonical_json(sbom))
            if signature_record is not None:
                signature_b64 = signature_record["signature"]
                public_key_b64 = signature_record["public_key"]
                if not isinstance(signature_b64, str) or not isinstance(
                    public_key_b64, str
                ):
                    raise ValueError("assinatura inválida")
                _write_entry(
                    archive, SIGNATURE_MANIFEST_NAME, canonical_json(signature_record)
                )
                _write_entry(
                    archive,
                    SIGNATURE_NAME,
                    base64.b64decode(signature_b64),
                )
                _write_entry(
                    archive,
                    PUBLIC_KEY_NAME,
                    base64.b64decode(public_key_b64),
                )
        if temporary.stat().st_size > MAX_PACKAGE_BYTES:
            raise ValueError("pacote excede o limite de tamanho")
        # Validate the completed archive before publishing it atomically.
        from backend.services.vext import inspect_vext

        inspect_vext(temporary)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return VextBuildResult(
        path=output,
        manifest=manifest,
        files=tuple(sorted(payload)),
        content_digest=str(integrity["content_digest"]),
        signed=signed,
    )


def verify_vext(
    path: str | Path, *, verify_key: VerifyKey | None = None
) -> VextBuildResult:
    """Verify payload hashes and an optional Ed25519 signature without running code."""
    from backend.services.vext import inspect_vext

    package = inspect_vext(path)
    artifact = Path(path)
    with zipfile.ZipFile(artifact) as archive:
        signed = False
        try:
            integrity = json.loads(archive.read(INTEGRITY_NAME))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("registro de integridade ausente ou inválido") from exc
        files = integrity.get("files")
        if not isinstance(files, dict):
            raise ValueError("registro de integridade inválido")
        regular_members = {
            info.filename
            for info in archive.infolist()
            if not info.is_dir() and info.filename not in GENERATED_NAMES
        }
        if set(files) != regular_members:
            raise ValueError(
                "registro de integridade não corresponde aos arquivos do pacote"
            )
        for name, expected in files.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise ValueError("registro de integridade inválido")
            _validate_archive_name(name)
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected:
                raise ValueError(f"integridade inválida: {name}")
        actual_digest = hashlib.sha256(canonical_json(files)).hexdigest()
        if actual_digest != integrity.get("content_digest"):
            raise ValueError("digest de conteúdo inválido")
        try:
            signature_record = json.loads(archive.read(SIGNATURE_MANIFEST_NAME))
        except KeyError:
            signature_record = None
        if verify_key is not None:
            if not isinstance(signature_record, dict):
                raise ValueError("assinatura ausente")
            try:
                signature_b64 = signature_record.get("signature")
                public_key_b64 = signature_record.get("public_key")
                if not isinstance(signature_b64, str) or not isinstance(
                    public_key_b64, str
                ):
                    raise ValueError("assinatura inválida")
                signature = base64.b64decode(signature_b64, validate=True)
                public_key = VerifyKey(base64.b64decode(public_key_b64, validate=True))
                if public_key != verify_key:
                    raise ValueError("chave pública inesperada")
                manifest_raw = json.loads(archive.read(MANIFEST_NAME))
                verify_key.verify(
                    canonical_json({"manifest": manifest_raw, "integrity": integrity}),
                    signature,
                )
                signed = True
            except (KeyError, ValueError, BadSignatureError) as exc:
                raise ValueError("assinatura inválida") from exc
    return VextBuildResult(
        path=artifact,
        manifest=package.manifest,
        files=tuple(package.files),
        content_digest=str(integrity["content_digest"]),
        signed=signed,
    )


__all__ = ["VextBuildResult", "build_vext", "canonical_json", "verify_vext"]
