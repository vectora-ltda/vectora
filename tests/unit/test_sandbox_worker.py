"""Sandbox — backend.sandbox.worker: protocolo JSON-lines do loop que roda
DENTRO do jail (exec/read_file/write_file)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sandbox import worker


@pytest.mark.asyncio
async def test_exec_returns_stdout_stderr_exit_code(monkeypatch):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        worker.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    resp = await worker.handle_request(
        {"op": "exec", "id": 1, "command": ["echo", "ok"]}
    )

    assert resp == {"id": 1, "stdout": "ok\n", "stderr": "", "exit_code": 0}


@pytest.mark.asyncio
async def test_exec_nonzero_exit_code_propagated(monkeypatch):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b"boom\n"))
    proc.returncode = 2
    monkeypatch.setattr(
        worker.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    resp = await worker.handle_request({"op": "exec", "id": 2, "command": ["false"]})

    assert resp["exit_code"] == 2
    assert resp["stderr"] == "boom\n"


@pytest.mark.asyncio
async def test_exec_command_not_found_returns_error_not_exception(monkeypatch):
    async def _raise(*_args, **_kwargs):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", _raise)

    resp = await worker.handle_request({"op": "exec", "id": 3, "command": ["nope"]})

    assert resp["id"] == 3
    assert "error" in resp


@pytest.mark.asyncio
async def test_write_then_read_file_roundtrip(tmp_path):
    target = tmp_path / "f.txt"

    write_resp = await worker.handle_request(
        {"op": "write_file", "id": 4, "path": str(target), "content": "olá"}
    )
    read_resp = await worker.handle_request(
        {"op": "read_file", "id": 5, "path": str(target)}
    )

    assert write_resp == {"id": 4, "ok": True}
    assert read_resp == {"id": 5, "content": "olá"}


@pytest.mark.asyncio
async def test_read_file_missing_returns_error_not_exception(tmp_path):
    resp = await worker.handle_request(
        {"op": "read_file", "id": 6, "path": str(tmp_path / "missing.txt")}
    )

    assert resp["id"] == 6
    assert "error" in resp


@pytest.mark.asyncio
async def test_unknown_op_returns_error():
    resp = await worker.handle_request({"op": "delete_everything", "id": 7})

    assert resp == {"id": 7, "error": "op desconhecida: 'delete_everything'"}


class _StubReader:
    """Simula um StreamReader que já devolve EOF na primeira leitura —
    `main()` sai do loop imediatamente após aplicar os rlimits."""

    async def readline(self):
        return b""


def _stub_event_loop(monkeypatch):
    loop_mock = MagicMock()
    loop_mock.connect_read_pipe = AsyncMock(return_value=None)
    monkeypatch.setattr(worker.asyncio, "get_event_loop", lambda: loop_mock)
    monkeypatch.setattr(worker.asyncio, "StreamReader", _StubReader)
    monkeypatch.setattr(
        worker.asyncio, "StreamReaderProtocol", lambda _reader: MagicMock()
    )


@pytest.mark.asyncio
async def test_main_aplica_rlimits_antes_do_loop_le_env_lockdown(monkeypatch):
    # 4.2 — main() lê VECTORA_SANDBOX_LOCKDOWN (setado via --setenv pelo
    # bwrap conforme policy.lockdown) e aplica o perfil de rlimits certo
    # antes de entrar no loop RPC.
    monkeypatch.setenv("VECTORA_SANDBOX_LOCKDOWN", "1")
    applied = MagicMock()
    monkeypatch.setattr(worker, "apply_rlimits", applied)
    _stub_event_loop(monkeypatch)

    await worker.main()

    applied.assert_called_once_with(lockdown=True)


@pytest.mark.asyncio
async def test_main_sem_env_lockdown_usa_perfil_normal(monkeypatch):
    monkeypatch.delenv("VECTORA_SANDBOX_LOCKDOWN", raising=False)
    applied = MagicMock()
    monkeypatch.setattr(worker, "apply_rlimits", applied)
    _stub_event_loop(monkeypatch)

    await worker.main()

    applied.assert_called_once_with(lockdown=False)


@pytest.mark.asyncio
async def test_main_repassa_allow_tcp_ports_pro_apply_landlock(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTORA_SANDBOX_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("VECTORA_SANDBOX_RW_PATHS", "[]")
    monkeypatch.setenv("VECTORA_SANDBOX_RO_PATHS", "[]")
    monkeypatch.setenv("VECTORA_SANDBOX_ALLOW_TCP_PORTS", "[443, 8080]")
    monkeypatch.setattr(worker, "apply_rlimits", MagicMock())
    landlock_call = MagicMock()
    monkeypatch.setattr(worker, "apply_landlock", landlock_call)
    _stub_event_loop(monkeypatch)

    await worker.main()

    args, _kwargs = landlock_call.call_args
    assert args[2] == (443, 8080)


@pytest.mark.asyncio
async def test_main_sem_allow_tcp_ports_repassa_tupla_vazia(monkeypatch, tmp_path):
    # Erro/borda: ausência da env var (worker antigo/config sem a feature)
    # não pode quebrar — degrada pra tupla vazia, mesmo comportamento de
    # antes desta feature existir.
    monkeypatch.setenv("VECTORA_SANDBOX_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.delenv("VECTORA_SANDBOX_RW_PATHS", raising=False)
    monkeypatch.delenv("VECTORA_SANDBOX_RO_PATHS", raising=False)
    monkeypatch.delenv("VECTORA_SANDBOX_ALLOW_TCP_PORTS", raising=False)
    monkeypatch.setattr(worker, "apply_rlimits", MagicMock())
    landlock_call = MagicMock()
    monkeypatch.setattr(worker, "apply_landlock", landlock_call)
    _stub_event_loop(monkeypatch)

    await worker.main()

    args, _kwargs = landlock_call.call_args
    assert args[2] == ()
