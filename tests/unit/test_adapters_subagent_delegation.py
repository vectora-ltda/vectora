"""``stream_engine_events`` registra delegação de subagente (tool `task`) na
aba Tarefas.

O motor nativo emite ``SubagentOutput`` (status "running" no início,
"complete"/"error" no fim) especificamente pra delegação via subagente —
tools normais emitem ``ToolCallStarted``/``ToolResult``, nunca
``SubagentOutput``. O bridge intercepta esse evento e persiste via
``backend.scheduling.background_tasks.record_subagent_delegation``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.stream_events import SubagentOutput, ToolCallStarted, ToolResult


@pytest.fixture(autouse=True)
def _isolated(_no_thread_persistence):
    pass


def _parse(sse: str) -> dict:
    assert sse.startswith("data: ")
    return json.loads(sse[len("data: ") :].strip())


def _delegation_run(
    subagent_type: str,
    description: str,
    *,
    status: str = "complete",
    content: str = "feito",
):
    async def run(on_event):
        await on_event(
            SubagentOutput(
                subagent_type=subagent_type,
                description=description,
                status="running",
                tool_call_id="run-task-1",
            )
        )
        await on_event(
            SubagentOutput(
                subagent_type=subagent_type,
                description=description,
                status=status,
                tool_call_id="run-task-1",
                content=content,
            )
        )
        return "stop"

    return run


def _non_subagent_tool_run():
    async def run(on_event):
        await on_event(
            ToolCallStarted(
                tool_name="file_read", tool_call_id="r1", args_json='{"path":"x.py"}'
            )
        )
        await on_event(ToolResult(tool_call_id="r1", content_json='"conteudo"'))
        return "stop"

    return run


@pytest.mark.asyncio
async def test_delegation_recorded_with_user_id(monkeypatch):
    """``SubagentOutput`` final (status != running) com user_id registra a
    delegação."""
    mock_record = AsyncMock()
    monkeypatch.setattr(
        "backend.scheduling.background_tasks.record_subagent_delegation", mock_record
    )

    run = _delegation_run(
        "coder", "Crie um arquivo X", content="Arquivo criado com sucesso"
    )
    _ = [
        _parse(s)
        async for s in stream_engine_events(run, thread_id="tid", user_id="u1")
    ]

    mock_record.assert_awaited_once()
    assert mock_record.await_args is not None
    kwargs = mock_record.await_args.kwargs
    assert kwargs["session_id"] == "tid"
    assert kwargs["user_id"] == "u1"
    assert kwargs["subagent_type"] == "coder"
    assert kwargs["description"] == "Crie um arquivo X"
    assert kwargs["status"] == "done"


@pytest.mark.asyncio
async def test_delegation_marks_error_status_on_failure(monkeypatch):
    """Erro/borda: ``SubagentOutput`` final com status='error' vira
    status='error' no registro."""
    mock_record = AsyncMock()
    monkeypatch.setattr(
        "backend.scheduling.background_tasks.record_subagent_delegation", mock_record
    )

    run = _delegation_run(
        "search", "Pesquise X", status="error", content="erro na delegação"
    )
    _ = [
        _parse(s)
        async for s in stream_engine_events(run, thread_id="tid", user_id="u1")
    ]

    assert mock_record.await_args is not None
    kwargs = mock_record.await_args.kwargs
    assert kwargs["status"] == "error"


@pytest.mark.asyncio
async def test_delegation_skipped_without_user_id(monkeypatch):
    """Erro/borda: sem user_id (sessão anônima/CLI), não tenta persistir."""
    mock_record = AsyncMock()
    monkeypatch.setattr(
        "backend.scheduling.background_tasks.record_subagent_delegation", mock_record
    )

    run = _delegation_run("coder", "x", content="ok")
    _ = [_parse(s) async for s in stream_engine_events(run, thread_id="tid")]

    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegation_failure_never_breaks_the_stream(monkeypatch):
    """Erro/borda: falha ao registrar não deve impedir o stream de completar."""
    monkeypatch.setattr(
        "backend.scheduling.background_tasks.record_subagent_delegation",
        AsyncMock(side_effect=RuntimeError("db indisponível")),
    )

    run = _delegation_run("coder", "x", content="ok")
    out = [
        _parse(s)
        async for s in stream_engine_events(run, thread_id="tid", user_id="u1")
    ]

    # O stream não deve abortar: o done final ainda sai.
    types = [e["type"] for e in out]
    assert "done" in types


@pytest.mark.asyncio
async def test_non_subagent_tools_do_not_trigger_delegation(monkeypatch):
    """Tools normais (``ToolCallStarted``/``ToolResult``, não
    ``SubagentOutput``) não devem disparar o registro de subagente."""
    mock_record = AsyncMock()
    monkeypatch.setattr(
        "backend.scheduling.background_tasks.record_subagent_delegation", mock_record
    )

    run = _non_subagent_tool_run()
    _ = [
        _parse(s)
        async for s in stream_engine_events(run, thread_id="tid", user_id="u1")
    ]

    mock_record.assert_not_awaited()
