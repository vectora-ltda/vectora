from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import patch

import pytest

from backend.vtypes.web import (
    CrawlRequest,
    FetchedDocument,
    MapRequest,
    SearchDiagnostic,
    SearchResponse,
    SearchResult,
)


def test_web_contracts_validate_provider_independent_payloads() -> None:
    response = SearchResponse(
        results=[SearchResult(title="Vectora", url="https://example.com")],
        diagnostic=SearchDiagnostic(status="ok", provider="browser", query="vectora"),
    )
    assert response.results[0].provider == "browser"
    assert CrawlRequest(url="https://example.com", max_depth=2).max_depth == 2
    assert MapRequest(url="https://example.com", limit=10).limit == 10
    assert (
        FetchedDocument(
            url="https://example.com", final_url="https://example.com", content="ok"
        ).provider
        == "browser"
    )


@pytest.mark.asyncio
async def test_crawl_uses_browser_without_tavily() -> None:
    from backend.tools.web import web_crawl

    with (
        patch("backend.tools.web.settings") as settings,
        patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
        patch(
            "backend.browser.search_fallback.fetch_fallback",
            return_value="root content",
        ),
        patch(
            "backend.browser.search_fallback.browser_map",
            return_value=[],
        ),
    ):
        settings.tavily_api_key = None
        result = json.loads(await web_crawl("https://example.com", limit=1))

    assert result["status"] == "ok"
    assert result["provider"] == "browser"
    assert result["results"][0]["content"] == "root content"


@pytest.mark.asyncio
async def test_map_reports_browser_error_without_fabricating_empty_results() -> None:
    from backend.tools.web import web_map

    with (
        patch("backend.tools.web.settings") as settings,
        patch(
            "backend.browser.search_fallback.browser_map",
            side_effect=RuntimeError("Electron indisponível"),
        ),
    ):
        settings.tavily_api_key = None
        result = json.loads(await web_map("https://example.com"))

    assert result["status"] == "error"
    assert result["provider"] == "browser"
    assert "Electron" in result["error"]


@pytest.mark.asyncio
async def test_crawl_reports_partial_failures_per_url() -> None:
    from backend.tools.web import web_crawl

    calls = 0

    def _fetch(url: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "root content"
        raise RuntimeError("redirect recusado")

    with (
        patch("backend.tools.web.settings") as settings,
        patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
        patch("backend.browser.search_fallback.fetch_fallback", side_effect=_fetch),
        patch(
            "backend.browser.search_fallback.browser_map",
            return_value=["https://example.com/child"],
        ),
    ):
        settings.tavily_api_key = None
        result = json.loads(
            await web_crawl("https://example.com", max_depth=1, limit=2)
        )

    assert result["status"] == "partial"
    assert len(result["results"]) == 1
    assert result["errors"][0]["url"] == "https://example.com/child"


def test_browser_fallback_requires_explicit_workspace_page_ownership() -> None:
    from backend.browser import session
    from backend.browser.search_fallback import _find_workbench_page

    class _Page:
        def __init__(self, target_id: str, url: str = "https://example.com") -> None:
            self.target_id = target_id
            self.url = url

    class _Context:
        def __init__(self, pages: list[_Page]) -> None:
            self.pages = pages

    class _Browser:
        def __init__(self, pages: list[_Page]) -> None:
            self.contexts = [_Context(pages)]

    unowned = _Page("unowned")
    other_workspace = _Page("owned-by-other")
    session._page_owners.clear()
    session._page_owners["owned-by-other"] = "workspace-b"

    with pytest.raises(RuntimeError, match="Nenhuma aba"):
        _find_workbench_page(
            cast("Any", _Browser([unowned, other_workspace])),
            workspace_id="workspace-a",
        )


def test_browser_search_rejects_unsafe_redirect() -> None:
    from backend.browser import search_fallback

    class _Page:
        url = "https://example.com"

        def set_default_timeout(self, _timeout: int) -> None:
            return None

        def goto(self, *_args: object, **_kwargs: object) -> None:
            self.url = "http://127.0.0.1:8080/private"

    page = _Page()

    with (
        patch.object(
            search_fallback, "_get_browser", return_value=cast("Any", object())
        ),
        patch.object(search_fallback, "_find_workbench_page", return_value=page),
        patch.object(search_fallback, "_install_ssrf_route", return_value=object()),
        patch.object(search_fallback, "_remove_ssrf_route"),
        patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=False),
    ):
        with pytest.raises(ValueError, match="redirect recusado"):
            search_fallback.browser_search("vectora")


def test_fetch_rejects_redirect_denied_by_workspace_egress() -> None:
    """A redirect to a public host can still violate the workspace allowlist."""
    from backend.browser import search_fallback

    class _Page:
        url = "https://allowed.example"

        def set_default_timeout(self, _timeout: int) -> None:
            return None

        def goto(self, *_args: object, **_kwargs: object) -> None:
            self.url = "https://denied.example/private"

        def inner_text(self, _selector: str) -> str:
            return "should never be read"

    page = _Page()

    class _Browser:
        contexts = [object()]

    def _allowed(url: str, _policy: object) -> tuple[bool, str]:
        return ("denied.example" not in url, "host denied")

    with (
        patch.object(
            search_fallback, "_get_browser", return_value=cast("Any", _Browser())
        ),
        patch.object(search_fallback, "_find_workbench_page", return_value=page),
        patch.object(search_fallback, "_install_ssrf_route", return_value=object()),
        patch.object(search_fallback, "_remove_ssrf_route"),
        patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
        patch("backend.services.egress_policy.is_url_allowed", side_effect=_allowed),
    ):
        with pytest.raises(ValueError, match="política de egress"):
            search_fallback.fetch_fallback(
                "https://allowed.example",
                egress_policy={"allowed_hosts": ["*.example"]},
            )


def test_browser_map_omits_links_denied_by_workspace_egress() -> None:
    from backend.browser import search_fallback

    class _Locator:
        def evaluate_all(self, _script: str) -> list[str]:
            return ["https://allowed.example/a", "https://denied.example/b"]

    class _Page:
        url = "https://allowed.example"

        def set_default_timeout(self, _timeout: int) -> None:
            return None

        def goto(self, *_args: object, **_kwargs: object) -> None:
            return None

        def locator(self, _selector: str) -> _Locator:
            return _Locator()

    class _Browser:
        contexts = [object()]

    def _allowed(url: str, _policy: object) -> tuple[bool, str]:
        return ("denied.example" not in url, "host denied")

    with (
        patch.object(
            search_fallback, "_get_browser", return_value=cast("Any", _Browser())
        ),
        patch.object(search_fallback, "_find_workbench_page", return_value=_Page()),
        patch.object(search_fallback, "_install_ssrf_route", return_value=object()),
        patch.object(search_fallback, "_remove_ssrf_route"),
        patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
        patch("backend.services.egress_policy.is_url_allowed", side_effect=_allowed),
    ):
        links = search_fallback.browser_map(
            "https://allowed.example", egress_policy={"allowed_hosts": ["*.example"]}
        )

    assert links == ["https://allowed.example/a"]
