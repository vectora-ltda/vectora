from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from utils.subprocess_compat import command_for_execution


@pytest.mark.skipif(os.name == "nt", reason="Fallback applies to POSIX launchers")
def test_executes_shebangless_shell_launcher_through_sh(tmp_path) -> None:
    launcher = tmp_path / "pnpm"
    launcher.write_text('# launcher without shebang\nprintf "%s\\n" "$1"\n')
    launcher.chmod(0o755)

    command = command_for_execution([str(launcher), "ok"])
    result = subprocess.run(  # noqa: S603 - executa apenas o launcher criado no tmp_path
        command, capture_output=True, text=True, check=True
    )

    assert command[:2] == [shutil.which("sh") or "/bin/sh", str(launcher)]
    assert result.stdout == "ok\n"


@pytest.mark.skipif(os.name == "nt", reason="Fallback applies to POSIX launchers")
def test_leaves_shebang_launchers_unchanged(tmp_path) -> None:
    launcher = tmp_path / "pnpm"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)

    assert command_for_execution([str(launcher), "install"]) == [
        str(launcher),
        "install",
    ]
