"""Compatibilidade de execução para launchers shell sem shebang."""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path


def command_for_execution(command: Sequence[str]) -> list[str]:
    """Adiciona um shell ao executar script POSIX sem linha shebang.

    Alguns gerenciadores de pacotes distribuem launchers shell sem shebang.
    Um terminal interativo tenta executá-los via shell automaticamente, mas
    ``subprocess`` recebe ``ENOEXEC``. Executá-los explicitamente com ``sh``
    mantém o comportamento do terminal sem depender desse fallback.
    """
    argv = list(command)
    if os.name == "nt" or not argv:
        return argv

    executable = Path(shutil.which(argv[0]) or argv[0])
    if not executable.is_file():
        return argv

    try:
        with executable.open("rb") as launcher:
            first_line = launcher.readline()
    except OSError:
        return argv

    if not first_line.startswith(b"#") or first_line.startswith(b"#!"):
        return argv

    shell = shutil.which("sh") or "/bin/sh"
    return [shell, str(executable), *argv[1:]]
