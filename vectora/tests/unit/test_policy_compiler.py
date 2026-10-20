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
    snapshot = PolicySnapshot(
        user_id="alice",
        permission_mode="ask",
    )
    ok, _ = snapshot.is_tool_allowed("file_read")
    assert ok is True

    ok, _ = snapshot.validate_call("file_read", {"path": "README.md"})
    assert ok is True


def test_policy_snapshot_disabled_tool():
    snapshot = PolicySnapshot(
        disabled_tools=frozenset({"terminal"}),
    )
    ok, reason = snapshot.is_tool_allowed("terminal")
    assert ok is False
    assert "desabilitada" in reason


def test_policy_snapshot_untrusted_content_blocks_destructive_tools():
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
    snapshot = PolicySnapshot(allow_force_push=False)

    # Push normal
    ok, _ = snapshot.validate_call("git_push", {"remote": "origin", "force": False})
    assert ok is True

    # Force push bloqueado
    ok, reason = snapshot.validate_call("git_push", {"remote": "origin", "force": True})
    assert ok is False
    assert "force" in reason


def test_policy_snapshot_egress_violation():
    policy = EgressPolicy(allowed_hosts=["allowed.com"])
    snapshot = PolicySnapshot(egress_policy=policy)

    ok, reason = snapshot.validate_call(
        "http_request", {"url": "https://forbidden.com/api", "method": "GET"}
    )
    assert ok is False
    assert "Egress policy violada" in reason


def test_compile_policy_snapshot():
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
