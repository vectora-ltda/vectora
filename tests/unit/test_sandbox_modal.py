"""Experiment — backend Modal do sandbox. SDK mockado via sys.modules (sem
custo real em CI, sem exigir credenciais)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from backend.sandbox.policy import SandboxPolicy


@pytest.fixture(autouse=True)
def _no_real_modal_module(monkeypatch):
    # Garante que um `modal` real instalado no ambiente não interfira —
    # cada teste controla explicitamente se o import deve funcionar.
    monkeypatch.delitem(sys.modules, "modal", raising=False)


@pytest.mark.asyncio
async def test_missing_modal_sdk_degrades_with_clear_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "modal", None)  # força ImportError

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(["ls"], "/ws", SandboxPolicy(enabled=True))

    assert result.exit_code == 127
    assert "pip install modal" in result.stderr


@pytest.mark.asyncio
async def test_successful_run_returns_sandbox_output(monkeypatch):
    fake_process = MagicMock()
    fake_process.wait.return_value = 0
    fake_process.stdout.read.return_value = "hello\n"
    fake_process.stderr.read.return_value = ""

    fake_sandbox = MagicMock()
    fake_sandbox.exec.return_value = fake_process

    fake_app_cls = MagicMock()
    fake_app_cls.lookup.return_value = MagicMock()

    fake_modal = types.ModuleType("modal")
    fake_modal.App = fake_app_cls  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox = MagicMock()  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox.create.return_value = fake_sandbox
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(
        ["echo", "hello"], "/ws", SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    fake_sandbox.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_sdk_failure_returns_clear_error_offering_other_backends(monkeypatch):
    fake_modal = types.ModuleType("modal")
    fake_modal.App = MagicMock()  # ty: ignore[unresolved-attribute]
    fake_modal.App.lookup.side_effect = RuntimeError("invalid token")
    fake_modal.Sandbox = MagicMock()  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(["ls"], "/ws", SandboxPolicy(enabled=True))

    assert result.exit_code == 125
    assert "outro backend" in result.stderr


def _fake_modal_module(*, exec_side_effect=None, wait_return=0):
    fake_process = MagicMock()
    if exec_side_effect is not None:
        fake_process.wait.side_effect = exec_side_effect
    else:
        fake_process.wait.return_value = wait_return
    fake_process.stdout.read.return_value = ""
    fake_process.stderr.read.return_value = ""

    fake_sandbox = MagicMock()
    fake_sandbox.exec.return_value = fake_process

    fake_modal = types.ModuleType("modal")
    fake_modal.App = MagicMock()  # ty: ignore[unresolved-attribute]
    fake_modal.App.lookup.return_value = MagicMock()
    fake_modal.Sandbox = MagicMock()  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox.create.return_value = fake_sandbox
    return fake_modal, fake_sandbox, fake_process


@pytest.mark.asyncio
async def test_timeout_no_modal_cloud_devolve_timed_out(monkeypatch):
    # Erro/borda: sandbox.exec()/wait() nunca retorna dentro do orçamento
    # (timeout_s + 10.0) — o wrapper de asyncio.wait_for deve reportar
    # timed_out=True, sem depender de bloquear uma thread real (que
    # vazaria do pool do executor default e travaria testes seguintes).
    fake_modal, _fake_sandbox, _fake_process = _fake_modal_module()
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    import backend.sandbox.modal as sandbox_modal

    async def _raise_timeout(*_a, **_kw):
        raise TimeoutError

    monkeypatch.setattr(sandbox_modal.asyncio, "wait_for", _raise_timeout)

    result = await sandbox_modal.run_modal_sandboxed(
        ["sleep", "999"], "/ws", SandboxPolicy(enabled=True), timeout_s=0.01
    )

    assert result.timed_out is True
    assert result.exit_code == 124


@pytest.mark.asyncio
async def test_sandbox_terminate_e_chamado_mesmo_quando_exec_falha(monkeypatch):
    # O finally garante terminate() mesmo em falha no meio da execução
    # (comando que quebra o processo remoto) — não vaza sandbox cloud
    # rodando indefinidamente (custo real).
    fake_modal, fake_sandbox, _fake_process = _fake_modal_module()
    fake_sandbox.exec.side_effect = RuntimeError("comando crashou no meio")
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(["crash"], "/ws", SandboxPolicy(enabled=True))

    assert result.exit_code == 125
    fake_sandbox.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_comando_com_lista_vazia_ainda_chama_sandbox_exec(monkeypatch):
    fake_modal, fake_sandbox, _fake_process = _fake_modal_module()
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed([], "/ws", SandboxPolicy(enabled=True))

    assert result.exit_code == 0
    fake_sandbox.exec.assert_called_once_with(timeout=60)


@pytest.mark.asyncio
async def test_exit_code_diferente_de_zero_e_preservado(monkeypatch):
    fake_modal, _fake_sandbox, fake_process = _fake_modal_module(wait_return=7)
    fake_process.stderr.read.return_value = "falhou no remoto\n"
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(["false"], "/ws", SandboxPolicy(enabled=True))

    assert result.exit_code == 7
    assert result.stderr == "falhou no remoto\n"


@pytest.mark.asyncio
async def test_app_lookup_cria_app_se_nao_existir(monkeypatch):
    fake_modal, _fake_sandbox, _fake_process = _fake_modal_module()
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    await run_modal_sandboxed(["ls"], "/ws", SandboxPolicy(enabled=True))

    fake_modal.App.lookup.assert_called_once_with(
        "vectora-sandbox", create_if_missing=True
    )


@pytest.mark.asyncio
async def test_workspace_dir_nao_afeta_o_exec_do_backend_modal(monkeypatch):
    # Invariante documentada no docstring do módulo: modal roda em
    # filesystem cloud isolado, `workspace_dir` não é montado nem afeta o
    # exec. De `policy` só o perfil de recurso é lido (teste próprio).
    fake_modal, _fake_sandbox, _fake_process = _fake_modal_module()
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result_a = await run_modal_sandboxed(
        ["ls"], "/qualquer/coisa", SandboxPolicy(enabled=True, rw_paths=("/x",))
    )
    result_b = await run_modal_sandboxed(
        ["ls"], "/outro/lugar", SandboxPolicy(enabled=False)
    )

    assert result_a.exit_code == result_b.exit_code == 0


@pytest.mark.asyncio
async def test_import_error_message_offers_other_backends(monkeypatch):
    monkeypatch.setitem(sys.modules, "modal", None)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(["ls"], "/ws", SandboxPolicy(enabled=True))

    assert "local" in result.stderr
    assert "docker" in result.stderr
    assert "ssh" in result.stderr


@pytest.mark.asyncio
async def test_sandbox_terminate_called_even_when_exec_raises(monkeypatch):
    fake_sandbox = MagicMock()
    fake_sandbox.exec.side_effect = RuntimeError("exec falhou")

    fake_app_cls = MagicMock()
    fake_app_cls.lookup.return_value = MagicMock()

    fake_modal = types.ModuleType("modal")
    fake_modal.App = fake_app_cls  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox = MagicMock()  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox.create.return_value = fake_sandbox
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(["ls"], "/ws", SandboxPolicy(enabled=True))

    assert result.exit_code == 125
    fake_sandbox.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_nonzero_exit_code_from_sandbox_process_is_propagated(monkeypatch):
    fake_process = MagicMock()
    fake_process.wait.return_value = 3
    fake_process.stdout.read.return_value = ""
    fake_process.stderr.read.return_value = "erro remoto\n"

    fake_sandbox = MagicMock()
    fake_sandbox.exec.return_value = fake_process

    fake_app_cls = MagicMock()
    fake_app_cls.lookup.return_value = MagicMock()

    fake_modal = types.ModuleType("modal")
    fake_modal.App = fake_app_cls  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox = MagicMock()  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox.create.return_value = fake_sandbox
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    result = await run_modal_sandboxed(["false"], "/ws", SandboxPolicy(enabled=True))

    assert result.exit_code == 3
    assert result.stderr == "erro remoto\n"
    fake_sandbox.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_timeout_returns_clear_error_without_raising(monkeypatch):
    # Mocka asyncio.wait_for diretamente em vez de correr uma corrida real
    # contra um asyncio.sleep(N) perto do limite do timeout — testes que
    # dependem de tempo real perto da borda são inerentemente instáveis
    # (a duração do sleep pode terminar ANTES do timeout por poucos ms).
    fake_modal = types.ModuleType("modal")
    fake_modal.App = MagicMock()  # ty: ignore[unresolved-attribute]
    fake_modal.Sandbox = MagicMock()  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    import backend.sandbox.modal as sandbox_modal

    async def _raise_timeout(*_a, **_kw):
        raise TimeoutError

    monkeypatch.setattr(sandbox_modal.asyncio, "wait_for", _raise_timeout)

    result = await sandbox_modal.run_modal_sandboxed(
        ["ls"], "/ws", SandboxPolicy(enabled=True), timeout_s=0.01
    )

    assert result.exit_code == 124
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_sandbox_recebe_teto_de_cpu_e_memoria_por_perfil(monkeypatch):
    """Sem teto explícito, um comando em loop roda no limite default da
    conta — e aqui a execução é remota e cobrada de verdade."""
    fake_modal, _sandbox, _process = _fake_modal_module()
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    from backend.sandbox.modal import run_modal_sandboxed

    await run_modal_sandboxed(["true"], "/ws", SandboxPolicy(enabled=True))
    normal = fake_modal.Sandbox.create.call_args.kwargs
    assert normal["cpu"] == 2
    assert normal["memory"] == 2048

    # Erro/borda: lockdown é pra código não-confiável — orçamento menor,
    # nunca o mesmo do perfil normal.
    await run_modal_sandboxed(
        ["true"], "/ws", SandboxPolicy(enabled=True, lockdown=True)
    )
    locked = fake_modal.Sandbox.create.call_args.kwargs
    assert locked["cpu"] == 1
    assert locked["memory"] == 1024
    assert locked["cpu"] < normal["cpu"]
    assert locked["memory"] < normal["memory"]
