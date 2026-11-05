"""Sandbox — runner.run_sandboxed: dispatcher que lê vectora.toml e decide
se roda dentro do sandbox ou sem wrapper (comportamento atual preservado
quando não há política configurada).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sandbox import runner


def _fake_proc(stdout=b"ok\n", stderr=b"", returncode=0):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_no_vectora_toml_runs_unsandboxed(tmp_path, monkeypatch):
    proc = _fake_proc(stdout=b"hello\n")
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["echo", "hello"], str(tmp_path))

    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    # Sem policy, comando roda direto — nunca prefixado com "bwrap".
    called_args = create_mock.call_args.args
    assert called_args[0] == "echo"


@pytest.mark.asyncio
async def test_enabled_local_policy_dispatches_to_bwrap(tmp_path, monkeypatch):
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "local"\n', encoding="utf-8"
    )
    proc = _fake_proc(stdout=b"sandboxed\n")
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 0
    assert result.stdout == "sandboxed\n"
    called_args = create_mock.call_args.args
    assert called_args[0] == "bwrap"


@pytest.mark.asyncio
async def test_unknown_backend_fails_closed_without_running_anything(
    tmp_path, monkeypatch
):
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "not-a-real-backend"\n', encoding="utf-8"
    )
    create_mock = AsyncMock()
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 126
    assert "not-a-real-backend" in result.stderr
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_backend_dispatches_to_docker_run(tmp_path, monkeypatch):
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "docker"\n', encoding="utf-8"
    )
    proc = _fake_proc(stdout=b"in-container\n")
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 0
    assert result.stdout == "in-container\n"
    called_args = create_mock.call_args.args
    assert called_args[0] == "docker"


@pytest.mark.asyncio
async def test_ssh_backend_without_remote_host_fails_closed(tmp_path):
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "ssh"\n', encoding="utf-8"
    )

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 126
    assert "remote_host" in result.stderr


@pytest.mark.asyncio
async def test_disabled_sandbox_section_runs_unsandboxed(tmp_path, monkeypatch):
    (tmp_path / "vectora.toml").write_text(
        "[sandbox]\nenabled = false\n", encoding="utf-8"
    )
    proc = _fake_proc()
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["true"], str(tmp_path))

    assert result.exit_code == 0
    called_args = create_mock.call_args.args
    assert called_args[0] == "true"


@pytest.mark.asyncio
async def test_ssh_backend_with_remote_host_dispatches_to_ssh_transport(
    tmp_path, monkeypatch
):
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "ssh"\nremote_host = "user@host"\n',
        encoding="utf-8",
    )
    fake_result = MagicMock(exit_code=0, stdout="remoto\n", stderr="")
    fake_transport = MagicMock()
    fake_transport.run = AsyncMock(return_value=fake_result)
    fake_transport.close = AsyncMock()
    monkeypatch.setattr(
        "backend.transport.ssh.SshTransport", MagicMock(return_value=fake_transport)
    )

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 0
    assert result.stdout == "remoto\n"


@pytest.mark.asyncio
async def test_modal_backend_without_sdk_installed_fails_with_clear_message(
    tmp_path, monkeypatch
):
    import sys

    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "modal"\n', encoding="utf-8"
    )
    monkeypatch.setitem(sys.modules, "modal", None)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 127
    assert "modal" in result.stderr.lower()


@pytest.mark.asyncio
async def test_empty_backend_string_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = ""\n', encoding="utf-8"
    )
    create_mock = AsyncMock()
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 126
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_vectora_toml_fails_closed_and_never_runs_command(
    tmp_path, monkeypatch
):
    (tmp_path / "vectora.toml").write_text("[sandbox\nbroken", encoding="utf-8")
    proc = _fake_proc(stdout=b"bwrap-output\n")
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    # TOML malformado -> LOCKED_DOWN_POLICY (backend "local", lockdown=True)
    # -> dispatcha pro bwrap, não roda o comando cru sem sandbox.
    called_args = create_mock.call_args.args
    assert called_args[0] == "bwrap"
    assert result is not None


@pytest.mark.asyncio
async def test_timeout_propagates_through_dispatcher_for_unsandboxed_run(
    tmp_path, monkeypatch
):
    proc = MagicMock()

    async def _hang(*_args, **_kwargs):
        import asyncio

        await asyncio.sleep(10)

    proc.communicate = _hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    monkeypatch.setattr(
        runner.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await runner.run_sandboxed(["sleep", "10"], str(tmp_path), timeout_s=0.05)

    assert result.exit_code == 124
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_case_sensitive_backend_name_fails_closed(tmp_path, monkeypatch):
    # "Local" com maiúscula não é igual a "local" registrado — fail-closed.
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "Local"\n', encoding="utf-8"
    )
    create_mock = AsyncMock()
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 126
    assert "Local" in result.stderr
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_comando_vazio_sem_policy_ainda_dispara_subprocess(tmp_path, monkeypatch):
    # Borda: lista de comando vazia não é validada pelo runner — quem
    # decide se isso é um erro é o subprocess/shell, não o dispatcher.
    proc = _fake_proc()
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed([], str(tmp_path))

    assert result.exit_code == 0
    create_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_e_relida_do_disco_a_cada_chamada_nao_e_cacheada(
    tmp_path, monkeypatch
):
    # Duas chamadas sucessivas com vectora.toml alterado entre elas devem
    # respeitar a política nova — não pode haver cache implícito de policy.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\nenabled = false\n", encoding="utf-8")
    proc = _fake_proc()
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    await runner.run_sandboxed(["ls"], str(tmp_path))
    first_call_argv = create_mock.call_args.args

    toml_path.write_text(
        '[sandbox]\nenabled = true\nbackend = "local"\n', encoding="utf-8"
    )
    await runner.run_sandboxed(["ls"], str(tmp_path))
    second_call_argv = create_mock.call_args.args

    assert first_call_argv[0] == "ls"
    assert second_call_argv[0] == "bwrap"


@pytest.mark.asyncio
async def test_duas_workspaces_com_policies_diferentes_dispatcham_isoladamente(
    tmp_path, monkeypatch
):
    # Concorrência/isolamento: dois workspaces distintos, um com sandbox
    # ligado e outro desligado, não podem vazar a política um pro outro.
    ws_sandboxed = tmp_path / "ws_a"
    ws_sandboxed.mkdir()
    (ws_sandboxed / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "local"\n', encoding="utf-8"
    )
    ws_plain = tmp_path / "ws_b"
    ws_plain.mkdir()

    proc = _fake_proc()
    create_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result_a, result_b = await asyncio.gather(
        runner.run_sandboxed(["ls"], str(ws_sandboxed)),
        runner.run_sandboxed(["ls"], str(ws_plain)),
    )

    assert result_a.exit_code == 0
    assert result_b.exit_code == 0
    argv_calls = [call.args[0] for call in create_mock.call_args_list]
    assert "bwrap" in argv_calls
    assert "ls" in argv_calls


@pytest.mark.asyncio
async def test_backend_none_via_toml_nulo_falha_fechado(tmp_path, monkeypatch):
    # Erro/borda: backend explicitamente vazio já coberto; aqui um valor
    # com espaços em branco também deve falhar fechado (não faz strip
    # mágico que coincida com "local").
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "  local  "\n', encoding="utf-8"
    )
    create_mock = AsyncMock()
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_mock)

    result = await runner.run_sandboxed(["ls"], str(tmp_path))

    assert result.exit_code == 126
    create_mock.assert_not_awaited()


def _docker_workspace(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "docker"\n', encoding="utf-8"
    )
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _quota_limpa():
    # A contagem de execuções em voo é estado de módulo — um teste que
    # deixe resíduo envenenaria os seguintes.
    runner._active_batch_runs.clear()
    yield
    runner._active_batch_runs.clear()


@pytest.mark.asyncio
async def test_quota_de_execucoes_batch_rejeita_acima_do_limite(tmp_path, monkeypatch):
    ws = _docker_workspace(tmp_path)
    solta = asyncio.Event()
    limite = runner.MAX_CONCURRENT_BATCH_RUNS_PER_WORKSPACE
    todos_dentro = asyncio.Event()

    async def _backend_lento(_cmd, _dir, _policy, *, timeout_s):
        if runner._active_batch_runs.get(ws, 0) >= limite:
            todos_dentro.set()
        await solta.wait()
        return runner.SandboxResult(stdout="ok", stderr="", exit_code=0)

    monkeypatch.setitem(runner._BACKENDS, "docker", _backend_lento)

    em_voo = [
        asyncio.create_task(runner.run_sandboxed(["true"], ws)) for _ in range(limite)
    ]
    await asyncio.wait_for(todos_dentro.wait(), timeout=5.0)

    # Erro/borda: a execução além da quota é REJEITADA, não enfileirada —
    # esperar numa fila seria indistinguível de travamento pra quem está
    # no chat.
    excedente = await runner.run_sandboxed(["true"], ws)
    assert excedente.exit_code == 126
    assert "simultâneas" in excedente.stderr

    solta.set()
    await asyncio.gather(*em_voo)

    # Happy: liberado o slot, volta a executar normalmente.
    depois = await runner.run_sandboxed(["true"], ws)
    assert depois.exit_code == 0
    assert runner._active_batch_runs.get(ws) is None


@pytest.mark.asyncio
async def test_quota_e_por_workspace_e_nao_atinge_backends_nao_batch(
    tmp_path, monkeypatch
):
    ws_a = _docker_workspace(tmp_path / "a")
    ws_b = _docker_workspace(tmp_path / "b")
    solta = asyncio.Event()
    limite = runner.MAX_CONCURRENT_BATCH_RUNS_PER_WORKSPACE
    a_saturado = asyncio.Event()
    b_entrou = asyncio.Event()
    contagem_vista_por_b: dict[str, int] = {}

    async def _backend_lento(_cmd, workspace_dir, _policy, *, timeout_s):
        if workspace_dir == ws_b:
            contagem_vista_por_b.update(runner._active_batch_runs)
            b_entrou.set()
        elif runner._active_batch_runs.get(ws_a, 0) >= limite:
            a_saturado.set()
        await solta.wait()
        return runner.SandboxResult(stdout="ok", stderr="", exit_code=0)

    monkeypatch.setitem(runner._BACKENDS, "docker", _backend_lento)

    em_voo = [
        asyncio.create_task(runner.run_sandboxed(["true"], ws_a)) for _ in range(limite)
    ]
    await asyncio.wait_for(a_saturado.wait(), timeout=5.0)

    # Workspace saturado nunca impede outro: a quota é por workspace, não
    # global — b entra mesmo com a no limite.
    outro = asyncio.create_task(runner.run_sandboxed(["true"], ws_b))
    await asyncio.wait_for(b_entrou.wait(), timeout=5.0)
    assert contagem_vista_por_b[ws_b] == 1
    assert contagem_vista_por_b[ws_a] == limite

    solta.set()
    resultados = await asyncio.gather(*em_voo, outro)
    assert all(r.exit_code == 0 for r in resultados)

    # `local` reusa um worker existente em vez de criar ambiente novo —
    # não é backend batch e não entra na contagem.
    local_ws = tmp_path / "local"
    local_ws.mkdir()
    (local_ws / "vectora.toml").write_text(
        '[sandbox]\nenabled = true\nbackend = "local"\n', encoding="utf-8"
    )
    chamou: dict[str, dict[str, int] | None] = {"contagem_durante": None}

    async def _backend_local(_cmd, _dir, _policy, *, timeout_s):
        chamou["contagem_durante"] = dict(runner._active_batch_runs)
        return runner.SandboxResult(stdout="", stderr="", exit_code=0)

    monkeypatch.setitem(runner._BACKENDS, "local", _backend_local)
    await runner.run_sandboxed(["true"], str(local_ws))

    assert chamou["contagem_durante"] == {}
