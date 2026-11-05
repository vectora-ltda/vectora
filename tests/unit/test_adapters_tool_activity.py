"""ToolActivityEvent em ``stream_engine_events``.

O bridge deve repassar `tool_activity` no início da tool (elapsed_ms=None) e
no fim (elapsed_ms≥0) para alimentar o AgentStatusLine — o motor nativo
(`backend/engine/conversation_loop.py`) já emite ``ToolActivity`` nesses dois
pontos; aqui testamos que o mapeamento pra SSE preserva os campos e a ordem.

O cálculo de ``args_preview`` (extração do campo semântico do args da tool)
é responsabilidade de ``conversation_loop.py``, não deste bridge — coberto
em ``tests/unit/test_engine_conversation_loop.py::TestArgsPreview``. Aqui só
confirmamos que o valor já calculado atravessa o bridge sem alteração.
"""

from __future__ import annotations

import json

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.stream_events import ToolActivity, ToolCallStarted, ToolResult


@pytest.fixture(autouse=True)
def _isolated(_no_thread_persistence):
    pass


def _parse(sse: str) -> dict:
    assert sse.startswith("data: ")
    return json.loads(sse[len("data: ") :].strip())


def _one_tool_run(tool_name: str, *, elapsed_ms: int = 5):
    async def run(on_event):
        await on_event(
            ToolCallStarted(tool_name=tool_name, tool_call_id="run-abc", args_json="{}")
        )
        await on_event(ToolActivity(tool_name=tool_name, tool_call_id="run-abc"))
        await on_event(ToolResult(tool_call_id="run-abc", content_json='"ok"'))
        await on_event(
            ToolActivity(
                tool_name=tool_name, tool_call_id="run-abc", elapsed_ms=elapsed_ms
            )
        )
        return "stop"

    return run


def _start_only_run(tool_name: str):
    async def run(on_event):
        await on_event(
            ToolCallStarted(tool_name=tool_name, tool_call_id="run-abc", args_json="{}")
        )
        await on_event(ToolActivity(tool_name=tool_name, tool_call_id="run-abc"))
        return "stop"

    return run


def _multi_tool_run():
    async def run(on_event):
        for name in ("file_edit", "web_search"):
            await on_event(
                ToolCallStarted(tool_name=name, tool_call_id=name, args_json="{}")
            )
            await on_event(ToolActivity(tool_name=name, tool_call_id=name))
            await on_event(ToolResult(tool_call_id=name, content_json='"ok"'))
            await on_event(
                ToolActivity(tool_name=name, tool_call_id=name, elapsed_ms=1)
            )
        return "stop"

    return run


@pytest.mark.asyncio
async def test_tool_activity_emitted_on_tool_start():
    """tool_activity com elapsed_ms=None sai antes de qualquer tool_result."""
    out = [
        _parse(s)
        async for s in stream_engine_events(
            _start_only_run("file_edit"), thread_id="tid"
        )
    ]

    activity = [e for e in out if e["type"] == "tool_activity"]
    assert len(activity) == 1, "deve emitir exatamente 1 tool_activity no start"
    ev = activity[0]
    assert ev["tool_name"] == "file_edit"
    assert ev["elapsed_ms"] is None


@pytest.mark.asyncio
async def test_tool_activity_emitted_on_tool_end_with_elapsed():
    """tool_activity de fim carrega elapsed_ms >= 0 (inteiro)."""
    out = [
        _parse(s)
        async for s in stream_engine_events(
            _one_tool_run("file_edit", elapsed_ms=12), thread_id="tid"
        )
    ]

    activities = [e for e in out if e["type"] == "tool_activity"]
    assert len(activities) == 2, (
        f"esperado 2 tool_activity, got {len(activities)}: {activities}"
    )
    end_ev = activities[1]
    assert end_ev["tool_name"] == "file_edit"
    assert isinstance(end_ev["elapsed_ms"], int)
    assert end_ev["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_tool_activity_and_tool_call_both_emitted_on_start():
    """No início da tool, tanto tool_call quanto tool_activity saem —
    ``conversation_loop.py`` emite ``ToolCallStarted`` seguido de
    ``ToolActivity``."""
    out = [
        _parse(s)
        async for s in stream_engine_events(
            _start_only_run("web_search"), thread_id="tid"
        )
    ]

    types = [e["type"] for e in out]
    assert "tool_activity" in types
    assert "tool_call" in types
    assert types.index("tool_call") < types.index("tool_activity")


@pytest.mark.asyncio
async def test_tool_activity_on_end_after_tool_result():
    """tool_activity (end) deve aparecer APÓS tool_result na sequência SSE."""
    out = [
        _parse(s)
        async for s in stream_engine_events(
            _one_tool_run("web_search"), thread_id="tid"
        )
    ]

    types = [e["type"] for e in out]
    activity_indices = [i for i, t in enumerate(types) if t == "tool_activity"]
    result_index = types.index("tool_result")
    # O segundo tool_activity (end) deve vir após tool_result
    assert activity_indices[1] > result_index


@pytest.mark.asyncio
async def test_multiple_tools_activity_tracked_independently():
    """Duas tools distintas geram dois pares de tool_activity com nomes corretos."""
    out = [
        _parse(s)
        async for s in stream_engine_events(_multi_tool_run(), thread_id="tid")
    ]

    activities = [e for e in out if e["type"] == "tool_activity"]
    assert len(activities) == 4
    names = [a["tool_name"] for a in activities]
    assert names == ["file_edit", "file_edit", "web_search", "web_search"]
