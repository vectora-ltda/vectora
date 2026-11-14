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


# --------------------------------------------------------------------------
# Log de eventos e retomada por cursor
# --------------------------------------------------------------------------


async def _alimenta(log, chunks: list[str]) -> list[str]:
    return [line async for line in sequence_stream(_stream(chunks), log)]


def _thread(run_id: str = "run-r") -> str:
    return encode_event(ThreadEvent(thread_id="t1", run_id=run_id))


async def test_replay_entrega_so_o_que_veio_depois_do_cursor() -> None:
    from backend.api.sse_sequence import RunEventLog

    log = RunEventLog("t1")
    await _alimenta(
        log,
        [
            _thread("run-a"),
            encode_event(TokenEvent(content="a")),
            encode_event(TokenEvent(content="b")),
            encode_event(DoneEvent(thread_id="t1", run_id="run-a")),
        ],
    )
    lines = [line async for line in log.replay(2)]
    assert [_payload(line)["seq"] for line in lines] == [3, 4]
    assert _payload(lines[-1])["type"] == "done"
    assert [line async for line in log.replay(4)] == []


async def test_replay_acompanha_o_run_em_andamento_ate_o_fim() -> None:
    import asyncio

    from backend.api.sse_sequence import RunEventLog

    log = RunEventLog("t1")
    log.add(_thread("run-b"))
    got: list[object] = []

    async def reader() -> None:
        got.extend([_payload(line)["seq"] async for line in log.replay(0)])

    task = asyncio.ensure_future(reader())
    await asyncio.sleep(0)
    log.add(encode_event(TokenEvent(content="x")))
    await asyncio.sleep(0)
    log.add(encode_event(DoneEvent(thread_id="t1", run_id="run-b")))
    log.finish()
    await asyncio.wait_for(task, 1)
    assert got == [1, 2, 3]


async def test_replay_recusa_cursor_fora_do_log() -> None:
    import pytest

    from backend.api import sse_sequence
    from backend.api.sse_sequence import ReplayUnavailableError, RunEventLog

    log = RunEventLog("t1")
    log.add(_thread("run-c"))
    log.finish()
    with pytest.raises(ReplayUnavailableError):
        _ = [line async for line in log.replay(5)]

    original = sse_sequence.MAX_LOG_EVENTS
    setattr(sse_sequence, "MAX_LOG_EVENTS", 2)  # noqa: B010
    try:
        small = RunEventLog("t1")
        small.add(_thread("run-d"))
        for text in "abc":
            small.add(encode_event(TokenEvent(content=text)))
        small.finish()
        with pytest.raises(ReplayUnavailableError):
            _ = [line async for line in small.replay(0)]
        assert [_payload(line)["seq"] async for line in small.replay(2)] == [3, 4]
    finally:
        setattr(sse_sequence, "MAX_LOG_EVENTS", original)  # noqa: B010


async def test_log_fica_registrado_por_run_e_cursor_e_validado() -> None:
    from backend.api.sse_sequence import RunEventLog, get_run_log, parse_event_id

    log = RunEventLog("t1")
    log.add(_thread("run-e"))
    assert get_run_log("run-e") is log
    assert get_run_log("outro") is None
    assert parse_event_id("run-e:7") == ("run-e", 7)
    assert parse_event_id("run:com:dois:3") == ("run:com:dois", 3)
    assert parse_event_id("sem-seq") is None
    assert parse_event_id("run:x") is None
