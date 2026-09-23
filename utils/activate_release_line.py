"""Prepara os arquivos da nova linha de manutenção a partir de uma revisão Git."""

import subprocess  # nosec B404 - executa apenas o git fixo do checkout confiável
import sys
from collections.abc import Callable, Iterable
from pathlib import Path


def copy_release_files(
    source_ref: str,
    destination: Path,
    files: Iterable[str],
    reader: Callable[[str, str], str] | None = None,
) -> tuple[str, ...]:
    """Copia os arquivos de release da revisão de rotação para o checkout alvo."""
    read_file = reader or _read_from_git
    copied: list[str] = []
    for relative_file in files:
        relative_path = Path(relative_file)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"caminho de release inseguro: {relative_file}")
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(read_file(source_ref, relative_file), encoding="utf-8")
        copied.append(relative_file)
    return tuple(copied)


def activation_push_mode(
    direct_push_succeeded: bool, existing_fallback_ref: str | None
) -> str:
    """Descreve se a ativação terminou direto ou atualizou a PR de fallback."""
    if direct_push_succeeded:
        return "direct"
    return "fallback-update" if existing_fallback_ref else "fallback-create"


def _read_from_git(source_ref: str, relative_file: str) -> str:
    result = subprocess.run(  # nosec
        ["git", "show", f"{source_ref}:{relative_file}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit("uso: activate_release_line.py REF DESTINO ARQUIVO...")
    copy_release_files(sys.argv[1], Path(sys.argv[2]), sys.argv[3:])


if __name__ == "__main__":
    main()
