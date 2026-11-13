"""``ResumeStream``: retoma o stream de um run pelo cursor do último evento."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api.handlers.chat import ResumeStreamRequest, resume_stream
from backend.api.schemas import DoneEvent, ThreadEvent, TokenEvent, encode_event
from backend.api.sse_sequence import RunEventLog


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": headers or []}
    )


def _finished_log(run_id: str, thread_id: str = "t1") -> RunEventLog:
    log = RunEventLog(thread_id)
    log.add(encode_event(ThreadEvent(thread_id=thread_id, run_id=run_id)))
    log.add(encode_event(TokenEvent(content="a")))
    log.add(encode_event(TokenEvent(content="b")))
    log.add(encode_event(DoneEvent(thread_id=thread_id, run_id=run_id)))
    log.finish()
    return log


async def _body(response) -> list[dict]:
    return [
        json.loads(chunk[len("data: ") :])
        async for chunk in response.body_iterator
        if chunk.startswith("data: ")
    ]


@pytest.fixture(autouse=True)
def _owns_thread():
    with patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()):
        yield


async def test_entrega_os_eventos_depois_do_cursor() -> None:
    _finished_log("run-rs-1")
    response = await resume_stream(
        ResumeStreamRequest(thread_id="t1", last_event_id="run-rs-1:2"), _request()
    )
    events = await _body(response)
    assert [e["seq"] for e in events] == [3, 4]
    assert events[-1]["type"] == "done"


async def test_header_last_event_id_vale_quando_o_campo_vem_vazio() -> None:
    _finished_log("run-rs-2")
    response = await resume_stream(
        ResumeStreamRequest(thread_id="t1"),
        _request([(b"last-event-id", b"run-rs-2:3")]),
    )
    assert [e["seq"] for e in await _body(response)] == [4]


@pytest.mark.parametrize("cursor", ["", "sem-seq", "run:x"])
async def test_cursor_invalido_responde_400(cursor: str) -> None:
    with pytest.raises(HTTPException) as exc:
        await resume_stream(
            ResumeStreamRequest(thread_id="t1", last_event_id=cursor), _request()
        )
    assert exc.value.status_code == 400


async def test_run_desconhecido_ou_de_outra_thread_responde_404() -> None:
    _finished_log("run-rs-3", thread_id="t1")
    for thread_id, cursor in (("t1", "run-inexistente:1"), ("t2", "run-rs-3:1")):
        with pytest.raises(HTTPException) as exc:
            await resume_stream(
                ResumeStreamRequest(thread_id=thread_id, last_event_id=cursor),
                _request(),
            )
        assert exc.value.status_code == 404


async def test_cursor_fora_do_log_responde_404() -> None:
    _finished_log("run-rs-4")
    with pytest.raises(HTTPException) as exc:
        await resume_stream(
            ResumeStreamRequest(thread_id="t1", last_event_id="run-rs-4:99"),
            _request(),
        )
    assert exc.value.status_code == 404
