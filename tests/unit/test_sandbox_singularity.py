"""Backend Singularity/Apptainer do sandbox. build_singularity_command
testável sem o binário instalado; execução real mockada — mesmo padrão de
test_sandbox_docker.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sandbox import singularity as singularity_mod
from backend.sandbox.policy import SandboxPolicy
from backend.sandbox.singularity import (
    _resolve_binary,
    build_singularity_command,
    run_singularity_sandboxed,
)


@pytest.fixture(autouse=True)
def _reset_binary_cache():
    singularity_mod._reset_binary_cache_for_tests()
    yield
    singularity_mod._reset_binary_cache_for_tests()


def test_build_command_uses_default_image_and_binds_workspace():
    argv = build_singularity_command(
        SandboxPolicy(enabled=True), "/ws", ["ls"], binary="singularity"
    )

    assert argv[:2] == ["singularity", "exec"]
    assert "--bind" in argv
    bind_idx = argv.index("--bind")
    assert argv[bind_idx + 1] == "/ws:/ws"
    assert argv[-2:] == ["docker://python:3.13-slim", "ls"]


def test_build_command_uses_configured_image():
    argv = build_singularity_command(
        SandboxPolicy(enabled=True, docker_image="docker://node:20-slim"),
        "/ws",
        ["true"],
        binary="apptainer",
    )

    assert argv[0] == "apptainer"
    assert "docker://node:20-slim" in argv


def test_build_command_always_applies_hardening_flags():
    argv = build_singularity_command(
        SandboxPolicy(enabled=True), "/ws", ["true"], binary="singularity"
    )

    assert "--containall" in argv
    assert "--no-home" in argv
    assert "--writable-tmpfs" in argv


def test_build_command_lockdown_adds_net_isolation():
    argv = build_singularity_command(
        SandboxPolicy(enabled=True, lockdown=True),
        "/ws",
        ["true"],
        binary="singularity",
    )

    assert "--net" in argv


def test_build_command_lockdown_false_omits_net_flag():
    argv = build_singularity_command(
        SandboxPolicy(enabled=True, lockdown=False),
        "/ws",
        ["true"],
        binary="singularity",
    )

    assert "--net" not in argv


class TestResolveBinary:
    def test_prefers_singularity_over_apptainer(self, monkeypatch):
        monkeypatch.setattr(
            singularity_mod.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        assert _resolve_binary() == "singularity"

    def test_falls_back_to_apptainer(self, monkeypatch):
        monkeypatch.setattr(
            singularity_mod.shutil,
            "which",
            lambda name: "/usr/bin/apptainer" if name == "apptainer" else None,
        )
        assert _resolve_binary() == "apptainer"

    def test_none_when_neither_installed(self, monkeypatch):
        monkeypatch.setattr(singularity_mod.shutil, "which", lambda _name: None)
        assert _resolve_binary() is None

    def test_result_is_cached(self, monkeypatch):
        calls = []

        def _which(name: str) -> str | None:
            calls.append(name)
            return "/usr/bin/singularity" if name == "singularity" else None

        monkeypatch.setattr(singularity_mod.shutil, "which", _which)
        assert _resolve_binary() == "singularity"
        assert _resolve_binary() == "singularity"
        # Segunda chamada não re-consulta shutil.which — cache por processo.
        assert calls == ["singularity"]


@pytest.mark.asyncio
async def test_neither_binary_installed_returns_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(singularity_mod.shutil, "which", lambda _name: None)

    result = await run_singularity_sandboxed(
        ["ls"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 127
    assert (
        "singularity" in result.stderr.lower() or "apptainer" in result.stderr.lower()
    )


@pytest.mark.asyncio
async def test_successful_run_returns_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        singularity_mod.shutil,
        "which",
        lambda name: "/usr/bin/singularity" if name == "singularity" else None,
    )
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"hi\n", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        "backend.sandbox.singularity.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    result = await run_singularity_sandboxed(
        ["echo", "hi"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    assert result.stdout == "hi\n"


@pytest.mark.asyncio
async def test_timeout_kills_process_and_reports_timed_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        singularity_mod.shutil,
        "which",
        lambda name: "/usr/bin/singularity" if name == "singularity" else None,
    )
    proc = MagicMock()

    async def _hang(*_args, **_kwargs):
        import asyncio

        await asyncio.sleep(10)

    proc.communicate = _hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    monkeypatch.setattr(
        "backend.sandbox.singularity.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    result = await run_singularity_sandboxed(
        ["sleep", "10"], str(tmp_path), SandboxPolicy(enabled=True), timeout_s=0.05
    )

    assert result.exit_code == 124
    assert result.timed_out is True
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_permission_denied_returns_clear_error_not_exception(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        singularity_mod.shutil,
        "which",
        lambda name: "/usr/bin/singularity" if name == "singularity" else None,
    )

    async def _raise_permission_denied(*_args, **_kwargs):
        raise PermissionError("Permission denied: singularity")

    monkeypatch.setattr(
        "backend.sandbox.singularity.asyncio.create_subprocess_exec",
        _raise_permission_denied,
    )

    result = await run_singularity_sandboxed(
        ["ls"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 126
    assert "permissão" in result.stderr.lower()
