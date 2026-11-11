"""Testa a adaptação do electron-builder para a imagem oficial Snapcraft."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP = REPO_ROOT / ".github" / "scripts" / "setup-snapcraft-container.sh"


def _bash() -> str:
    executable = shutil.which("bash") or os.getenv("WORKFLOW_SIM_BASH")
    if not executable:
        pytest.skip("bash é necessário para simular o script do workflow")
    return executable


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> '{calls}'\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir, calls


def _setup(tmp_path: Path, bin_dir: Path) -> tuple[Path, Path]:
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    github_path = tmp_path / "github-path"
    subprocess.run(  # noqa: S603 - executa script fixo do próprio repositório
        [_bash(), str(SETUP)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_PATH": str(github_path),
            "SNAPCRAFT_IMAGE": "ghcr.io/canonical/snapcraft:8_core22",
        },
        check=True,
    )
    return runner_temp / "snapcraft-compat" / "snapcraft", github_path


def test_uses_official_image_and_adapts_legacy_snap_command(tmp_path: Path) -> None:
    bin_dir, calls = _fake_docker(tmp_path)
    wrapper, github_path = _setup(tmp_path, bin_dir)
    result = subprocess.run(  # noqa: S603 - wrapper gerado pelo script local testado
        [str(wrapper), "snap", "--confinement", "strict"],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    docker_calls = calls.read_text().splitlines()
    assert docker_calls[0] == "pull ghcr.io/canonical/snapcraft:8_core22"
    assert docker_calls[1].endswith(
        "ghcr.io/canonical/snapcraft:8_core22 pack --destructive-mode "
        "--confinement strict"
    )
    assert str(wrapper.parent) in github_path.read_text()


def test_preserves_non_snapcraft_command_arguments(tmp_path: Path) -> None:
    bin_dir, calls = _fake_docker(tmp_path)
    wrapper, _ = _setup(tmp_path, bin_dir)
    result = subprocess.run(  # noqa: S603 - wrapper gerado pelo script local testado
        [str(wrapper), "--version"],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        calls.read_text()
        .splitlines()[1]
        .endswith("ghcr.io/canonical/snapcraft:8_core22 --version")
    )
