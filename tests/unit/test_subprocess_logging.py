"""pipe_to_logger: repassa stdout de subprocess pro logger, linha a linha."""

from __future__ import annotations

import asyncio
import logging

import pytest

from backend.services.subprocess_logging import pipe_to_logger


class _FakeStreamReader:
    """Simula asyncio.StreamReader.readline() a partir de uma lista de linhas."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


@pytest.mark.asyncio
async def test_pipe_to_logger_forwards_each_line_with_prefix(caplog):
    stream = _FakeStreamReader([b"line one\n", b"line two\n"])
    logger = logging.getLogger("test.pipe_to_logger")

    with caplog.at_level(logging.INFO, logger="test.pipe_to_logger"):
        await pipe_to_logger(stream, logger, prefix="preview:web")

    messages = [r.message for r in caplog.records if r.name == logger.name]
    assert "preview:web: line one" in messages
    assert "preview:web: line two" in messages


@pytest.mark.asyncio
async def test_pipe_to_logger_stops_on_eof():
    stream = _FakeStreamReader([b"only line\n"])
    logger = logging.getLogger("test.pipe_to_logger")

    # Não deve travar/loop infinito — readline() vazio (EOF) encerra.
    await asyncio.wait_for(pipe_to_logger(stream, logger, prefix="x"), timeout=2.0)


@pytest.mark.asyncio
async def test_pipe_to_logger_none_stream_is_noop():
    # erro/borda: subprocess sem stdout capturado (ex. herança de FD falhou)
    # não pode quebrar o helper.
    logger = logging.getLogger("test.pipe_to_logger")
    await asyncio.wait_for(pipe_to_logger(None, logger, prefix="x"), timeout=1.0)


@pytest.mark.asyncio
async def test_pipe_to_logger_skips_blank_lines(caplog):
    stream = _FakeStreamReader([b"\n", b"real content\n"])
    logger = logging.getLogger("test.pipe_to_logger")

    with caplog.at_level(logging.INFO, logger="test.pipe_to_logger"):
        await pipe_to_logger(stream, logger, prefix="x")

    messages = [r.message for r in caplog.records if r.name == logger.name]
    assert len(messages) == 1
    assert "real content" in messages[0]


@pytest.mark.asyncio
async def test_pipe_to_logger_calls_on_line_for_each_line():
    stream = _FakeStreamReader([b"first\n", b"second\n"])
    logger = logging.getLogger("test.pipe_to_logger")
    seen: list[str] = []

    await pipe_to_logger(stream, logger, prefix="x", on_line=seen.append)

    assert seen == ["first", "second"]


@pytest.mark.asyncio
async def test_pipe_to_logger_on_line_not_called_for_blank_lines():
    # erro/borda: linha em branco não deve virar entrada vazia no buffer.
    stream = _FakeStreamReader([b"\n", b"content\n"])
    logger = logging.getLogger("test.pipe_to_logger")
    seen: list[str] = []

    await pipe_to_logger(stream, logger, prefix="x", on_line=seen.append)

    assert seen == ["content"]
