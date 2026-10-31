"""Testes unitários para o compilador de políticas e snapshot de execução."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock

import pytest

from backend.rbac import tool_policy
from backend.services.egress_policy import EgressPolicy
from backend.services.policy_compiler import (
    PolicySnapshot,
    compile_policy_snapshot,
)
from backend.tools import browser as browser_tools
from backend.tools import browser_devtools
from backend.tools.context import ToolContext
from backend.vtypes.message import ToolCall


@pytest.fixture(autouse=True)
def mock_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executa a operação mock dns."""

    def _fake_getaddrinfo(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[tuple[object, ...]]:
        """Executa a operação fake getaddrinfo."""
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


def test_policy_snapshot_basic_allow() -> None:
    """Verifica o comportamento de policy snapshot basic allow."""
    snapshot = PolicySnapshot(
        user_id="alice",
        permission_mode="ask",
    )
    ok, _ = snapshot.is_tool_allowed("file_read")
    assert ok is True


def test_manual_policy_snapshot_does_not_inherit_stale_global_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshots construídos por adapters não devem nascer revogados."""
    monkeypatch.setitem(tool_policy._versions, "alice", 7)
    monkeypatch.setitem(tool_policy._versions, tool_policy.GLOBAL_SCOPE, 11)

    snapshot = PolicySnapshot(user_id="alice")

    assert snapshot.is_revoked() is False
    assert snapshot.validate_call("file_read", {"path": "README.md"}) == (True, "")

    ok, _ = snapshot.validate_call("file_read", {"path": "README.md"})
    assert ok is True


def test_policy_snapshot_disabled_tool() -> None:
    """Verifica o comportamento de policy snapshot disabled tool."""
    snapshot = PolicySnapshot(
        disabled_tools=frozenset({"terminal"}),
    )
    ok, reason = snapshot.is_tool_allowed("terminal")
    assert ok is False
    assert "desabilitada" in reason


def test_compiled_snapshot_is_revoked_after_user_policy_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uma mudança de ABAC revoga chamadas pendentes do mesmo run."""
    ctx = ToolContext(user_id="alice")
    snapshot = compile_policy_snapshot(ctx)
    assert snapshot.is_revoked() is False
    assert snapshot.user_policy_version is not None

    monkeypatch.setitem(
        tool_policy._versions, "alice", snapshot.user_policy_version + 1
    )
    ok, reason = snapshot.validate_call("file_read", {"path": "README.md"})

    assert ok is False
    assert "revogada" in reason


def test_compiled_snapshot_is_revoked_after_global_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O kill-switch global também interrompe a próxima chamada do run."""
    ctx = ToolContext(user_id="alice")
    snapshot = compile_policy_snapshot(ctx)
    assert snapshot.global_policy_version is not None
    monkeypatch.setitem(
        tool_policy._versions,
        tool_policy.GLOBAL_SCOPE,
        snapshot.global_policy_version + 1,
    )

    ok, reason = snapshot.validate_call("file_read", {"path": "README.md"})

    assert ok is False
    assert "revogada" in reason


def test_policy_snapshot_untrusted_content_blocks_destructive_tools() -> None:
    """Verifica o comportamento de policy snapshot untrusted content blocks destructive tools."""
    snapshot = PolicySnapshot(
        untrusted_content=True,
    )
    # Ferramenta segura permitida
    ok, _ = snapshot.is_tool_allowed("file_read")
    assert ok is True

    # Ferramentas destrutivas bloqueadas
    for tool_name in ("terminal", "file_write", "git_push", "computer_use"):
        ok, reason = snapshot.is_tool_allowed(tool_name)
        assert ok is False
        assert "untrusted_content" in reason


def test_policy_snapshot_git_force_push() -> None:
    """Verifica o comportamento de policy snapshot git force push."""
    snapshot = PolicySnapshot(allow_force_push=False)

    # Push normal
    ok, _ = snapshot.validate_call("git_push", {"remote": "origin", "force": False})
    assert ok is True

    # Force push bloqueado
    ok, reason = snapshot.validate_call("git_push", {"remote": "origin", "force": True})
    assert ok is False
    assert "force" in reason


def test_policy_snapshot_egress_violation() -> None:
    """Verifica o comportamento de policy snapshot egress violation."""
    policy = EgressPolicy(allowed_hosts=["allowed.com"])
    snapshot = PolicySnapshot(egress_policy=policy)

    ok, reason = snapshot.validate_call(
        "http_request", {"url": "https://forbidden.com/api", "method": "GET"}
    )
    assert ok is False
    assert "Egress policy violada" in reason


@pytest.mark.parametrize("tool_name", ["browser_navigate", "browser_new_tab"])
def test_policy_snapshot_browser_navigation_uses_egress_allowlist(
    tool_name: str,
) -> None:
    """URLs do Browser Workbench não podem contornar a política de egress."""
    snapshot = PolicySnapshot(egress_policy=EgressPolicy(allowed_hosts=["allowed.com"]))

    ok, reason = snapshot.validate_call(
        tool_name, {"url": "https://forbidden.com/private"}
    )

    assert ok is False
    assert "Egress policy violada" in reason

    ok, reason = snapshot.validate_call(tool_name, {"url": "https://allowed.com/path"})
    assert ok is True
    assert reason == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["browser_navigate", "browser_new_tab"])
async def test_browser_egress_block_happens_before_handler_effect(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """Uma URL bloqueada não pode alcançar o efeito do handler da tool."""
    snapshot = PolicySnapshot(egress_policy=EgressPolicy(allowed_hosts=["allowed.com"]))

    if tool_name == "browser_navigate":
        handler_effect = AsyncMock()
        monkeypatch.setattr(
            browser_tools, "compile_policy_snapshot", lambda _ctx: snapshot
        )
        monkeypatch.setattr(browser_tools, "get_browser_page", handler_effect)
        result = await browser_tools.browser_navigate(
            url="https://forbidden.com/private", ctx=ToolContext(workspace_id="ws")
        )
    else:
        handler_effect = AsyncMock(return_value="tab-1")
        monkeypatch.setattr(
            browser_devtools, "compile_policy_snapshot", lambda _ctx: snapshot
        )
        monkeypatch.setattr(browser_devtools, "new_tab", handler_effect)
        result = await browser_devtools.browser_new_tab(
            url="https://forbidden.com/private", ctx=ToolContext(workspace_id="ws")
        )

    assert "egress" in result.lower()
    handler_effect.assert_not_awaited()


def test_compile_policy_snapshot() -> None:
    """Verifica o comportamento de compile policy snapshot."""
    ctx = ToolContext(
        workspace_id="ws-123",
        user_id="bob",
        permission_mode="accept_edits",
        _extra={"untrusted_content": True},
    )
    snapshot = compile_policy_snapshot(ctx)
    assert snapshot.workspace_id == "ws-123"
    assert snapshot.user_id == "bob"
    assert snapshot.permission_mode == "accept_edits"
    assert snapshot.untrusted_content is True


def test_policy_snapshot_untrusted_content_mutable_http() -> None:
    """Verifica o comportamento de policy snapshot untrusted content mutable http."""
    snapshot = PolicySnapshot(untrusted_content=True)

    # GET e métodos seguros permitidos sob conteúdo não confiável
    ok, _ = snapshot.validate_call(
        "http_request", {"method": "GET", "url": "https://allowed.com"}
    )
    assert ok is True

    # Métodos mutáveis bloqueados
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        ok, reason = snapshot.validate_call(
            "http_request", {"method": m, "url": "https://allowed.com"}
        )
        assert ok is False
        assert "mutável" in reason


def test_policy_snapshot_call_mcp_tool_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica o comportamento de policy snapshot call mcp tool resolution."""
    snapshot = PolicySnapshot()

    # Sem servidor e sem mapeamento -> bloqueado
    ok, reason = snapshot.validate_call("call_mcp_tool", {"tool_name": "unknown_tool"})
    assert ok is False
    assert "resolver" in reason

    # Servidor resolvido via cliente MCP
    class _FakeMcpClient:
        def tools_by_server(self) -> dict[str, str]:
            """Executa a operação tools by server."""
            return {"known_tool": "my_server"}

    import backend.tools.mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "_mcp_client", _FakeMcpClient())

    ok, _ = snapshot.validate_call("call_mcp_tool", {"tool_name": "known_tool"})
    assert ok is True


def test_policy_snapshot_version_identifies_effective_egress_policy() -> None:
    """Snapshots with equal counters but different policy remain distinguishable."""
    first = PolicySnapshot(
        workspace_id="ws-a",
        user_policy_version=1,
        global_policy_version=2,
        egress_policy=EgressPolicy(allowed_hosts=["one.example"]),
    )
    second = PolicySnapshot(
        workspace_id="ws-a",
        user_policy_version=1,
        global_policy_version=2,
        egress_policy=EgressPolicy(allowed_hosts=["two.example"]),
    )
    equal = PolicySnapshot(
        workspace_id="ws-a",
        user_policy_version=1,
        global_policy_version=2,
        egress_policy=EgressPolicy(allowed_hosts=["one.example"]),
    )

    assert first.version != second.version
    assert first.version == equal.version
