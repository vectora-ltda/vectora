"""Sandbox — linux.run_local_sandboxed: erro/borda de bwrap ausente e de
timeout, sem depender do binário real estar instalado."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sandbox import linux as sandbox_linux
from backend.sandbox.dry_run import DENIED_SYSCALLS
from backend.sandbox.policy import SandboxPolicy


@pytest.mark.asyncio
async def test_missing_bwrap_binary_returns_clear_error_not_exception(
    tmp_path, monkeypatch
):
    async def _raise_not_found(*_args, **_kwargs):
        raise FileNotFoundError("bwrap")

    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", _raise_not_found
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["ls"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 127
    assert "bwrap" in result.stderr.lower()


@pytest.mark.asyncio
async def test_timeout_kills_process_and_reports_timed_out(tmp_path, monkeypatch):
    proc = MagicMock()

    async def _hang(*_args, **_kwargs):
        import asyncio

        await asyncio.sleep(10)

    proc.communicate = _hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["sleep", "10"], str(tmp_path), SandboxPolicy(enabled=True), timeout_s=0.05
    )

    assert result.exit_code == 124
    assert result.timed_out is True
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_successful_run_returns_stdout_stderr_and_exit_code(
    tmp_path, monkeypatch
):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["echo", "ok"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_nonzero_exit_code_is_propagated(tmp_path, monkeypatch):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b"comando falhou\n"))
    proc.returncode = 2
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["false"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 2
    assert result.stderr == "comando falhou\n"


@pytest.mark.asyncio
async def test_none_returncode_defaults_to_zero(tmp_path, monkeypatch):
    # Borda: processo terminado sem returncode setado (não deveria em teoria
    # acontecer após communicate(), mas o código defende com `or 0`).
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = None
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["true"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_invalid_utf8_output_is_replaced_not_raised(tmp_path, monkeypatch):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"\xff\xfe invalid", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["cat", "binfile"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    assert "invalid" in result.stdout


@pytest.mark.asyncio
async def test_permission_denied_returns_clear_error_not_exception(
    tmp_path, monkeypatch
):
    # Erro/borda: bwrap presente mas sem permissão de execução —
    # PermissionError não é FileNotFoundError, precisa degradar igual.
    async def _raise_permission_denied(*_args, **_kwargs):
        raise PermissionError("Permission denied: bwrap")

    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", _raise_permission_denied
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["ls"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr != ""


@pytest.mark.asyncio
async def test_process_dies_mid_execution_returns_its_exit_code(tmp_path, monkeypatch):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"parcial\n", b"killed\n"))
    proc.returncode = -9
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["long-running"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == -9
    assert result.stdout == "parcial\n"


@pytest.mark.asyncio
async def test_zero_timeout_still_returns_timed_out_result(tmp_path, monkeypatch):
    proc = MagicMock()

    async def _hang(*_args, **_kwargs):
        import asyncio

        await asyncio.sleep(10)

    proc.communicate = _hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["sleep", "10"], str(tmp_path), SandboxPolicy(enabled=True), timeout_s=0.0
    )

    assert result.timed_out is True
    assert result.exit_code == 124


@pytest.mark.asyncio
async def test_comando_bem_sucedido_devolve_stdout_e_exit_code_zero(
    tmp_path, monkeypatch
):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"ola mundo\n", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["echo", "ola mundo"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    assert result.stdout == "ola mundo\n"
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_returncode_none_apos_communicate_vira_exit_code_zero(
    tmp_path, monkeypatch
):
    # Borda: proc.returncode pode ficar None mesmo depois de communicate()
    # completar em alguns cenários de mock — `or 0` cobre isso, confirmar.
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = None
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["true"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_comando_com_codigo_de_saida_diferente_de_zero_e_preservado(
    tmp_path, monkeypatch
):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b"permission denied\n"))
    proc.returncode = 13
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["cat", "/root/secret"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 13
    assert "permission denied" in result.stderr


@pytest.mark.asyncio
async def test_stdout_com_bytes_invalidos_utf8_nao_quebra_decode(tmp_path, monkeypatch):
    # Erro/borda: saída binária corrompida (ex. comando que imprime bytes
    # crus) não pode levantar UnicodeDecodeError — errors="replace" cobre.
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"\xff\xfe\x00lixo", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["cat", "binfile"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    assert isinstance(result.stdout, str)


@pytest.mark.asyncio
async def test_comando_vazio_ainda_monta_argv_e_chama_subprocess(tmp_path, monkeypatch):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(sandbox_linux.asyncio, "create_subprocess_exec", create_mock)

    result = await sandbox_linux.run_local_sandboxed(
        [], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    create_mock.assert_called_once()


@pytest.mark.asyncio
async def test_wait_apos_kill_tambem_falha_nao_propaga_excecao_adicional(
    tmp_path, monkeypatch
):
    # Erro/borda: se proc.wait() após kill() também levantar, o comportamento
    # de timeout ainda deve ser reportado de forma controlada — aqui
    # garantimos que wait() é aguardado (não apenas kill "fire and forget").
    proc = MagicMock()

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(10)

    proc.communicate = _hang
    proc.kill = MagicMock()
    wait_mock = AsyncMock()
    proc.wait = wait_mock
    monkeypatch.setattr(
        sandbox_linux.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await sandbox_linux.run_local_sandboxed(
        ["sleep", "10"], str(tmp_path), SandboxPolicy(enabled=True), timeout_s=0.05
    )

    assert result.timed_out is True
    wait_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_duas_execucoes_concorrentes_nao_compartilham_estado(
    tmp_path, monkeypatch
):
    # Concorrência: duas chamadas simultâneas com procs distintos não podem
    # vazar resultado de uma pra outra.
    proc_a = MagicMock()
    proc_a.communicate = AsyncMock(return_value=(b"saida-a", b""))
    proc_a.returncode = 0
    proc_b = MagicMock()
    proc_b.communicate = AsyncMock(return_value=(b"saida-b", b""))
    proc_b.returncode = 1

    procs = iter([proc_a, proc_b])

    async def _create(*_args, **_kwargs):
        return next(procs)

    monkeypatch.setattr(sandbox_linux.asyncio, "create_subprocess_exec", _create)

    result_a, result_b = await asyncio.gather(
        sandbox_linux.run_local_sandboxed(
            ["echo", "a"], str(tmp_path), SandboxPolicy(enabled=True)
        ),
        sandbox_linux.run_local_sandboxed(
            ["echo", "b"], str(tmp_path), SandboxPolicy(enabled=True)
        ),
    )

    assert result_a.stdout == "saida-a"
    assert result_a.exit_code == 0
    assert result_b.stdout == "saida-b"
    assert result_b.exit_code == 1


class _FakeSeccompFilter:
    """Fake mínimo de `seccomp.SyscallFilter` — captura as regras aplicadas
    sem depender de libseccomp instalada (indisponível neste ambiente de
    dev Windows e no CI)."""

    def __init__(self, defaction):
        self.defaction = defaction
        self.rules: list[tuple[object, str]] = []

    def add_rule(self, action, name):
        if name == "syscall_inexistente":
            raise OSError(f"syscall {name!r} desconhecida nesta arch")
        self.rules.append((action, name))

    def export_bpf(self, fileobj):
        fileobj.write(b"BPF-PROGRAM")


def _install_fake_seccomp_module(monkeypatch) -> list[_FakeSeccompFilter]:
    import sys
    import types

    created: list[_FakeSeccompFilter] = []

    def _syscall_filter(defaction):
        f = _FakeSeccompFilter(defaction)
        created.append(f)
        return f

    fake_module = types.SimpleNamespace(
        SyscallFilter=_syscall_filter, ALLOW="ALLOW", KILL="KILL"
    )
    monkeypatch.setitem(sys.modules, "seccomp", fake_module)
    return created


def test_seccomp_ausente_degrada_para_none_sem_lancar(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_seccomp(name, *args, **kwargs):
        if name == "seccomp":
            raise ImportError("no module named seccomp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_seccomp)

    result = sandbox_linux.build_seccomp_filter()

    assert result is None


def test_seccomp_presente_nega_todas_as_denied_syscalls(monkeypatch):
    created = _install_fake_seccomp_module(monkeypatch)

    result = sandbox_linux.build_seccomp_filter()

    assert result == b"BPF-PROGRAM"
    (f,) = created
    assert f.defaction == "ALLOW"
    denied_names = {name for _action, name in f.rules}
    assert denied_names == set(DENIED_SYSCALLS)


def test_seccomp_syscall_desconhecida_na_arch_nao_e_fatal(monkeypatch):
    import sys
    import types

    fake_module = types.SimpleNamespace(
        SyscallFilter=_FakeSeccompFilter, ALLOW="ALLOW", KILL="KILL"
    )
    monkeypatch.setitem(sys.modules, "seccomp", fake_module)
    monkeypatch.setattr(
        sandbox_linux,
        "DENIED_SYSCALLS",
        (*DENIED_SYSCALLS, "syscall_inexistente"),
    )

    result = sandbox_linux.build_seccomp_filter()

    assert result == b"BPF-PROGRAM"
