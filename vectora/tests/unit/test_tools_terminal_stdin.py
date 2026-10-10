"""Tool `terminal`: streaming real via `_drain_terminal_output` + stdin interativo.

Detecta "processo vivo mas sem output novo há alguns segundos"
(provavelmente esperando input) e devolve o controle ao agente, que pode
responder via `terminal(stdin_input=...)` no mesmo processo em vez de
travar a chamada de tool inteira.
"""

from __future__ import annotations

import sys

import pytest

from backend.tools.context import ctx_from_config
from backend.tools.fs import _pending_terminal, terminal
from backend.workspace.workspace import WorkspaceRegistry


@pytest.fixture
def _workspace(tmp_path, monkeypatch):
    """Workspace real e confiável, registrado num registry isolado.

    Sem isso, ``_active_workspace`` cai no fallback ``get_or_create()``
    (resolve por ``Path.cwd()`` ou por um estado de registry global que
    outros testes podem ter deixado apontando pra um diretório de sessão
    nunca criado em disco) — em CI (runner efêmero) isso derruba o
    subprocess do terminal com ``FileNotFoundError`` porque o `cwd`
    resolvido não existe.
    """
    registry = WorkspaceRegistry()
    registry._loaded = True
    monkeypatch.setattr(registry, "_save", lambda: None)
    monkeypatch.setattr("backend.workspace.workspace.workspace_registry", registry)
    return registry.create(str(tmp_path), trust=True)


def _config(workspace_id: str, thread_id: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread_id, "workspace_id": workspace_id}}


@pytest.fixture(autouse=True)
def _clear_pending():
    _pending_terminal.clear()
    yield
    _pending_terminal.clear()


@pytest.mark.asyncio
async def test_terminal_command_idle_waiting_input_then_resumed_via_stdin(
    monkeypatch, _workspace
):
    """Comando que fica esperando input registra pendência e é retomado com stdin_input."""
    monkeypatch.setattr("backend.tools.fs._IDLE_TIMEOUT", 0.1)
    monkeypatch.setattr("backend.tools.fs._HARD_TIMEOUT", 5.0)

    script = (
        "import sys; print('pronto'); sys.stdout.flush(); "
        "resp = input(); print('recebido:' + resp)"
    )
    command = f'{sys.executable} -c "{script}"'

    result = await terminal(
        command=command, ctx=ctx_from_config(_config(_workspace.id, "t1"))
    )

    assert "esperando input" in result
    assert "t1" in _pending_terminal

    resumed = await terminal(
        stdin_input="ola", ctx=ctx_from_config(_config(_workspace.id, "t1"))
    )

    assert "recebido:ola" in resumed
    assert "t1" not in _pending_terminal


@pytest.mark.asyncio
async def test_terminal_stdin_input_without_pending_process_returns_error(_workspace):
    """stdin_input sem nenhum comando pendente na thread é um erro claro, não um crash."""
    result = await terminal(
        stdin_input="ola",
        ctx=ctx_from_config(_config(_workspace.id, "sem-pendencia")),
    )

    assert result.startswith("Error:")
    assert "sem-pendencia" not in _pending_terminal


@pytest.mark.asyncio
async def test_terminal_without_command_or_stdin_input_returns_error(_workspace):
    result = await terminal(ctx=ctx_from_config(_config(_workspace.id, "t2")))

    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_terminal_accepts_output_line_larger_than_asyncio_limit(_workspace):
    """Saída sem newline dentro do limite do StreamReader não quebra o tool."""
    script = "print('x' * 200000, end='')"
    command = f'{sys.executable} -c "{script}"'

    result = await terminal(
        command=command, ctx=ctx_from_config(_config(_workspace.id, "large-line"))
    )

    assert len(result) == 200000
