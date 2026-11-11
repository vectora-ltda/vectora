"""Gera checksums e assinaturas GPG opcionais dos artefatos de release."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path


def _run_gpg(*args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("gpg")
    if executable is None:
        raise SystemExit("GPG is required to sign release artifacts.")
    return subprocess.run(  # nosec B603
        [executable, "--batch", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def sign_release_artifacts(directory: Path) -> None:
    if not directory.is_dir():
        raise SystemExit(f"Artifact directory does not exist: {directory}")

    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and not path.name.startswith("SHA256SUMS")
        and not path.name.endswith(".asc")
    )
    if not files:
        raise SystemExit(f"No release artifacts found in {directory}")

    checksum_path = directory / "SHA256SUMS"
    checksum_lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(directory).as_posix()}"
        for path in files
    ]
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    private_key = os.environ.get("GPG_PRIVATE_KEY", "")
    if not private_key:
        print(
            "GPG_PRIVATE_KEY is not configured; publishing SHA256SUMS without detached signatures."
        )
        return

    with tempfile.TemporaryDirectory(prefix="vectora-gpg-") as temporary_directory:
        temporary_home = Path(temporary_directory) / "gnupg"
        temporary_home.mkdir(mode=0o700)
        key_file = Path(temporary_directory) / "private-key.asc"
        key_file.write_text(private_key, encoding="utf-8")
        key_file.chmod(0o600)
        passphrase_file = Path(temporary_directory) / "passphrase"
        passphrase_file.write_text(
            os.environ.get("GPG_PASSPHRASE", ""), encoding="utf-8"
        )
        passphrase_file.chmod(0o600)

        _run_gpg("--homedir", str(temporary_home), "--import", str(key_file))
        secret_keys = _run_gpg(
            "--homedir", str(temporary_home), "--with-colons", "--list-secret-keys"
        ).stdout
        fingerprint = next(
            (
                line.split(":")[9]
                for line in secret_keys.splitlines()
                if line.startswith("fpr:")
            ),
            "",
        )
        if not fingerprint:
            raise SystemExit(
                "The configured GPG private key has no secret-key fingerprint."
            )

        public_key = _run_gpg(
            "--homedir", str(temporary_home), "--armor", "--export", fingerprint
        ).stdout
        (directory / "vectora-release-key.asc").write_text(public_key, encoding="utf-8")

        for artifact in [*files, checksum_path]:
            _run_gpg(
                "--homedir",
                str(temporary_home),
                "--yes",
                "--pinentry-mode",
                "loopback",
                "--passphrase-file",
                str(passphrase_file),
                "--local-user",
                fingerprint,
                "--armor",
                "--detach-sign",
                str(artifact),
            )
    print(f"Signed {len(files)} release artifact(s) and SHA256SUMS with GPG.")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: sign_release_artifacts.py DIRECTORY")
    sign_release_artifacts(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
