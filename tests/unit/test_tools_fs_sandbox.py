"""Sandbox — terminal/file_write/file_edit roteiam pelo worker jailado
(`backend.sandbox.workspace_jail.jail_manager`) quando `vectora.toml`/
`[sandbox]` está habilitado; sem isso, comportamento atual é preservado."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.sandbox.workspace_jail import WorkerSpawnError
from backend.tools import fs as fs_mod
from backend.tools.context import ctx_from_config
from backend.vtypes import Workspace


@pytest.fixture
def trusted_ws(tmp_path, monkeypatch):
    from backend.workspace import workspace as ws_mod

    ws = Workspace(
        id="testws",
        name="testws",
        cwd=str(tmp_path),
        created_at="2024-01-01T00:00:00+00:00",
        trusted=True,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry, "get", lambda wid: ws if wid == "testws" else None
    )
    monkeypatch.setattr(ws_mod.workspace_registry, "get_or_create", lambda cwd=None: ws)
    return {"configurable": {"workspace_id": "testws"}}


def _write_sandbox_toml(tmp_path) -> None:
    (tmp_path / "vectora.toml").write_text(
        "[sandbox]\nenabled = true\n", encoding="utf-8"
    )


class _FakeWorker:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def request(self, op: str, **kwargs) -> dict:
        self.calls.append((op, kwargs))
        return self._responses.pop(0)


class TestFileWriteSandboxed:
    async def test_routes_through_jail_worker_when_enabled(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        _write_sandbox_toml(tmp_path)
        worker = _FakeWorker([{"ok": True}])
        monkeypatch.setattr(
            fs_mod.jail_manager, "get_or_spawn", AsyncMock(return_value=worker)
        )

        result = await fs_mod.file_write(
            file_path="out.txt", content="olá jail", ctx=ctx_from_config(trusted_ws)
        )

        assert "[OK]" in result
        assert worker.calls == [
            (
                "write_file",
                {"path": str(tmp_path / "out.txt"), "content": "olá jail"},
            )
        ]
        # nunca escreve direto no disco fora do worker — a "escrita real"
        # é responsabilidade do processo jailado, não do backend.
        assert not (tmp_path / "out.txt").exists()

    async def test_without_sandbox_config_writes_directly(self, tmp_path, trusted_ws):
        result = await fs_mod.file_write(
            file_path="out.txt", content="sem jail", ctx=ctx_from_config(trusted_ws)
        )

        assert "[OK]" in result
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "sem jail"

    async def test_worker_spawn_error_returns_clear_message_not_exception(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        _write_sandbox_toml(tmp_path)
        monkeypatch.setattr(
            fs_mod.jail_manager,
            "get_or_spawn",
            AsyncMock(side_effect=WorkerSpawnError("bwrap não está instalado")),
        )

        result = await fs_mod.file_write(
            file_path="out.txt", content="x", ctx=ctx_from_config(trusted_ws)
        )

        assert result.startswith("Error:")
        assert "bwrap" in result
        assert not (tmp_path / "out.txt").exists()


class TestFileEditSandboxed:
    async def test_edit_reads_and_writes_through_worker(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        _write_sandbox_toml(tmp_path)
        worker = _FakeWorker(
            [
                {"content": "olá mundo"},
                {"ok": True},
            ]
        )
        monkeypatch.setattr(
            fs_mod.jail_manager, "get_or_spawn", AsyncMock(return_value=worker)
        )

        result = await fs_mod.file_edit(
            file_path="f.txt",
            old_text="mundo",
            new_text="jail",
            ctx=ctx_from_config(trusted_ws),
        )

        assert "[OK]" in result
        assert worker.calls[0][0] == "read_file"
        assert worker.calls[1] == (
            "write_file",
            {"path": str(tmp_path / "f.txt"), "content": "olá jail"},
        )
        assert not (tmp_path / "f.txt").exists()

    async def test_edit_creates_file_when_missing_and_old_text_empty(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        _write_sandbox_toml(tmp_path)
        worker = _FakeWorker(
            [
                {"error": "[Errno 2] No such file or directory"},
                {"ok": True},
            ]
        )
        monkeypatch.setattr(
            fs_mod.jail_manager, "get_or_spawn", AsyncMock(return_value=worker)
        )

        result = await fs_mod.file_edit(
            file_path="novo.txt",
            old_text="",
            new_text="conteúdo novo",
            ctx=ctx_from_config(trusted_ws),
        )

        assert "created" in result.lower()
        assert worker.calls[1] == (
            "write_file",
            {"path": str(tmp_path / "novo.txt"), "content": "conteúdo novo"},
        )

    async def test_edit_missing_file_and_old_text_not_empty_returns_error(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        _write_sandbox_toml(tmp_path)
        worker = _FakeWorker([{"error": "not found"}])
        monkeypatch.setattr(
            fs_mod.jail_manager, "get_or_spawn", AsyncMock(return_value=worker)
        )

        result = await fs_mod.file_edit(
            file_path="sumido.txt",
            old_text="algo",
            new_text="outro",
            ctx=ctx_from_config(trusted_ws),
        )

        assert result == "Error: Text not found in file"


class TestTerminalSandboxed:
    @pytest.mark.asyncio
    async def test_routes_through_jail_worker_when_enabled(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        _write_sandbox_toml(tmp_path)
        worker = _FakeWorker([{"stdout": "ok\n", "stderr": "", "exit_code": 0}])
        monkeypatch.setattr(
            fs_mod.jail_manager, "get_or_spawn", AsyncMock(return_value=worker)
        )

        result = await fs_mod.terminal(
            command="echo ok", ctx=ctx_from_config(trusted_ws)
        )

        assert result.strip() == "ok"
        assert worker.calls == [("exec", {"command": ["sh", "-c", "echo ok"]})]

    @pytest.mark.asyncio
    async def test_worker_spawn_error_returns_clear_message(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        _write_sandbox_toml(tmp_path)
        monkeypatch.setattr(
            fs_mod.jail_manager,
            "get_or_spawn",
            AsyncMock(side_effect=WorkerSpawnError("bwrap não está instalado")),
        )

        result = await fs_mod.terminal(
            command="echo ok", ctx=ctx_from_config(trusted_ws)
        )

        assert result.startswith("Error:")
        assert "bwrap" in result

    @pytest.mark.asyncio
    async def test_stdin_input_without_sandbox_pending_returns_clear_error(
        self, tmp_path, trusted_ws, monkeypatch
    ):
        """Comando sandboxed roda até o fim numa chamada só — não registra
        nada em _pending_terminal, então tentar responder um prompt depois
        (stdin_input) retorna o erro já existente de "sem comando
        pendente", sem código extra pra esse caso."""
        _write_sandbox_toml(tmp_path)
        worker = _FakeWorker([{"stdout": "ok\n", "stderr": "", "exit_code": 0}])
        monkeypatch.setattr(
            fs_mod.jail_manager, "get_or_spawn", AsyncMock(return_value=worker)
        )
        await fs_mod.terminal(command="echo ok", ctx=ctx_from_config(trusted_ws))

        result = await fs_mod.terminal(stdin_input="y", ctx=ctx_from_config(trusted_ws))

        assert "não há comando pendente" in result
