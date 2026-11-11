"""Testes das tentativas de instalação do Snapcraft no workflow Linux."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / ".github" / "scripts" / "install-snapcraft.sh"


def _fake_commands(tmp_path: Path, *, failures: int) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "attempts"
    sudo = bin_dir / "sudo"
    sudo.write_text(
        "#!/usr/bin/env bash\n"
        f"count=$(cat '{counter}' 2>/dev/null || echo 0)\n"
        "count=$((count + 1))\n"
        f"echo $count > '{counter}'\n"
        f"[ $count -gt {failures} ]\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    return bin_dir, counter


def test_retries_transient_snap_store_failure(tmp_path: Path) -> None:
    bin_dir, counter = _fake_commands(tmp_path, failures=2)
    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert counter.read_text().strip() == "3"
    assert "nova tentativa" in result.stdout


def test_fails_after_three_snap_store_failures(tmp_path: Path) -> None:
    bin_dir, counter = _fake_commands(tmp_path, failures=3)
    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert counter.read_text().strip() == "3"
    assert "Não foi possível instalar o Snapcraft" in result.stdout
