"""Tests para src/services/plugins.py — registry de servidores MCP.

CRUD por usuário, persistido em disco, isolado entre usuários. O diretório base
é redirecionado para tmp_path nos testes.
"""

from __future__ import annotations

import pytest

from backend.workspace.plugins import (
    McpServer,
    add_server,
    build_connection,
    list_servers,
    remove_server,
)


@pytest.fixture(autouse=True)
def iso_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.workspace.plugins._plugins_dir", lambda: tmp_path / "mcp"
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_list_empty_initially():
    assert list_servers("u1") == []


def test_add_then_list():
    srv = McpServer(name="files", transport="stdio", command="mcp-files")
    add_server("u1", srv)
    servers = list_servers("u1")
    assert len(servers) == 1
    assert servers[0].name == "files"
    assert servers[0].command == "mcp-files"


def test_add_is_upsert_by_name():
    add_server("u1", McpServer(name="x", transport="stdio", command="a"))
    add_server("u1", McpServer(name="x", transport="stdio", command="b"))
    servers = list_servers("u1")
    assert len(servers) == 1
    assert servers[0].command == "b"


def test_remove_server():
    add_server("u1", McpServer(name="x", transport="stdio", command="a"))
    assert remove_server("u1", "x") is True
    assert list_servers("u1") == []


def test_remove_unknown_returns_false():
    assert remove_server("u1", "nao-existe") is False


# ---------------------------------------------------------------------------
# Isolamento por usuário
# ---------------------------------------------------------------------------


def test_users_are_isolated():
    add_server("a", McpServer(name="sa", transport="stdio", command="x"))
    add_server("b", McpServer(name="sb", transport="sse", url="http://h/sse"))
    assert [s.name for s in list_servers("a")] == ["sa"]
    assert [s.name for s in list_servers("b")] == ["sb"]


def test_persists_across_calls():
    add_server("u1", McpServer(name="p", transport="http", url="http://h/mcp"))
    # Nova leitura lê do disco (sem cache em memória entre chamadas)
    assert list_servers("u1")[0].url == "http://h/mcp"


def test_workspace_and_runtime_scopes_are_isolated():
    server = McpServer(name="scoped", transport="stdio", command="cmd")
    add_server("u1", server, "workspace", "ws-a")
    add_server("u1", server.model_copy(update={"name": "runtime"}), "runtime", "run-a")
    assert [item.name for item in list_servers("u1", "workspace", "ws-a")] == ["scoped"]
    assert list_servers("u1", "workspace", "ws-b") == []
    assert [item.name for item in list_servers("u1", "runtime", "run-a")] == ["runtime"]
    assert list_servers("u1", "runtime", "run-b") == []


# ---------------------------------------------------------------------------
# build_connection — formato do MultiServerMCPClient
# ---------------------------------------------------------------------------


def test_build_connection_stdio():
    conn = build_connection(
        McpServer(name="x", transport="stdio", command="cmd", args=["--a"])
    )
    assert conn == {
        "transport": "stdio",
        "command": "cmd",
        "args": ["--a"],
        "env_vars": [],
    }


def test_build_connection_stdio_repassa_env_vars_declaradas():
    conn = build_connection(
        McpServer(
            name="x",
            transport="stdio",
            command="cmd",
            env_vars=["MEU_TOKEN_DE_SERVICO"],
        )
    )
    assert conn["env_vars"] == ["MEU_TOKEN_DE_SERVICO"]


def test_build_connection_sse():
    conn = build_connection(McpServer(name="x", transport="sse", url="http://h/sse"))
    assert conn == {"transport": "sse", "url": "http://h/sse"}


def test_build_connection_http_maps_to_streamable():
    conn = build_connection(McpServer(name="x", transport="http", url="http://h/mcp"))
    assert conn == {"transport": "streamable_http", "url": "http://h/mcp"}
