"""Testes dos contratos estruturais por domínio."""

from __future__ import annotations

from typing import get_type_hints

from backend.vtypes.browser import BrowserTab, NetworkProfile
from backend.vtypes.plan import DecompositionNode, KanbanCard
from backend.vtypes.shell import TerminalCommand, TerminalResult


def test_plan_contracts_require_agent_facing_fields() -> None:
    """O payload de decomposição não pode perder seus campos essenciais."""
    assert set(get_type_hints(DecompositionNode)) == {"name", "instruction", "parents"}
    assert set(get_type_hints(KanbanCard)) >= {"task_id", "name", "kind", "status"}


def test_browser_contracts_describe_serializable_workbench_payloads() -> None:
    """A lista de abas e perfis CDP têm shape explícito e compartilhável."""
    assert set(get_type_hints(BrowserTab)) >= {"tab_id", "url", "active"}
    assert set(get_type_hints(NetworkProfile)) == {
        "offline",
        "latency",
        "downloadThroughput",
        "uploadThroughput",
    }


def test_shell_contracts_keep_command_and_result_distinct() -> None:
    """Entrada de shell e resultado não devem compartilhar campos implícitos."""
    assert "command" in get_type_hints(TerminalCommand)
    assert {"status", "state"} <= set(get_type_hints(TerminalResult))
