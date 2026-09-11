"""Security and contract tests for the isolated URL preview endpoint."""

from __future__ import annotations

import socket

import pytest
from fastapi import HTTPException

from backend.api.handlers.url_preview import _public_host, preview_url


def test_public_host_rejects_private_and_loopback_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args: [(socket.AF_INET, 0, 0, "", ("127.0.0.1", 0))],
    )
    assert _public_host("internal.example") is False


@pytest.mark.asyncio
async def test_preview_rejects_non_http_schemes() -> None:
    with pytest.raises(HTTPException) as error:
        await preview_url("file:///etc/passwd")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_preview_rejects_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.api.handlers.url_preview._validated_ip", lambda _host: None
    )
    with pytest.raises(HTTPException) as error:
        await preview_url("https://internal.example/page")
    assert error.value.status_code == 403
