"""``stream_engine_events`` emite output do terminal em tempo real (não só no
fim da tool).

A tool `terminal` já chamava `emit_terminal_line()` a cada linha, mas nada
registrava um callback — o output só chegava ao frontend no ToolResultEvent
final. ``stream_engine_events`` registra um callback e corre uma fila de
linhas em paralelo ao consumo de eventos do motor nativo (que fica parado
durante toda a execução da tool, sem emitir nada novo).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.stream_events import ToolCallStarted, ToolResult
from backend.services.terminal_stream import emit_terminal_line


@pytest.fixture(autouse=True)
def _isolated(_no_thread_persistence):
    pass


def _parse(sse: str) -> dict:
    assert sse.startswith("data: ")
    return json.loads(sse[len("data: ") :].strip())


async def _collect(gen):
    return [_parse(s) async for s in gen]


def _slow_terminal_run(delay: float):
    """Simula o motor nativo ficando parado enquanto a tool `terminal` roda."""

    async def run(on_event):
        await asyncio.sleep(delay)
        await on_event(
            ToolCallStarted(
                tool_name="terminal",
                tool_call_id="r1",
                args_json='{"command":"npm install"}',
            )
        )
        await asyncio.sleep(delay)
        await on_event(ToolResult(tool_call_id="r1", content_json='"instalado"'))
        return "stop"

    return run


def _slow_other_tool_run(delay: float):
    async def run(on_event):
        await asyncio.sleep(delay)
        await on_event(
            ToolCallStarted(
                tool_name="file_read", tool_call_id="r1", args_json='{"path":"x.py"}'
            )
        )
        await asyncio.sleep(delay)
        await on_event(ToolResult(tool_call_id="r1", content_json='"conteudo"'))
        return "stop"

    return run


def _noop_run():
    async def run(on_event):
        return "stop"

    return run


@pytest.mark.asyncio
async def test_terminal_lines_emitted_while_engine_is_idle():
    """Linhas emitidas via emit_terminal_line chegam ANTES do próximo evento
    do motor nativo, mesmo que ele não produza nada por um tempo."""
    gen = stream_engine_events(_slow_terminal_run(delay=0.2), thread_id="tid")

    async def _emit_lines_soon():
        await asyncio.sleep(0.02)
        emit_terminal_line("added 42 packages")
        await asyncio.sleep(0.02)
        emit_terminal_line("audited 100 packages")

    emit_task = asyncio.create_task(_emit_lines_soon())
    out = await _collect(gen)
    await emit_task

    types = [e["type"] for e in out]
    terminal_lines = [e for e in out if e["type"] == "terminal_line"]

    assert [e["line"] for e in terminal_lines] == [
        "added 42 packages",
        "audited 100 packages",
    ]
    # As duas linhas chegam ANTES do tool_result (que só sai depois de
    # 0.2s+0.2s) — prova que não esperaram o motor avançar.
    assert types.index(terminal_lines[0]["type"]) < types.index("tool_result")
    assert types.index(terminal_lines[1]["type"]) < types.index("tool_result")


@pytest.mark.asyncio
async def test_no_terminal_lines_when_none_emitted():
    """Erro/borda: sem chamada a emit_terminal_line, não gera terminal_line."""
    out = await _collect(
        stream_engine_events(_slow_other_tool_run(delay=0.0), thread_id="tid")
    )
    assert not [e for e in out if e["type"] == "terminal_line"]


@pytest.mark.asyncio
async def test_terminal_callback_unregistered_after_stream_ends():
    """Erro/borda: callback não deve vazar entre streams — o próximo
    stream_engine_events não deve receber linhas de uma emissão tardia do
    stream anterior."""
    await _collect(stream_engine_events(_noop_run(), thread_id="tid1"))

    # Emissão tardia após o primeiro stream terminar — não deve quebrar nada
    # nem ser entregue a ninguém (callback já foi desregistrado).
    emit_terminal_line("linha orfa")

    out2 = await _collect(stream_engine_events(_noop_run(), thread_id="tid2"))
    assert not [e for e in out2 if e["type"] == "terminal_line"]
