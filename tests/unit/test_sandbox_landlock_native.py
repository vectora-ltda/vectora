"""Linux-only checks run irreversible Landlock restrictions in a child process."""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest


@pytest.mark.skipif(
    sys.platform != "linux", reason="requires a real Linux Landlock kernel"
)
async def test_tcp_allowlist_denies_bind_even_on_a_listed_port() -> None:
    """An allowed outbound TCP port must not imply permission to host a server."""
    script = textwrap.dedent("""
        import ctypes
        import errno
        import socket
        from backend.sandbox.landlock import _apply_net_ruleset, _syscall_numbers

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.listen(1)
        numbers = _syscall_numbers()
        if numbers is None:
            print("Landlock syscall numbers unavailable for this architecture")
            raise SystemExit(77)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.syscall(numbers[0], 0, 0, 1) < 4:
            print("Landlock ABI V4 unavailable")
            raise SystemExit(77)
        assert _apply_net_ruleset(libc, numbers, (port,)), "Landlock V4 detected but policy application failed"
        with socket.socket() as server:
            try:
                server.bind(("127.0.0.1", port))
            except PermissionError as exc:
                assert exc.errno == errno.EACCES, exc
                print("bind denied for listed port")
            else:
                raise AssertionError("outbound allowlist unexpectedly permits TCP bind")
    """)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode == 77:
        pytest.skip(stdout.decode())
    assert process.returncode == 0, stderr.decode()
    assert stdout.decode().strip() == "bind denied for listed port"
