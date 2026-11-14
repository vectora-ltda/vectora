"""Sandbox — WorkspaceJailManager/JailedWorker: worker persistente por
workspace (não um bwrap novo a cada tool call)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sandbox import workspace_jail as wj
from backend.sandbox.policy import SandboxPolicy


@pytest.fixture(autouse=True)
def _force_linux_platform(monkeypatch):
    # Testes abaixo exercitam o caminho bwrap nativo (Linux) — forçar a
    # plataforma torna isso determinístico independente do SO onde os
    # testes rodam de verdade (esta máquina de dev é Windows). Monkeypatcha
    # `_is_windows` (indireção local), não `sys.platform` direto — mexer no
    # `sys` global quebra pytest-asyncio/outras libs que também leem
    # `sys.platform` no mesmo processo. O caminho WSL2 (win32) tem sua
    # própria seção de testes abaixo.
    monkeypatch.setattr(wj, "_is_windows", lambda: False)


def _fake_proc(returncode: int | None = None) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_disabled_policy_raises_without_spawning(tmp_path, monkeypatch):
    spawn_mock = AsyncMock()
    monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
    manager = wj.WorkspaceJailManager()

    with pytest.raises(wj.WorkerSpawnError):
        await manager.get_or_spawn("ws-1", str(tmp_path), SandboxPolicy(enabled=False))

    spawn_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reuses_same_worker_for_same_workspace(tmp_path, monkeypatch):
    proc = _fake_proc()
    spawn_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
    manager = wj.WorkspaceJailManager()
    policy = SandboxPolicy(enabled=True)

    w1 = await manager.get_or_spawn("ws-1", str(tmp_path), policy)
    w2 = await manager.get_or_spawn("ws-1", str(tmp_path), policy)

    assert w1 is w2
    spawn_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_bwrap_raises_clear_error(tmp_path, monkeypatch):
    async def _raise_not_found(*_args, **_kwargs):
        raise FileNotFoundError("bwrap")

    monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", _raise_not_found)
    manager = wj.WorkspaceJailManager()

    with pytest.raises(wj.WorkerSpawnError, match="bwrap"):
        await manager.get_or_spawn("ws-1", str(tmp_path), SandboxPolicy(enabled=True))


@pytest.mark.asyncio
async def test_request_writes_json_line_and_parses_response(tmp_path, monkeypatch):
    proc = _fake_proc()
    response = json.dumps({"id": 1, "stdout": "ok\n", "stderr": "", "exit_code": 0})
    proc.stdout.readline = AsyncMock(return_value=(response + "\n").encode("utf-8"))
    spawn_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
    manager = wj.WorkspaceJailManager()

    worker = await manager.get_or_spawn(
        "ws-1", str(tmp_path), SandboxPolicy(enabled=True)
    )
    result = await worker.request("exec", command=["echo", "ok"])

    assert result == {"id": 1, "stdout": "ok\n", "stderr": "", "exit_code": 0}
    sent = json.loads(proc.stdin.write.call_args[0][0].decode("utf-8"))
    assert sent == {"op": "exec", "id": 1, "command": ["echo", "ok"]}


@pytest.mark.asyncio
async def test_request_raises_when_worker_closes_stdout(tmp_path, monkeypatch):
    proc = _fake_proc()
    proc.stdout.readline = AsyncMock(return_value=b"")
    monkeypatch.setattr(
        wj.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    manager = wj.WorkspaceJailManager()
    worker = await manager.get_or_spawn(
        "ws-1", str(tmp_path), SandboxPolicy(enabled=True)
    )

    with pytest.raises(RuntimeError, match="encerrou"):
        await worker.request("exec", command=["echo"])


@pytest.mark.asyncio
async def test_spawn_aplica_seccomp_fd_quando_filtro_disponivel(tmp_path, monkeypatch):
    proc = _fake_proc()
    spawn_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
    monkeypatch.setattr(wj, "build_seccomp_filter", lambda: b"BPF-PROGRAM")
    manager = wj.WorkspaceJailManager()

    await manager.get_or_spawn("ws-1", str(tmp_path), SandboxPolicy(enabled=True))

    assert spawn_mock.await_args is not None
    argv = spawn_mock.await_args.args
    assert argv[0] == "bwrap"
    assert "--seccomp" in argv
    kwargs = spawn_mock.await_args.kwargs
    assert kwargs["pass_fds"] != ()


@pytest.mark.asyncio
async def test_spawn_degrada_sem_seccomp_quando_libseccomp_ausente(
    tmp_path, monkeypatch
):
    proc = _fake_proc()
    spawn_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
    monkeypatch.setattr(wj, "build_seccomp_filter", lambda: None)
    manager = wj.WorkspaceJailManager()

    await manager.get_or_spawn("ws-1", str(tmp_path), SandboxPolicy(enabled=True))

    assert spawn_mock.await_args is not None
    argv = spawn_mock.await_args.args
    assert "--seccomp" not in argv
    kwargs = spawn_mock.await_args.kwargs
    assert kwargs["pass_fds"] == ()


@pytest.mark.asyncio
async def test_is_alive_reflects_returncode(tmp_path, monkeypatch):
    dead_proc = _fake_proc(returncode=1)
    spawn_mock = AsyncMock(return_value=dead_proc)
    monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
    manager = wj.WorkspaceJailManager()
    policy = SandboxPolicy(enabled=True)

    await manager.get_or_spawn("ws-1", str(tmp_path), policy)
    # worker morto (returncode != None) — próxima chamada nasce um novo
    await manager.get_or_spawn("ws-1", str(tmp_path), policy)

    assert spawn_mock.await_count == 2


def test_windows_path_to_wsl_traduz_drive_e_barras():
    assert wj._windows_path_to_wsl(r"C:\Users\dev\meu projeto") == (
        "/mnt/c/Users/dev/meu projeto"
    )


def test_windows_path_to_wsl_raiz_do_drive_sem_barra_sobrando():
    assert wj._windows_path_to_wsl("D:\\") == "/mnt/d"


class TestWsl2Spawn:
    """No Windows (`sys.platform == "win32"`), bwrap não roda nativo — o
    worker é roteado inteiro por dentro de uma distro WSL2 via `wsl.exe`."""

    @pytest.fixture(autouse=True)
    def _win32(self, monkeypatch):
        monkeypatch.setattr(wj, "_is_windows", lambda: True)

    @pytest.mark.asyncio
    async def test_roteia_via_wsl_exe_e_traduz_o_workspace_dir(self, monkeypatch):
        proc = _fake_proc()
        spawn_mock = AsyncMock(return_value=proc)
        monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
        monkeypatch.setattr(wj, "detect_wsl2", AsyncMock(return_value="Ubuntu"))
        monkeypatch.setattr(
            wj, "_bwrap_available_in_distro", AsyncMock(return_value=True)
        )
        manager = wj.WorkspaceJailManager()

        await manager.get_or_spawn(
            "ws-1", r"C:\Users\dev\projeto", SandboxPolicy(enabled=True)
        )

        assert spawn_mock.await_args is not None
        argv = spawn_mock.await_args.args
        assert argv[0] == "wsl.exe"
        assert argv[1:4] == ("-d", "Ubuntu", "--")
        assert "bwrap" in argv
        assert "/mnt/c/Users/dev/projeto" in argv
        assert "python3" in argv
        # sys.executable (path do Windows) nunca deve vazar pro argv do WSL.
        assert wj.sys.executable not in argv

    @pytest.mark.asyncio
    async def test_sem_wsl2_faz_fallback_pro_sandbox_soft(self, monkeypatch):
        """Sem WSL2 elegível: não bloqueia — degrada pro sandbox soft nativo
        do Windows (worker em subprocess puro, env limpo, cwd no workspace).
        Com `lockdown`, o modo soft só propaga o perfil de rlimits: não há
        `--unshare-net`, então a rede do worker não é bloqueada."""
        proc = _fake_proc()
        spawn_mock = AsyncMock(return_value=proc)
        monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
        monkeypatch.setattr(wj, "detect_wsl2", AsyncMock(return_value=None))
        monkeypatch.setattr(wj, "assign_job_object_kill_on_close", lambda pid: 1234)
        manager = wj.WorkspaceJailManager()

        worker = await manager.get_or_spawn(
            "ws-1",
            r"C:\Users\dev\projeto",
            SandboxPolicy(enabled=True, lockdown=True),
        )

        assert spawn_mock.await_args is not None
        argv = spawn_mock.await_args.args
        assert argv[0] == wj.sys.executable
        assert argv[1:3] == ("-m", "backend.sandbox.worker")
        assert "bwrap" not in argv
        assert "--unshare-net" not in argv
        kwargs = spawn_mock.await_args.kwargs
        assert kwargs["cwd"] == r"C:\Users\dev\projeto"
        assert kwargs["env"]["VECTORA_SANDBOX_SOFT"] == "1"
        assert kwargs["env"]["VECTORA_SANDBOX_LOCKDOWN"] == "1"
        assert kwargs["pass_fds"] == ()
        assert worker.job_handle == 1234

    @pytest.mark.asyncio
    async def test_wsl2_sem_bwrap_dentro_da_distro_faz_fallback_pro_soft(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """WSL2 presente mas sem bwrap: idem — fallback soft em vez de bloquear."""
        proc = _fake_proc()
        spawn_mock = AsyncMock(return_value=proc)
        monkeypatch.setattr(wj.asyncio, "create_subprocess_exec", spawn_mock)
        monkeypatch.setattr(wj, "detect_wsl2", AsyncMock(return_value="Ubuntu"))
        monkeypatch.setattr(
            wj, "_bwrap_available_in_distro", AsyncMock(return_value=False)
        )
        monkeypatch.setattr(wj, "assign_job_object_kill_on_close", lambda pid: 5678)
        manager = wj.WorkspaceJailManager()

        from backend.sandbox import policy as policy_module

        monkeypatch.setattr(policy_module, "_wsl2_distro_cache", "Ubuntu")
        policy = policy_module.parse_policy(tmp_path / "vectora.toml")
        assert policy.enabled is True
        assert policy.backend == "local"
        worker = await manager.get_or_spawn("ws-1", r"C:\Users\dev\projeto", policy)

        assert spawn_mock.await_args is not None
        argv = spawn_mock.await_args.args
        assert argv[:3] == (wj.sys.executable, "-m", "backend.sandbox.worker")
        assert "bwrap" not in argv
        assert "--unshare-net" not in argv
        kwargs = spawn_mock.await_args.kwargs
        assert kwargs["env"]["VECTORA_SANDBOX_SOFT"] == "1"
        assert worker.job_handle == 5678
        assert worker.isolation_enforced is False
        assert manager.has_effective_isolation("ws-1") is False
        from types import SimpleNamespace

        from backend.engine.hitl import should_require_approval
        from backend.tools.context import ToolContext
        from backend.workspace.workspace import workspace_registry

        monkeypatch.setattr(
            workspace_registry, "get", lambda _wid: SimpleNamespace(cwd=str(tmp_path))
        )
        monkeypatch.setattr(wj.jail_manager, "_workers", manager._workers)
        ctx = ToolContext(user_id="local", workspace_id="ws-1", permission_mode="plan")
        for tool in ("terminal", "file_write", "file_edit"):
            assert should_require_approval(tool, ctx, {}, []) is True


@pytest.mark.asyncio
async def test_request_normal_responde_sem_ser_afetado_por_timeout():
    """Caminho feliz: worker responde dentro do orçamento — nada é morto."""
    proc = _fake_proc()
    proc.stdout.readline = AsyncMock(return_value=b'{"ok": true}\n')
    worker = wj.JailedWorker(workspace_id="w1", proc=proc)

    result = await worker.request("ping", timeout_s=5.0)

    assert result == {"ok": True}
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_worker_travado_e_morto_no_timeout_e_respawna_depois(
    tmp_path, monkeypatch
):
    """Erro/borda: worker que trava sem morrer deixaria o workspace inteiro
    inacessível (é singleton). O timeout precisa matar o processo — depois
    do timeout o protocolo de linhas está dessincronizado, então reaproveitar
    o mesmo worker faria a resposta atrasada virar resposta da requisição
    seguinte."""

    async def _nunca_responde():
        await asyncio.sleep(3600)

    travado = _fake_proc()
    travado.stdout.readline = _nunca_responde

    def _matar():
        travado.returncode = -9

    travado.kill = MagicMock(side_effect=_matar)

    worker = wj.JailedWorker(workspace_id="w1", proc=travado)

    with pytest.raises(RuntimeError, match="não respondeu"):
        await worker.request("read_file", timeout_s=0.05)

    travado.kill.assert_called_once()
    assert worker.is_alive() is False

    # O worker morto não é reaproveitado: `get_or_spawn` sobe um novo, sem
    # mecanismo de restart próprio.
    manager = wj.WorkspaceJailManager()
    manager._workers["w1"] = worker
    novo = _fake_proc()
    monkeypatch.setattr(
        wj.asyncio, "create_subprocess_exec", AsyncMock(return_value=novo)
    )

    resultado = await manager.get_or_spawn(
        "w1", str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert resultado is not worker


@pytest.mark.asyncio
async def test_hitl_native_proof_cannot_be_replaced_by_a_soft_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = wj.WorkspaceJailManager()
    native = wj.JailedWorker("ws-isolated", _fake_proc(), isolation_enforced=True)
    manager._workers["ws-isolated"] = native
    assert manager.has_effective_isolation("ws-isolated") is True
    await manager.close("ws-isolated")
    soft_process = _fake_proc()
    soft = wj.JailedWorker("ws-isolated", soft_process, isolation_enforced=False)
    monkeypatch.setattr(manager, "_spawn", AsyncMock(return_value=soft))
    with pytest.raises(wj.WorkerSpawnError, match="refusing a soft replacement"):
        await manager.get_or_spawn(
            "ws-isolated", str(tmp_path), SandboxPolicy(enabled=True)
        )
    soft_process.terminate.assert_called_once()
    assert "ws-isolated" not in manager._workers
