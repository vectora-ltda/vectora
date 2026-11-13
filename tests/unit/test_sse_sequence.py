"""Numeração e identidade dos eventos do stream de chat."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from backend.api.schemas import DoneEvent, ThreadEvent, TokenEvent, encode_event
from backend.api.sse_sequence import sequence_stream, stamp_event


def _payload(chunk: str) -> dict[str, object]:
    return json.loads(chunk[len("data: ") :])


def test_stamp_event_acrescenta_seq_e_event_id_sem_mudar_o_quadro() -> None:
    stamped = stamp_event(encode_event(TokenEvent(content="oi")), "run-1", 3)
    assert stamped.startswith("data: {")
    assert stamped.endswith("\n\n")
    payload = _payload(stamped)
    assert payload["seq"] == 3
    assert payload["event_id"] == "run-1:3"
    assert payload["type"] == "token"
    assert payload["content"] == "oi"


def test_linhas_que_nao_sao_evento_passam_sem_mudanca() -> None:
    assert stamp_event(": keep-alive\n\n", "run-1", 1) == ": keep-alive\n\n"


async def _stream(chunks: list[str]) -> AsyncGenerator[str]:
    for chunk in chunks:
        yield chunk


async def test_sequence_stream_numera_em_ordem_e_usa_o_run_do_primeiro_evento() -> None:
    chunks = [
        encode_event(ThreadEvent(thread_id="t1", run_id="run-9")),
        encode_event(TokenEvent(content="a")),
        ": keep-alive\n\n",
        encode_event(TokenEvent(content="b")),
        encode_event(DoneEvent(thread_id="t1", run_id="run-9", stopped_reason="stop")),
    ]
    out = [chunk async for chunk in sequence_stream(_stream(chunks))]
    events = [_payload(chunk) for chunk in out if chunk.startswith("data: ")]
    assert [event["seq"] for event in events] == [1, 2, 3, 4]
    assert {event["event_id"] for event in events} == {
        "run-9:1",
        "run-9:2",
        "run-9:3",
        "run-9:4",
    }
    assert out[2] == ": keep-alive\n\n"
    assert events[-1]["type"] == "done"
    assert events[-1]["stopped_reason"] == "stop"


async def test_fechar_o_stream_numerado_fecha_o_gerador_interno() -> None:
    closed: list[bool] = []

    async def inner() -> AsyncGenerator[str]:
        try:
            yield encode_event(ThreadEvent(thread_id="t1", run_id="run-1"))
            yield encode_event(TokenEvent(content="a"))
        finally:
            closed.append(True)

    stream = sequence_stream(inner())
    await anext(stream)
    await stream.aclose()
    assert closed == [True]


async def test_sem_evento_thread_o_event_id_fica_sem_run() -> None:
    out = [
        chunk
        async for chunk in sequence_stream(
            _stream([encode_event(TokenEvent(content="a"))])
        )
    ]
    assert _payload(out[0])["event_id"] == ":1"
