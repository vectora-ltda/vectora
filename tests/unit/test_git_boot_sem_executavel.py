"""Boot numa máquina sem git instalado — reproduz o crash relatado.

GitPython roda `Git.refresh()` na importação (`import git`) e, por padrão,
levanta `ImportError` se não achar o executável `git` no PATH — derrubava o
processo inteiro (`backend.main`/`launcher.py`) antes mesmo do backend
inicializar, em qualquer máquina Windows limpa sem git instalado.

Cada teste roda um subprocesso Python isolado com PATH sem nenhum diretório
de git — só assim reproduz de verdade a condição (`import git` já executado
neste processo de teste não re-levanta o erro numa segunda tentativa).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dir_has_git_executable(dirpath: str) -> bool:
    """True se `dirpath` contém um executável `git`/`git.exe` de verdade.

    Filtrar por substring "git" no NOME do diretório só funciona no Windows
    (`C:\\Program Files\\Git\\cmd`) — no Linux o git mora em `/usr/bin/git`,
    um diretório cujo nome não menciona "git". Checar o arquivo real
    funciona nas duas plataformas por construção.
    """
    exe_names = ("git.exe", "git") if os.name == "nt" else ("git",)
    return any((Path(dirpath) / name).is_file() for name in exe_names)


def _run_without_git_on_path(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    stripped = os.pathsep.join(
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if p and not _dir_has_git_executable(p)
    )
    env["PATH"] = stripped
    env.pop("GIT_PYTHON_REFRESH", None)
    env.pop("GIT_PYTHON_GIT_EXECUTABLE", None)
    return subprocess.run(  # noqa: S603 # nosec B603
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_git_realmente_nao_resolve_sem_git_no_path():
    """Sanidade do próprio setup do teste: confirma que git.exe não é
    encontrável no PATH filtrado (senão o teste principal não provaria nada)."""
    result = _run_without_git_on_path(
        "import shutil, sys; sys.exit(0 if shutil.which('git') is None else 1)"
    )
    assert result.returncode == 0, (
        "PATH filtrado ainda expõe um executável git — ajustar o filtro do teste"
    )


def test_import_tools_git_sem_git_no_path_nao_crasha():
    result = _run_without_git_on_path("import backend.tools.git; print('OK')")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_import_persistence_checkpoint_sem_git_no_path_nao_crasha():
    result = _run_without_git_on_path(
        "import backend.persistence.checkpoint; print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_import_backend_main_sem_git_no_path_nao_crasha():
    """Reproduz literalmente a cadeia do traceback relatado: launcher →
    backend.main → ... → tools/git.py → `import git`."""
    result = _run_without_git_on_path("import backend.main; print('OK')")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
