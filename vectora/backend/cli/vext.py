"""Command-line builder and verifier for Vectora extension artifacts."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from nacl.signing import SigningKey, VerifyKey
from rich.console import Console

from backend.services.vext import inspect_vext
from backend.services.vext_artifact import build_vext, verify_vext
from backend.services.vext_install import VextInstallStore
from backend.services.vext_registry import VextTrustStore


def _load_signing_key(path: Path) -> SigningKey:
    """Read a raw 32-byte Ed25519 private key from disk."""
    data = path.read_bytes()
    if len(data) != 32:
        raise ValueError("a chave privada deve conter 32 bytes")
    return SigningKey(data)


def _load_verify_key(path: Path) -> VerifyKey:
    """Read a raw 32-byte Ed25519 public key from disk."""
    data = path.read_bytes()
    if len(data) != 32:
        raise ValueError("a chave pública deve conter 32 bytes")
    return VerifyKey(data)


def main(argv: list[str] | None = None) -> int:
    """Run the language-agnostic VEXT CLI."""
    console = Console(markup=False)
    parser = argparse.ArgumentParser(prog="vext")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="build a deterministic artifact")
    build.add_argument("source", type=Path)
    build.add_argument("-o", "--output", type=Path, required=True)
    build.add_argument("--signing-key", type=Path)

    pack = commands.add_parser("pack", help="alias for build")
    pack.add_argument("source", type=Path)
    pack.add_argument("-o", "--output", type=Path, required=True)
    pack.add_argument("--signing-key", type=Path)

    sign = commands.add_parser("sign", help="build and sign an artifact")
    sign.add_argument("source", type=Path)
    sign.add_argument("-o", "--output", type=Path, required=True)
    sign.add_argument("--signing-key", type=Path, required=True)

    init = commands.add_parser("init", help="create an extension project")
    init.add_argument("destination", type=Path)

    dev = commands.add_parser("dev", help="validate a project for development")
    dev.add_argument("source", type=Path)

    validate = commands.add_parser("validate", help="validate an artifact")
    validate.add_argument("artifact", type=Path)

    inspect = commands.add_parser("inspect", help="print the manifest")
    inspect.add_argument("artifact", type=Path)

    verify = commands.add_parser("verify", help="verify hashes and signature")
    verify.add_argument("artifact", type=Path)
    verify.add_argument("--public-key", type=Path)

    install = commands.add_parser("install", help="install and activate an artifact")
    install.add_argument("artifact", type=Path)
    install.add_argument("--root", type=Path, required=True)
    install.add_argument("--allow-unsigned", action="store_true")
    install.add_argument("--public-key", type=Path)

    rollback = commands.add_parser("rollback", help="activate an installed version")
    rollback.add_argument("extension_id")
    rollback.add_argument("version")
    rollback.add_argument("--root", type=Path, required=True)
    rollback.add_argument("--public-key", type=Path)
    rollback.add_argument("--allow-unsigned", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command in {"build", "pack", "sign"}:
            key = _load_signing_key(args.signing_key) if args.signing_key else None
            result = build_vext(args.source, args.output, signing_key=key)
            console.print(
                json.dumps(
                    {
                        "path": str(result.path),
                        "digest": result.content_digest,
                        "signed": result.signed,
                    }
                )
            )
        elif args.command == "init":
            destination = args.destination
            if destination.exists() and any(destination.iterdir()):
                raise ValueError("diretório de destino não está vazio")
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
            (destination / "main.py").write_text(
                "def handle(method, params):\n    return {'method': method, 'params': params}\n",
                encoding="utf-8",
            )
            (destination / "vectora-extension.json").write_text(
                json.dumps(
                    {
                        "id": "example.extension",
                        "publisher": "local",
                        "name": "Example extension",
                        "version": "0.1.0",
                        "api_version": 1,
                        "protocol_version": 1,
                        "runtime": "python",
                        "entrypoint": "main.py",
                        "permissions": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            console.print(json.dumps({"source": str(destination)}))
        elif args.command == "dev":
            if args.source.suffix.lower() == ".vext":
                manifest = inspect_vext(args.source)
                console.print(
                    json.dumps({"id": manifest.manifest.id, "mode": "artifact"})
                )
            else:
                from backend.services.vext_artifact import _read_manifest

                manifest, _ = _read_manifest(args.source)
                console.print(
                    json.dumps({"id": manifest.id, "mode": "source", "ready": True})
                )
        elif args.command == "validate":
            result = inspect_vext(args.artifact)
            console.print(json.dumps({"id": result.manifest.id, "files": result.files}))
        elif args.command == "inspect":
            result = inspect_vext(args.artifact)
            console.print(result.manifest.model_dump_json(indent=2))
        elif args.command == "verify":
            key = _load_verify_key(args.public_key) if args.public_key else None
            result = verify_vext(args.artifact, verify_key=key)
            console.print(
                json.dumps(
                    {
                        "id": result.manifest.id,
                        "digest": result.content_digest,
                        "signed": result.signed,
                    }
                )
            )
        elif args.command == "install":
            trust_store = None
            if args.public_key:
                trust_store = VextTrustStore()
                key = _load_verify_key(args.public_key)
                trust_store.add(inspect_vext(args.artifact).manifest.publisher, key)
            installed = VextInstallStore(args.root).install(
                args.artifact,
                trust_store=trust_store,
                allow_unsigned=args.allow_unsigned,
            )
            console.print(
                json.dumps(
                    {
                        "id": installed.extension_id,
                        "version": installed.version,
                        "active": installed.active,
                    }
                )
            )
        else:
            trust_store = None
            if args.public_key:
                trust_store = VextTrustStore()
                key = _load_verify_key(args.public_key)
                rollback_artifact = (
                    args.root
                    / "versions"
                    / args.extension_id
                    / args.version
                    / "package.vext"
                )
                trust_store.add(inspect_vext(rollback_artifact).manifest.publisher, key)
            installed = VextInstallStore(args.root).rollback(
                args.extension_id,
                args.version,
                trust_store=trust_store,
                allow_unsigned=args.allow_unsigned,
            )
            console.print(
                json.dumps(
                    {
                        "id": installed.extension_id,
                        "version": installed.version,
                        "active": installed.active,
                    }
                )
            )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"vext: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
