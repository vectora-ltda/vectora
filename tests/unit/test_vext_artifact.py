import json
import zipfile
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from backend.services import vext_artifact
from backend.services.vext_artifact import build_vext, verify_vext


def _source(root: Path, *, extra: str = "") -> None:
    (root / "frontend" / "dist").mkdir(parents=True)
    (root / "backend" / "python").mkdir(parents=True)
    (root / "main.py").write_text(
        "def handle(method, params):\n    return {'method': method, 'params': params}\n",
        encoding="utf-8",
    )
    (root / "frontend" / "dist" / "index.js").write_text(
        "console.log('ok');\n", encoding="utf-8"
    )
    (root / "vectora-extension.json").write_text(
        json.dumps(
            {
                "id": "hello.tool",
                "publisher": "vectora",
                "name": "Hello",
                "version": "1.0.0",
                "api_version": 1,
                "protocol_version": 1,
                "runtime": "python",
                "entrypoint": "main.py",
                "backend_entrypoint": "main.py",
                "frontend_entrypoint": "frontend/dist/index.js",
                "permissions": ["workspace.read"],
                "platforms": ["any"],
                "provenance": {"source": "test"},
            }
        )
        + extra,
        encoding="utf-8",
    )


def test_build_vext_is_deterministic_and_verifiable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    first = build_vext(source, tmp_path / "first.vext")
    second = build_vext(source, tmp_path / "second.vext")

    assert first.content_digest == second.content_digest
    assert (tmp_path / "first.vext").read_bytes() == (
        tmp_path / "second.vext"
    ).read_bytes()
    verified = verify_vext(tmp_path / "first.vext")
    assert verified.manifest.id == "hello.tool"
    assert verified.signed is False
    import zipfile

    with zipfile.ZipFile(tmp_path / "first.vext") as archive:
        sbom = json.loads(archive.read("sbom.json"))
    assert sbom["bomFormat"] == "CycloneDX"


def test_build_vext_signs_and_verifies_with_ed25519(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    key = SigningKey.generate()
    result = build_vext(source, tmp_path / "signed.vext", signing_key=key)

    assert result.signed is True
    verified = verify_vext(tmp_path / "signed.vext", verify_key=key.verify_key)
    assert verified.signed is True


def test_verify_vext_rejects_tampered_payload(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    artifact = tmp_path / "tampered.vext"
    build_vext(source, artifact)

    import zipfile

    tampered = tmp_path / "tampered-copy.vext"
    with (
        zipfile.ZipFile(artifact) as source_archive,
        zipfile.ZipFile(tampered, "w") as target_archive,
    ):
        for info in source_archive.infolist():
            content = source_archive.read(info.filename)
            if info.filename == "main.py":
                content = b"def handle(method, params): return None"
            target_archive.writestr(info, content)
    with pytest.raises(ValueError, match="integridade inválida"):
        verify_vext(tampered)


def test_build_rejects_reserved_directory_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    (source / "signature").write_text("collision", encoding="utf-8")
    manifest = json.loads((source / "vectora-extension.json").read_text())
    manifest["files"] = ["main.py", "signature"]
    (source / "vectora-extension.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="reservados"):
        build_vext(source, tmp_path / "collision.vext")


def test_build_rejects_oversized_file_before_reading(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    payload = source / "main.py"
    original_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def tracked_read_bytes(path: Path) -> bytes:
        reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(vext_artifact, "MAX_UNCOMPRESSED_BYTES", 1)
    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    with pytest.raises(ValueError, match="descompactado"):
        build_vext(source, tmp_path / "oversized.vext")

    assert payload not in reads


def test_inspect_rejects_file_directory_conflict(tmp_path: Path) -> None:
    artifact = tmp_path / "conflict.vext"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr(
            "vectora-extension.json",
            json.dumps(
                {
                    "id": "hello.tool",
                    "name": "Hello",
                    "version": "1.0.0",
                    "api_version": 1,
                    "entrypoint": "signature",
                }
            ),
        )
        archive.writestr("signature", b"file")
        archive.writestr("signature/child.py", b"payload")
    from backend.services.vext import inspect_vext

    with pytest.raises(ValueError, match="arquivo e diretório"):
        inspect_vext(artifact)


@pytest.mark.parametrize(
    ("member", "message"),
    [
        ("../escape.py", "caminho inseguro"),
        ("/absolute.py", "caminho inseguro"),
        ("C:/escape.py", "caminho inseguro"),
    ],
)
def test_inspect_rejects_traversal_and_absolute_members(
    tmp_path: Path, member: str, message: str
) -> None:
    artifact = tmp_path / "unsafe.vext"
    manifest = {
        "id": "unsafe.ext",
        "name": "Unsafe",
        "version": "1.0.0",
        "api_version": 1,
        "entrypoint": "main.py",
    }
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("vectora-extension.json", json.dumps(manifest))
        archive.writestr("main.py", "def handle(method, params): return None")
        archive.writestr(member, "escape")

    from backend.services.vext import inspect_vext

    with pytest.raises(ValueError, match=message):
        inspect_vext(artifact)


def test_inspect_rejects_duplicate_names_and_symlinks(tmp_path: Path) -> None:
    from backend.services.vext import inspect_vext

    manifest = {
        "id": "unsafe.ext",
        "name": "Unsafe",
        "version": "1.0.0",
        "api_version": 1,
        "entrypoint": "main.py",
    }
    duplicate = tmp_path / "duplicate.vext"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("vectora-extension.json", json.dumps(manifest))
        archive.writestr("main.py", "safe")
        archive.writestr("main.py", "replacement")
    with pytest.raises(ValueError, match="nomes duplicados"):
        inspect_vext(duplicate)

    symlink = tmp_path / "symlink.vext"
    info = zipfile.ZipInfo("linked.py")
    info.create_system = 3
    info.external_attr = (0o120777 << 16) | 0xA000
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr("vectora-extension.json", json.dumps(manifest))
        archive.writestr("main.py", "safe")
        archive.writestr(info, "../outside.py")
    with pytest.raises(ValueError, match="links simbólicos"):
        inspect_vext(symlink)


def test_inspect_rejects_uncompressed_limit(tmp_path: Path, monkeypatch) -> None:
    from backend.services import vext

    artifact = tmp_path / "large.vext"
    manifest = {
        "id": "large.ext",
        "name": "Large",
        "version": "1.0.0",
        "api_version": 1,
        "entrypoint": "main.py",
    }
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("vectora-extension.json", json.dumps(manifest))
        archive.writestr("main.py", "x" * 64)
    monkeypatch.setattr(vext, "MAX_UNCOMPRESSED_BYTES", 32)

    with pytest.raises(ValueError, match="descompactado"):
        vext.inspect_vext(artifact)


def test_verify_rejects_signature_bytes_changed_after_signing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    key = SigningKey.generate()
    signed = tmp_path / "signed.vext"
    build_vext(source, signed, signing_key=key)
    altered = tmp_path / "altered.vext"
    with (
        zipfile.ZipFile(signed) as source_archive,
        zipfile.ZipFile(altered, "w") as target_archive,
    ):
        for info in source_archive.infolist():
            payload = source_archive.read(info.filename)
            if info.filename == "signature/signature.bin":
                payload = bytes([payload[0] ^ 1]) + payload[1:]
            target_archive.writestr(info, payload)

    with pytest.raises(ValueError, match="assinatura inválida"):
        verify_vext(altered, verify_key=key.verify_key)
