from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://registry.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "upstream error", request=request, response=response
            )

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.content


class _FakeClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, _: str) -> Any:
        upstream_response = self.response
        payload: dict[str, object] = (
            {"content": "# Extension"}
            if _.endswith("readme")
            else {"entries": [{"id": "sample.ext", "version": "1.0.0"}]}
        )

        class JsonResponse:
            status_code = self.response.status_code

            def raise_for_status(self) -> None:
                upstream_response.raise_for_status()

            def json(self) -> dict[str, object]:
                return payload

        return JsonResponse()

    def stream(self, *_: object, **__: object) -> _FakeStreamResponse:
        return self.response


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    from backend.api.server import create_app

    return TestClient(create_app(serve_static=False))


def test_registry_routes_return_versions_and_readme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import registry_client

    monkeypatch.setattr(
        registry_client,
        "extension_versions_url",
        lambda _: "https://registry.test/versions",
    )
    monkeypatch.setattr(
        registry_client,
        "extension_readme_url",
        lambda _: "https://registry.test/readme",
    )
    monkeypatch.setattr(
        "backend.api.handlers.extension_registry.httpx.AsyncClient",
        lambda **_: _FakeClient(_FakeStreamResponse()),
    )
    client = _client(monkeypatch)

    versions = client.get("/registry/extensions/sample.ext/versions")
    readme = client.get("/registry/extensions/sample.ext/readme")

    assert versions.status_code == 200
    assert versions.json() == {
        "entries": [{"id": "sample.ext", "version": "1.0.0"}],
        "total": 1,
    }
    assert readme.status_code == 200
    assert readme.json() == {"content": "# Extension"}


def test_registry_routes_require_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "true")
    from backend.api.server import create_app

    response = TestClient(create_app(serve_static=False)).get("/registry/extensions")

    assert response.status_code == 401


def test_registry_download_checks_digest_and_returns_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import extension_registry
    from backend.services import registry_client

    package = b"vext-package"
    digest = hashlib.sha256(package).hexdigest()
    monkeypatch.setattr(
        registry_client,
        "extension_download_url",
        lambda *_: "https://registry.test/download",
    )
    monkeypatch.setattr(
        "backend.api.handlers.extension_registry.httpx.AsyncClient",
        lambda **_: _FakeClient(
            _FakeStreamResponse(
                content=package,
                headers={"content-length": str(len(package)), "x-vext-digest": digest},
            )
        ),
    )
    response = _client(monkeypatch).get(
        "/registry/extensions/sample.ext/download/1.0.0"
    )

    assert response.status_code == 200
    assert response.content == package
    assert response.headers["x-vext-digest"] == digest


@pytest.mark.parametrize(
    ("content", "headers", "limit", "expected_status"),
    [
        (b"payload", {"x-vext-digest": "0" * 64}, 100, 502),
        (b"payload", {}, 3, 413),
        (b"payload", {"content-length": "101"}, 100, 413),
    ],
)
def test_registry_download_rejects_bad_or_oversized_artifact(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    headers: dict[str, str],
    limit: int,
    expected_status: int,
) -> None:
    from backend.api.handlers import extension_registry
    from backend.services import registry_client

    monkeypatch.setattr(
        registry_client,
        "extension_download_url",
        lambda *_: "https://registry.test/download",
    )
    monkeypatch.setattr(extension_registry, "MAX_PACKAGE_BYTES", limit)
    monkeypatch.setattr(
        "backend.api.handlers.extension_registry.httpx.AsyncClient",
        lambda **_: _FakeClient(_FakeStreamResponse(content=content, headers=headers)),
    )

    response = _client(monkeypatch).get(
        "/registry/extensions/sample.ext/download/1.0.0"
    )

    assert response.status_code == expected_status


def test_registry_download_reports_not_found_and_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import registry_client

    monkeypatch.setattr(
        registry_client,
        "extension_download_url",
        lambda *_: "https://registry.test/download",
    )
    monkeypatch.setattr(
        "backend.api.handlers.extension_registry.httpx.AsyncClient",
        lambda **_: _FakeClient(_FakeStreamResponse(status_code=404)),
    )
    client = _client(monkeypatch)
    assert (
        client.get("/registry/extensions/sample.ext/download/1.0.0").status_code == 404
    )

    class OfflineClient:
        async def __aenter__(self) -> OfflineClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def stream(self, *_: object, **__: object) -> _FakeStreamResponse:
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(
        "backend.api.handlers.extension_registry.httpx.AsyncClient",
        lambda **_: OfflineClient(),
    )
    assert (
        client.get("/registry/extensions/sample.ext/download/1.0.0").status_code == 503
    )
