"""Cálculo determinístico de snapshot criptográfico (SHA-256) de arquivos de workspace."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def hash_file_sha256(file_path: Path) -> str:
    """Calcula o hash SHA-256 de um arquivo individual."""
    h = hashlib.sha256()
    try:
        with file_path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def compute_files_snapshot(
    cwd: Path | str, files: list[str] | None = None
) -> tuple[str, dict[str, str]]:
    """Calcula o SHA-256 combinado e o mapa de hashes individuais para auditoria.

    Args:
        cwd: Diretório base do workspace.
        files: Lista opcional de caminhos relativos. Se omitida ou vazia, varre os arquivos relevantes.

    Returns:
        Tupla com (sha256_combinado, mapa_arquivo_para_hash).
    """
    root = Path(cwd)
    file_hashes: dict[str, str] = {}

    target_paths: list[Path] = []
    if files:
        for f in files:
            p = root / f if not Path(f).is_absolute() else Path(f)
            if not p.is_dir():
                target_paths.append(p)
    else:
        # Varre arquivos do workspace ignorando diretórios de cache/vcs
        ignored_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            "dist",
            "build",
        }
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ignored_dirs]
            target_paths.extend(Path(dirpath) / fn for fn in filenames)

    for p in sorted(target_paths, key=str):
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(p).replace("\\", "/")
        digest = hash_file_sha256(p)
        file_hashes[rel] = digest or "unreadable"

    # Digest combinado de todos os arquivos ordenados por caminho relativo
    combined = hashlib.sha256()
    for rel_path in sorted(file_hashes.keys()):
        combined.update(rel_path.encode("utf-8"))
        combined.update(b":")
        combined.update(file_hashes[rel_path].encode("utf-8"))
        combined.update(b"\n")

    return combined.hexdigest(), file_hashes
