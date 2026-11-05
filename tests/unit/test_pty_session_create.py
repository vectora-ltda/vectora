"""Sandbox — PtySession.create() com `policy`: o terminal interativo
do usuário compartilha a MESMA política jailada (bwrap) que `terminal`/
`file_write` da mesma workspace quando `[sandbox]` está habilitado.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.sandbox.policy import SandboxPolicy
from backend.services import pty_session as pty_mod
from backend.services.pty_session import PtySession


class _FakeSpawnedProc:
    def isalive(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _fake_backend(monkeypatch):
    """Substitui `_Backend.spawn` por um fake que captura o argv/cwd/env
    recebidos, sem depender de pywinpty/ptyprocess reais instalados."""
    spawn_mock = MagicMock(return_value=_FakeSpawnedProc())
    fake_backend = MagicMock()
    fake_backend.spawn = spawn_mock
    monkeypatch.setattr(pty_mod, "_Backend", fake_backend)
    monkeypatch.setattr(pty_mod.asyncio, "create_task", lambda *_a, **_k: MagicMock())
    return spawn_mock


def test_sem_policy_spawna_shell_padrao_sem_bwrap(_fake_backend, tmp_path):
    session = PtySession.create(
        terminal_id="t1", workspace_id="ws-1", thread_id="th-1", cwd=str(tmp_path)
    )

    cmd = _fake_backend.call_args[0][0]
    assert cmd[0] != "bwrap"
    assert isinstance(session, PtySession)


def test_policy_desabilitada_nao_envolve_com_bwrap(_fake_backend, tmp_path):
    PtySession.create(
        terminal_id="t1",
        workspace_id="ws-1",
        thread_id="th-1",
        cwd=str(tmp_path),
        policy=SandboxPolicy(enabled=False),
    )

    cmd = _fake_backend.call_args[0][0]
    assert cmd[0] != "bwrap"


def test_policy_habilitada_backend_local_com_bwrap_disponivel_envolve_argv(
    _fake_backend, tmp_path, monkeypatch
):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/bwrap")

    PtySession.create(
        terminal_id="t1",
        workspace_id="ws-1",
        thread_id="th-1",
        cwd=str(tmp_path),
        policy=SandboxPolicy(enabled=True, backend="local"),
    )

    cmd = _fake_backend.call_args[0][0]
    assert cmd[0] == "bwrap"
    assert str(tmp_path) in cmd


def test_policy_habilitada_sem_bwrap_no_sistema_falha_com_mensagem_clara(
    _fake_backend, tmp_path, monkeypatch
):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="bwrap"):
        PtySession.create(
            terminal_id="t1",
            workspace_id="ws-1",
            thread_id="th-1",
            cwd=str(tmp_path),
            policy=SandboxPolicy(enabled=True, backend="local"),
        )

    _fake_backend.assert_not_called()


def test_policy_habilitada_backend_nao_local_falha_com_mensagem_clara(
    _fake_backend, tmp_path
):
    with pytest.raises(RuntimeError, match="docker"):
        PtySession.create(
            terminal_id="t1",
            workspace_id="ws-1",
            thread_id="th-1",
            cwd=str(tmp_path),
            policy=SandboxPolicy(enabled=True, backend="docker"),
        )

    _fake_backend.assert_not_called()
