"""list_terminals/close_terminal — paridade com o REST auxiliar do handler
de terminal (GET .../list, POST .../close), reaproveitando o mesmo
pty_registry."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.services.pty_registry import pty_registry
from backend.tools.context import ToolContext
from backend.tools.terminal_sessions import (
    close_terminal,
    list_terminals,
    read_terminal,
    write_terminal,
)


def _fake_session(
    terminal_id: str,
    thread_id: str,
    workspace_id: str,
    alive: bool = True,
    user_id: str = "local",
):
    return SimpleNamespace(
        terminal_id=terminal_id,
        thread_id=thread_id,
        workspace_id=workspace_id,
        user_id=user_id,
        consume_rate_limit=lambda: None,
        is_alive=lambda: alive,
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    pty_registry._sessions.clear()
    yield
    pty_registry._sessions.clear()


class TestListTerminals:
    @pytest.mark.asyncio
    async def test_lista_terminais_da_thread(self) -> None:
        pty_registry.add(_fake_session("t1", "thr-1", "ws-1"))
        pty_registry.add(_fake_session("t2", "thr-2", "ws-1"))

        result = await list_terminals(
            ctx=ToolContext(thread_id="thr-1", workspace_id="ws-1")
        )
        data = json.loads(result)
        assert [t["terminal_id"] for t in data["terminals"]] == ["t1"]

    @pytest.mark.asyncio
    async def test_sem_contexto_nao_lista_sessoes(self) -> None:
        pty_registry.add(_fake_session("t1", "thr-1", "ws-1"))
        pty_registry.add(_fake_session("t2", "thr-2", "ws-1"))

        result = await list_terminals(ctx=ToolContext())
        data = json.loads(result)
        assert data["terminals"] == []

    @pytest.mark.asyncio
    async def test_lista_apenas_thread_e_workspace(self) -> None:
        pty_registry.add(_fake_session("t1", "thr-1", "ws-1"))
        pty_registry.add(_fake_session("t2", "thr-1", "ws-2"))
        result = await list_terminals(
            ctx=ToolContext(thread_id="thr-1", workspace_id="ws-1")
        )
        assert [t["terminal_id"] for t in json.loads(result)["terminals"]] == ["t1"]

    @pytest.mark.asyncio
    async def test_sem_terminais_retorna_lista_vazia_sem_erro(self) -> None:
        result = await list_terminals(ctx=ToolContext())
        data = json.loads(result)
        assert data["terminals"] == []


class TestCloseTerminal:
    @pytest.mark.asyncio
    async def test_fecha_terminal_existente(self) -> None:
        session = _fake_session("t1", "thr-1", "ws-1")
        session.close = lambda: None
        pty_registry.add(session)

        result = await close_terminal(terminal_id="t1")
        data = json.loads(result)
        assert data == {"status": "closed", "terminal_id": "t1"}
        assert pty_registry.get("t1") is None

    @pytest.mark.asyncio
    async def test_terminal_inexistente_retorna_erro_claro(self) -> None:
        result = await close_terminal(terminal_id="nao-existe")
        data = json.loads(result)
        assert data["status"] == "error"

    @pytest.mark.asyncio
    async def test_terminal_id_vazio_retorna_erro(self) -> None:
        result = await close_terminal(terminal_id="")
        data = json.loads(result)
        assert data["status"] == "error"


class TestInteractiveTerminalTools:
    @pytest.mark.asyncio
    async def test_leitura_e_escrita_respeitam_contexto(self) -> None:
        class Session:
            user_id = "local"
            terminal_id = "t1"
            thread_id = "thr-1"
            workspace_id = "ws-1"

            def consume_rate_limit(self):
                return None

            def read_since(self, cursor, max_bytes):
                return {
                    "data": b"prompt: ",
                    "cursor": 8,
                    "alive": True,
                    "truncated": False,
                    "has_more": False,
                }

            def write_input(self, data, request_id):
                return {"status": "accepted", "terminal_id": "t1"}

        pty_registry.add(Session())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        ctx = ToolContext(thread_id="thr-1", workspace_id="ws-1")
        read = json.loads(await read_terminal("t1", ctx=ctx))
        assert read["output"] == "prompt: "
        assert (
            json.loads(await write_terminal("t1", "yes\n", "request-1", ctx=ctx))[
                "status"
            ]
            == "accepted"
        )

    @pytest.mark.asyncio
    async def test_usuario_diferente_nao_le_nem_escreve(self) -> None:
        class OwnedSession:
            user_id = "owner"
            terminal_id = "t-owned"
            thread_id = "thr-1"
            workspace_id = "ws-1"

            def consume_rate_limit(self):
                return None

            def read_since(self, cursor, max_bytes):
                raise AssertionError("usuário não autorizado não deve ler")

            def write_input(self, data, request_id):
                raise AssertionError("usuário não autorizado não deve escrever")

        pty_registry.add(OwnedSession())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        ctx = ToolContext(user_id="other", thread_id="thr-1", workspace_id="ws-1")
        assert (
            json.loads(await read_terminal("t-owned", ctx=ctx))["code"] == "not_found"
        )
        assert (
            json.loads(await write_terminal("t-owned", "x", "req", ctx=ctx))["code"]
            == "not_found"
        )
        assert json.loads(await list_terminals(ctx=ctx))["terminals"] == []

    @pytest.mark.asyncio
    async def test_escrita_exige_request_id_e_isolamento(self) -> None:
        pty_registry.add(_fake_session("t1", "thr-1", "ws-1"))
        assert (
            json.loads(
                await write_terminal(
                    "t1",
                    "x",
                    "",
                    ctx=ToolContext(thread_id="thr-1", workspace_id="ws-1"),
                )
            )["code"]
            == "request_id_required"
        )
        assert (
            json.loads(
                await read_terminal(
                    "t1", ctx=ToolContext(thread_id="thr-2", workspace_id="ws-1")
                )
            )["code"]
            == "not_found"
        )
