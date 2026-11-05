"""Experiment — backend SSH do sandbox. Reaproveita SshTransport (mockado
nos testes, sem conexão real)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.sandbox.policy import SandboxPolicy
from backend.sandbox.ssh import run_ssh_sandboxed


@pytest.mark.asyncio
async def test_missing_remote_host_fails_closed_without_connecting():
    result = await run_ssh_sandboxed(
        ["ls"], "/ws", SandboxPolicy(enabled=True, backend="ssh")
    )

    assert result.exit_code == 126
    assert "remote_host" in result.stderr


@pytest.mark.asyncio
async def test_successful_run_returns_remote_output():
    fake_result = MagicMock(exit_code=0, stdout="hi\n", stderr="")
    fake_transport = MagicMock()
    fake_transport.run = AsyncMock(return_value=fake_result)
    fake_transport.close = AsyncMock()

    with patch("backend.transport.ssh.SshTransport", return_value=fake_transport):
        result = await run_ssh_sandboxed(
            ["echo", "hi"],
            "/ws",
            SandboxPolicy(enabled=True, backend="ssh", remote_host="user@host"),
        )

    assert result.exit_code == 0
    assert result.stdout == "hi\n"
    fake_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_failure_returns_clear_error_and_still_closes():
    fake_transport = MagicMock()
    fake_transport.run = AsyncMock(side_effect=RuntimeError("connection refused"))
    fake_transport.close = AsyncMock()

    with patch("backend.transport.ssh.SshTransport", return_value=fake_transport):
        result = await run_ssh_sandboxed(
            ["ls"],
            "/ws",
            SandboxPolicy(enabled=True, backend="ssh", remote_host="user@host"),
        )

    assert result.exit_code == 125
    assert "connection refused" in result.stderr
    fake_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_remote_host_string_fails_closed_like_none():
    # Erro/borda: string vazia é falsy — mesmo caminho de "ausente".
    result = await run_ssh_sandboxed(
        ["ls"], "/ws", SandboxPolicy(enabled=True, backend="ssh", remote_host="")
    )

    assert result.exit_code == 126
    assert "remote_host" in result.stderr


@pytest.mark.asyncio
async def test_timeout_during_remote_run_returns_error_and_still_closes():
    fake_transport = MagicMock()
    fake_transport.run = AsyncMock(side_effect=TimeoutError("comando travou"))
    fake_transport.close = AsyncMock()

    with patch("backend.transport.ssh.SshTransport", return_value=fake_transport):
        result = await run_ssh_sandboxed(
            ["sleep", "999"],
            "/ws",
            SandboxPolicy(enabled=True, backend="ssh", remote_host="user@host"),
            timeout_s=0.05,
        )

    assert result.exit_code == 125
    assert "comando travou" in result.stderr
    fake_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_host_unreachable_returns_clear_error_and_still_closes():
    fake_transport = MagicMock()
    fake_transport.run = AsyncMock(side_effect=OSError("No route to host"))
    fake_transport.close = AsyncMock()

    with patch("backend.transport.ssh.SshTransport", return_value=fake_transport):
        result = await run_ssh_sandboxed(
            ["ls"],
            "/ws",
            SandboxPolicy(enabled=True, backend="ssh", remote_host="unreachable-host"),
        )

    assert result.exit_code == 125
    assert "No route to host" in result.stderr
    fake_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_constructed_with_policy_ssh_key_id():
    fake_result = MagicMock(exit_code=0, stdout="", stderr="")
    fake_transport = MagicMock()
    fake_transport.run = AsyncMock(return_value=fake_result)
    fake_transport.close = AsyncMock()

    with patch(
        "backend.transport.ssh.SshTransport", return_value=fake_transport
    ) as transport_cls:
        await run_ssh_sandboxed(
            ["true"],
            "/ws",
            SandboxPolicy(
                enabled=True,
                backend="ssh",
                remote_host="user@host",
                ssh_key_id="key-42",
            ),
        )

    transport_cls.assert_called_once_with(
        remote_host="user@host", ssh_key_id="key-42", user_id=None
    )


@pytest.mark.asyncio
async def test_close_is_awaited_even_when_run_returns_normally():
    fake_result = MagicMock(exit_code=0, stdout="ok\n", stderr="")
    fake_transport = MagicMock()
    fake_transport.run = AsyncMock(return_value=fake_result)
    fake_transport.close = AsyncMock()

    with patch("backend.transport.ssh.SshTransport", return_value=fake_transport):
        result = await run_ssh_sandboxed(
            ["true"],
            "/ws",
            SandboxPolicy(enabled=True, backend="ssh", remote_host="user@host"),
        )

    assert result.exit_code == 0
    fake_transport.close.assert_awaited_once()
