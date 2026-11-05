"""Run the black-box qualification suite against this installed Vectora."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

_PYTHON_VERSION = "3.13"
_PYTEST_VERSION = "9.1.1"
_PYTEST_TIMEOUT_VERSION = "2.4.0"


def _runtime_test_assets() -> tuple[Path, Path]:
    """Resolve the bundled test suite and uv executable in frozen and dev runs."""
    if getattr(sys, "frozen", False):
        runtime_dir = Path(sys.executable).resolve().parent / "vectora-test-runtime"
        return runtime_dir / "test_installed_runtime.py", runtime_dir / _uv_name()

    project_root = Path(__file__).resolve().parents[2]
    return (
        project_root / "installed_tests" / "test_installed_runtime.py",
        Path(os.environ.get("VECTORA_TEST_UV") or shutil.which("uv") or ""),
    )


def _uv_name() -> str:
    return "uv.exe" if sys.platform == "win32" else "uv"


def run_installed_runtime_tests() -> int:
    """Download isolated test tooling and run smoke tests against this binary."""
    suite_path, uv_path = _runtime_test_assets()
    if not suite_path.is_file():
        print(f"Suíte de instalação não encontrada: {suite_path}", file=sys.stderr)
        return 2
    pytest_config = suite_path.parent / "pytest.ini"
    if not pytest_config.is_file():
        print(f"Configuração da suíte não encontrada: {pytest_config}", file=sys.stderr)
        return 2
    if not uv_path.is_file():
        print(
            "O runtime uv não está disponível para baixar as dependências de teste. "
            "Reinstale o Vectora ou defina VECTORA_TEST_UV.",
            file=sys.stderr,
        )
        return 2

    if getattr(sys, "frozen", False):
        # No app empacotado, --tests sempre valida exatamente este executável.
        executable_value = sys.executable
    else:
        executable_value = os.environ.get(
            "VECTORA_INSTALLED_EXECUTABLE"
        ) or shutil.which("vectora")
    if not executable_value or not Path(executable_value).is_file():
        print(
            "Executável instalado não encontrado. Defina "
            "VECTORA_INSTALLED_EXECUTABLE para o binário do app.",
            file=sys.stderr,
        )
        return 2
    executable = Path(executable_value).resolve()
    cache_root = _test_cache_dir()
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(cache_root / "uv-cache"),
        "UV_NO_PROGRESS": "1",
        "VECTORA_INSTALLED_EXECUTABLE": str(executable),
        "PYTHONUNBUFFERED": "1",
    }
    command = [
        str(uv_path),
        "run",
        "--isolated",
        "--no-project",
        "--python",
        _PYTHON_VERSION,
        "--with",
        f"pytest=={_PYTEST_VERSION}",
        "--with",
        f"pytest-timeout=={_PYTEST_TIMEOUT_VERSION}",
        "pytest",
        "-c",
        str(pytest_config),
        "-q",
        "--timeout=90",
        str(suite_path),
    ]
    print("Baixando as dependências de teste para um ambiente isolado...")
    print(f"Validando a instalação: {executable}")
    try:
        # O executável e os argumentos são resolvidos acima e não passam por shell.
        result = subprocess.run(  # noqa: S603  # nosec B603
            command, env=env, check=False
        )
    except OSError as exc:
        print(f"Não foi possível iniciar o runner de testes: {exc}", file=sys.stderr)
        return 2
    if result.returncode == 0:
        print("Smoke tests da instalação passaram.")
    else:
        print(
            f"Smoke tests da instalação falharam (exit code {result.returncode}).",
            file=sys.stderr,
        )
    return result.returncode


def _test_cache_dir() -> Path:
    """Return a user-scoped cache path without writing into the install tree."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "Vectora" / "test-runtime"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Vectora" / "test-runtime"
    return (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "vectora"
        / "test-runtime"
    )
