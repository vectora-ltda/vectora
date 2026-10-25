"""Testes unitários para o compilador de políticas e snapshot de execução."""

from __future__ import annotations

import pytest

from backend.services.egress_policy import EgressPolicy
from backend.services.policy_compiler import (
    PolicySnapshot,
    compile_policy_snapshot,
)
from backend.tools.context import ToolContext
from backend.vtypes.message import ToolCall


def test_policy_snapshot_basic_allow():
    """Verifica o comportamento de policy snapshot basic allow."""
    snapshot = PolicySnapshot(
        user_id="alice",
        permission_mode="ask",
    )
    ok, _ = snapshot.is_tool_allowed("file_read")
    assert ok is True

    ok, _ = snapshot.validate_call("file_read", {"path": "README.md"})
    assert ok is True


def test_policy_snapshot_disabled_tool():
    """Verifica o comportamento de policy snapshot disabled tool."""
    snapshot = PolicySnapshot(
        disabled_tools=frozenset({"terminal"}),
    )
    ok, reason = snapshot.is_tool_allowed("terminal")
    assert ok is False
    assert "desabilitada" in reason


def test_policy_snapshot_untrusted_content_blocks_destructive_tools():
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


def test_policy_snapshot_git_force_push():
    """Verifica o comportamento de policy snapshot git force push."""
    snapshot = PolicySnapshot(allow_force_push=False)

    # Push normal
    ok, _ = snapshot.validate_call("git_push", {"remote": "origin", "force": False})
    assert ok is True

    # Force push bloqueado
    ok, reason = snapshot.validate_call("git_push", {"remote": "origin", "force": True})
    assert ok is False
    assert "force" in reason


def test_policy_snapshot_egress_violation():
    """Verifica o comportamento de policy snapshot egress violation."""
    policy = EgressPolicy(allowed_hosts=["allowed.com"])
    snapshot = PolicySnapshot(egress_policy=policy)

    ok, reason = snapshot.validate_call(
        "http_request", {"url": "https://forbidden.com/api", "method": "GET"}
    )
    assert ok is False
    assert "Egress policy violada" in reason


def test_compile_policy_snapshot():
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


def test_policy_snapshot_untrusted_content_mutable_http():
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


def test_policy_snapshot_call_mcp_tool_resolution(monkeypatch):
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
