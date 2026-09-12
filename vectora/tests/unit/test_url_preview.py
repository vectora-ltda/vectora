"""Security and contract tests for the isolated URL preview endpoint."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from typing import TypedDict, cast

import httpx
import pytest
from fastapi import HTTPException

from backend.api.handlers.url_preview import _public_host, preview_url


class _FakeResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html"}
        self._chunks = chunks if chunks is not None else [body]

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _RequestCall(TypedDict):
    method: str
    url: str
    headers: dict[str, str]
    extensions: dict[str, str]


class _FakeClient:
    response: _FakeResponse | None = None
    calls: list[_RequestCall] = []

    def __init__(self, **_kwargs: object) -> None:
        self.__class__.calls = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": cast("dict[str, str]", kwargs["headers"]),
                "extensions": cast("dict[str, str]", kwargs["extensions"]),
            }
        )
        assert self.response is not None
        return self.response


def test_public_host_rejects_private_and_loopback_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args: [(socket.AF_INET, 0, 0, "", ("127.0.0.1", 0))],
    )
    assert _public_host("internal.example") is False


def test_validated_ip_rejects_shared_cgnat_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args: [(socket.AF_INET, 0, 0, "", ("100.64.0.1", 0))],
    )
    from backend.api.handlers.url_preview import _validated_ip

    assert _validated_ip("shared.example") is None


def test_validated_ip_accepts_global_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))],
    )
    from backend.api.handlers.url_preview import _validated_ip

    assert _validated_ip("example.com") == "93.184.216.34"


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


@pytest.mark.asyncio
async def test_preview_rejects_userinfo_before_network() -> None:
    with pytest.raises(HTTPException) as error:
        await preview_url("https://user:password@example.com/page")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_preview_sanitizes_metadata_and_uses_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.api.handlers.url_preview._validated_ip",
        lambda _host: "93.184.216.34",
    )
    _FakeClient.response = _FakeResponse(
        b"""
        <html><title>Docs &amp; <b>Vectora</b></title>
        <meta name='description' content='Use <script>safe</script>'>
        <meta property='og:image' content='https://cdn.example/image.png'>
        </html>
        """,
    )
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await preview_url("https://example.com/docs")

    assert result == {
        "url": "https://example.com/docs",
        "title": "Docs & Vectora",
        "description": "Use safe",
        "image": "https://cdn.example/image.png",
        "origin": "example.com",
    }
    call = _FakeClient.calls[0]
    assert call["url"] == "https://93.184.216.34/docs"
    assert call["headers"]["Host"] == "example.com"
    assert call["extensions"]["sni_hostname"] == "example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "headers", "expected"),
    [
        (302, {"content-type": "text/html"}, 403),
        (200, {"content-type": "application/json"}, 415),
        (200, {"content-type": "text/html", "content-length": "600000"}, 413),
        (200, {"content-type": "text/html", "content-length": "invalid"}, 502),
        (200, {"content-type": "text/html", "content-length": "-1"}, 502),
    ],
)
async def test_preview_rejects_unsafe_response_headers(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    headers: dict[str, str],
    expected: int,
) -> None:
    monkeypatch.setattr(
        "backend.api.handlers.url_preview._validated_ip",
        lambda _host: "93.184.216.34",
    )
    _FakeClient.response = _FakeResponse(status_code=status_code, headers=headers)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    with pytest.raises(HTTPException) as error:
        await preview_url("https://example.com/page")
    assert error.value.status_code == expected


@pytest.mark.asyncio
async def test_preview_rejects_streaming_response_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.api.handlers.url_preview._validated_ip",
        lambda _host: "93.184.216.34",
    )
    _FakeClient.response = _FakeResponse(
        headers={"content-type": "text/html"},
        chunks=[b"x" * 512_000, b"overflow"],
    )
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    with pytest.raises(HTTPException) as error:
        await preview_url("https://example.com/page")
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_preview_maps_network_timeout_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.api.handlers.url_preview._validated_ip",
        lambda _host: "93.184.216.34",
    )

    class _TimeoutStream:
        async def __aenter__(self) -> _TimeoutStream:
            raise httpx.ReadTimeout("timed out")

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _TimeoutClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _TimeoutClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def stream(self, method: str, url: str, **kwargs: object) -> _TimeoutStream:
            return _TimeoutStream()

    monkeypatch.setattr(httpx, "AsyncClient", _TimeoutClient)

    with pytest.raises(HTTPException) as error:
        await preview_url("https://example.com/page")
    assert error.value.status_code == 502
