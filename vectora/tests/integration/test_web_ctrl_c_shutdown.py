"""Process-level regression test for ``vectora web`` Ctrl+C shutdown."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess  # nosec B404 - process is the test subject
import sys
from pathlib import Path

import pytest

from backend.scheduling.nats_sidecar import _resolve_binary

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        _resolve_binary() is None,
        reason="nats-server não encontrado; teste exige o sidecar real",
    ),
]


async def _read_until(
    stream: asyncio.StreamReader,
    marker: str,
    lines: list[str],
    budget_seconds: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + budget_seconds
    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        raw = await asyncio.wait_for(stream.readline(), timeout=remaining)
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace")
        lines.append(line)
        if marker in line:
            return
    pytest.fail(
        f"processo não emitiu {marker!r} em {budget_seconds}s: {''.join(lines)}"
    )


def _send_ctrl_c(proc: asyncio.subprocess.Process) -> None:
    if sys.platform == "win32":
        # A process in its own Windows console group cannot receive
        # CTRL_C_EVENT from the parent. CTRL_BREAK_EVENT is the targeted
        # equivalent and now intentionally follows the same shutdown path.
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        os.kill(proc.pid, signal.SIGINT)


@pytest.mark.timeout(45)
async def test_web_ctrl_c_fecha_backend_e_nats_ate_o_prompt(tmp_path: Path) -> None:
    """Ctrl+C deve atravessar o processo real e fechar o NATS antes de sair."""
    env = dict(os.environ)
    env.update(
        {
            "VECTORA_HOME": str(tmp_path),
            "VECTORA_SKIP_STATIC": "1",
            "VECTORA_UVICORN_LOG_LEVEL": "warning",
        }
    )
    port = 0
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "backend.main",
        "web",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=str(Path(__file__).parents[2]),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **(
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if sys.platform == "win32"
            else {}
        ),
    )
    assert proc.stdout is not None
    lines: list[str] = []
    try:
        await _read_until(proc.stdout, "api/server: startup", lines, 30)
        await _read_until(proc.stdout, "nats_sidecar: pronto", lines, 15)
        startup = "".join(lines)
        nats_pid = re.search(r"nats_sidecar: pronto .*pid=(\d+)", startup)
        assert nats_pid is not None, startup

        _send_ctrl_c(proc)
        remaining, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        output = startup + remaining.decode("utf-8", errors="replace")

        assert "Vectora: sinal recebido" in output
        assert "api/server: shutdown" in output
        assert "api/server: sidecar NATS fechado" in output
        assert "Vectora: server.serve retornou após shutdown" in output
        assert proc.returncode == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
