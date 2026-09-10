"""Tests para src/services/tool_resolver.py — toolset por usuário (S4).

resolve_tools = built-ins permitidas (tool_policy) + tools MCP do usuário.
Mocka tool_policy e plugins.get_user_mcp_tools para isolar a lógica.
"""

from __future__ import annotations

import pytest

from backend.services import tool_resolver


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeWorkspace:
    cwd = "C:/workspaces/demo"


@pytest.fixture
def fake_all_tools(monkeypatch):
    tools = [_FakeTool("file_read"), _FakeTool("terminal"), _FakeTool("grep")]
    monkeypatch.setattr(tool_resolver, "ALL_TOOL_SPECS", tools)
    return tools


@pytest.mark.asyncio
async def test_local_user_gets_all_tools(fake_all_tools, monkeypatch):
    """Sem user_id (CLI/local) → ALL_TOOLS, sem consulta de policy/MCP."""
    result = await tool_resolver.resolve_tools(None)
    assert [t.name for t in result] == ["file_read", "terminal", "grep"]


@pytest.mark.asyncio
async def test_filters_disabled_tools(fake_all_tools, monkeypatch):
    monkeypatch.setattr(
        "backend.rbac.tool_policy.is_allowed",
        lambda uid, name: name != "terminal",
    )

    async def _no_mcp(_uid):
        return []

    monkeypatch.setattr(tool_resolver, "get_user_mcp_tools", _no_mcp)

    result = await tool_resolver.resolve_tools("u1")
    names = [t.name for t in result]
    assert "terminal" not in names
    assert "file_read" in names
    assert "grep" in names


@pytest.mark.asyncio
async def test_appends_mcp_tools(fake_all_tools, monkeypatch):
    monkeypatch.setattr("backend.rbac.tool_policy.is_allowed", lambda uid, name: True)

    async def _mcp(_uid):
        return [_FakeTool("mcp_search")]

    monkeypatch.setattr(tool_resolver, "get_user_mcp_tools", _mcp)

    result = await tool_resolver.resolve_tools("u1")
    names = [t.name for t in result]
    assert "mcp_search" in names
    assert names.count("mcp_search") == 1
    # built-ins continuam presentes
    assert "file_read" in names


@pytest.mark.asyncio
async def test_mcp_failure_degrades_to_builtins(fake_all_tools, monkeypatch):
    monkeypatch.setattr("backend.rbac.tool_policy.is_allowed", lambda uid, name: True)

    async def _boom(_uid):
        raise RuntimeError("mcp down")

    monkeypatch.setattr(tool_resolver, "get_user_mcp_tools", _boom)

    # Falha de MCP não derruba a resolução — retorna só os built-ins.
    result = await tool_resolver.resolve_tools("u1")
    assert [t.name for t in result] == ["file_read", "terminal", "grep"]


@pytest.mark.asyncio
async def test_workspace_skill_resolution_usa_diretorio_autorizado(
    fake_all_tools, monkeypatch
):
    monkeypatch.setattr("backend.rbac.tool_policy.is_allowed", lambda uid, name: True)

    async def _no_mcp(_uid: str, **_kwargs: object) -> list[_FakeTool]:
        return []

    captured: dict[str, object] = {}

    def _list_paths(_uid: str, **kwargs: object) -> list[object]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(tool_resolver, "get_user_mcp_tools", _no_mcp)
    monkeypatch.setattr(tool_resolver, "list_skill_paths", _list_paths)
    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry.get",
        lambda workspace_id: _FakeWorkspace() if workspace_id == "ws-public" else None,
    )

    await tool_resolver.resolve_tools("u1", workspace_id="ws-public")

    assert captured["workspace_id"] == "C:/workspaces/demo"
