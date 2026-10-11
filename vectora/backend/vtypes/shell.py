"""Contratos de shell, PTY e execução de comandos do agente."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

ShellState = Literal["running", "exited", "idle", "error"]


class TerminalCommand(TypedDict):
    """Comando que a tool envia ao terminal compartilhado."""

    command: str
    terminal_id: NotRequired[str]
    workspace_id: NotRequired[str]


class TerminalResult(TypedDict):
    """Envelope serializável retornado pela execução de shell."""

    status: Literal["ok", "error"]
    state: ShellState
    terminal_id: NotRequired[str]
    output: NotRequired[str]
    error: NotRequired[str]
    exit_code: NotRequired[int | None]
