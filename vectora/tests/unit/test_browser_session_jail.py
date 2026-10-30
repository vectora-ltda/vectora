"""Contratos da sessão conectada ao Chromium do Electron via CDP."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.browser import session as browser_session


@pytest.fixture(autouse=True)
async def _clean_sessions() -> AsyncIterator[None]:
    await browser_session.close_all_browser_sessions()
    yield
    await browser_session.close_all_browser_sessions()


def test_endpoint_cdp_usa_porta_padrao_do_electron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VECTORA_ELECTRON_CDP_URL", raising=False)
    monkeypatch.delenv("VECTORA_ELECTRON_CDP_PORT", raising=False)
    assert browser_session._electron_cdp_endpoint() == "http://127.0.0.1:9223"


def test_endpoint_cdp_usa_url_explicita(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_ELECTRON_CDP_URL", "http://127.0.0.1:9223")
    assert browser_session._electron_cdp_endpoint() == "http://127.0.0.1:9223"


def test_endpoint_cdp_rejeita_host_remoto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_ELECTRON_CDP_URL", "http://10.0.0.8:9223")
    with pytest.raises(RuntimeError, match="loopback"):
        browser_session._electron_cdp_endpoint()


@pytest.mark.asyncio
async def test_get_browser_page_conecta_ao_electron_sem_lancar_chromium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VECTORA_ELECTRON_CDP_PORT", "9223")
    fake_page = MagicMock()
    fake_page.on = MagicMock()
    new_page = MagicMock()
    new_page.on = MagicMock()
    fake_cdp = MagicMock()
    fake_cdp.send = AsyncMock(
        return_value={"targetInfo": {"targetId": "target-jail-electron"}}
    )
    fake_cdp.detach = AsyncMock()
    fake_context = SimpleNamespace(
        pages=[fake_page],
        new_page=AsyncMock(return_value=new_page),
        new_cdp_session=AsyncMock(return_value=fake_cdp),
    )
    new_page.context = fake_context
    fake_page.context = fake_context
    fake_browser = SimpleNamespace(contexts=[fake_context], close=AsyncMock())
    fake_playwright = SimpleNamespace(
        chromium=SimpleNamespace(
            connect_over_cdp=AsyncMock(return_value=fake_browser),
            launch=AsyncMock(side_effect=AssertionError("não pode lançar Chromium")),
            launch_persistent_context=AsyncMock(
                side_effect=AssertionError("não pode lançar Chromium")
            ),
        ),
        stop=AsyncMock(),
    )
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: SimpleNamespace(start=AsyncMock(return_value=fake_playwright)),
    )

    page = await browser_session.get_browser_page("ws-electron")

    assert page is new_page
    fake_playwright.chromium.connect_over_cdp.assert_awaited_once_with(
        "http://127.0.0.1:9223"
    )
    fake_playwright.chromium.launch.assert_not_awaited()
    fake_playwright.chromium.launch_persistent_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_tab_fecha_pagina_se_goto_falha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha após ``new_page`` não pode deixar uma aba órfã no Chromium."""
    page = SimpleNamespace(
        goto=AsyncMock(side_effect=RuntimeError("net::ERR_FAILED")),
        close=AsyncMock(),
    )
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    monkeypatch.setitem(
        browser_session._sessions,
        "ws-failure",
        {"context": context, "tabs": {}, "active_tab_id": None},
    )

    with pytest.raises(RuntimeError, match="ERR_FAILED"):
        await browser_session.new_tab("ws-failure", url="https://example.com")

    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_tab_preserva_motivo_de_bloqueio_e_remove_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rota bloqueada deve produzir erro de policy sem exceção no callback."""
    registered: dict[str, object] = {}
    unroutes: list[tuple[str, object]] = []
    abort = AsyncMock()
    continue_ = AsyncMock()

    class _Page:
        async def route(self, pattern: str, handler: object) -> None:
            registered[pattern] = handler

        async def unroute(self, pattern: str, handler: object) -> None:
            assert registered[pattern] is handler
            unroutes.append((pattern, handler))

        async def goto(self, _url: str, **_kwargs: object) -> None:
            handler = cast("Callable[[object], Awaitable[object]]", registered["**/*"])
            route = SimpleNamespace(
                request=SimpleNamespace(url="http://127.0.0.1/private"),
                abort=abort,
                continue_=continue_,
            )
            await handler(route)
            raise RuntimeError("navigation aborted")

        close = AsyncMock()

    page = _Page()
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    monkeypatch.setitem(
        browser_session._sessions,
        "ws-policy",
        {"context": context, "tabs": {}, "active_tab_id": None},
    )

    with pytest.raises(RuntimeError, match="private network"):
        await browser_session.new_tab(
            "ws-policy",
            url="https://example.com",
            url_validator=lambda _url: (False, "private network"),
        )

    abort.assert_awaited_once()
    continue_.assert_not_awaited()
    assert len(unroutes) == 1
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_tab_mantem_guard_apos_navegacao_e_libera_ao_fechar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirects tardios continuam protegidos até o fechamento da aba."""
    registered: dict[str, object] = {}
    unroutes: list[tuple[str, object]] = []

    class _Page:
        url = "https://example.com"
        _target_id = ""

        async def route(self, pattern: str, handler: object) -> None:
            registered[pattern] = handler

        async def unroute(self, pattern: str, handler: object) -> None:
            unroutes.append((pattern, handler))

        async def goto(self, _url: str, **_kwargs: object) -> None:
            handler = cast("Callable[[object], Awaitable[object]]", registered["**/*"])
            route = SimpleNamespace(
                request=SimpleNamespace(url="https://example.com"),
                abort=AsyncMock(),
                continue_=AsyncMock(),
            )
            await handler(route)

        close = AsyncMock()

    page = _Page()
    page._target_id = "target-persistent-guard"
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    monkeypatch.setattr(
        browser_session,
        "_create_tab_state",
        AsyncMock(return_value=SimpleNamespace(page=page)),
    )
    monkeypatch.setitem(
        browser_session._sessions,
        "ws-persistent-guard",
        {"context": context, "tabs": {}, "active_tab_id": None},
    )

    tab_id = await browser_session.new_tab(
        "ws-persistent-guard",
        url="https://example.com",
        url_validator=lambda _url: (True, ""),
    )

    assert tab_id
    assert cast("Any", page)._vectora_route_handler is registered["**/*"]
    await browser_session._release_route_guard(page)
    assert unroutes == [("**/*", registered["**/*"])]


@pytest.mark.asyncio
async def test_new_tab_instala_guard_no_contexto_antes_do_primeiro_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A primeira requisição do popup também passa pelo mesmo egress guard."""
    registered: dict[str, object] = {}
    unroutes: list[tuple[str, object]] = []
    abort = AsyncMock()
    continue_ = AsyncMock()

    class _Context:
        async def route(self, pattern: str, handler: object) -> None:
            registered[pattern] = handler

        async def unroute(self, pattern: str, handler: object) -> None:
            assert registered[pattern] is handler
            unroutes.append((pattern, handler))

        async def new_page(self) -> object:
            return page

    class _Page:
        async def goto(self, _url: str, **_kwargs: object) -> None:
            handler = cast("Callable[[object], Awaitable[object]]", registered["**/*"])
            route = SimpleNamespace(
                request=SimpleNamespace(url="http://127.0.0.1/private"),
                abort=abort,
                continue_=continue_,
            )
            await handler(route)
            raise RuntimeError("navigation aborted")

        close = AsyncMock()

    page = _Page()
    context = _Context()
    monkeypatch.setitem(
        browser_session._sessions,
        "ws-context-policy",
        {"context": context, "tabs": {}, "active_tab_id": None},
    )

    with pytest.raises(RuntimeError, match="private network"):
        await browser_session.new_tab(
            "ws-context-policy",
            url="https://example.com",
            url_validator=lambda _url: (False, "private network"),
        )

    abort.assert_awaited_once()
    continue_.assert_not_awaited()
    assert len(unroutes) == 1
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_session_nao_fecha_chromium_do_electron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VECTORA_ELECTRON_CDP_PORT", "9223")
    fake_page = MagicMock()
    fake_page.on = MagicMock()
    new_page = MagicMock()
    new_page.on = MagicMock()
    fake_cdp = MagicMock()
    fake_cdp.send = AsyncMock(
        return_value={"targetInfo": {"targetId": "target-jail-electron"}}
    )
    fake_cdp.detach = AsyncMock()
    fake_context = SimpleNamespace(
        pages=[fake_page],
        new_page=AsyncMock(return_value=new_page),
        new_cdp_session=AsyncMock(return_value=fake_cdp),
    )
    new_page.context = fake_context
    fake_page.context = fake_context
    fake_browser = SimpleNamespace(contexts=[fake_context], close=AsyncMock())
    fake_playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=fake_browser)),
        stop=AsyncMock(),
    )
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: SimpleNamespace(start=AsyncMock(return_value=fake_playwright)),
    )

    await browser_session.get_browser_page("ws-electron")
    await browser_session.close_browser_session("ws-electron")

    fake_browser.close.assert_not_awaited()
    fake_playwright.stop.assert_awaited_once()


def test_find_agent_page_rejeita_vectora_app_e_ui() -> None:
    app_shell_page = MagicMock()
    app_shell_page.url = "vectora-app://app/session/123"

    localhost_page = MagicMock()
    localhost_page.url = "http://localhost:8080/app"

    devtools_page = MagicMock()
    devtools_page.url = "devtools://devtools/bundled/inspector.html"

    workbench_tab_page = MagicMock()
    workbench_tab_page.url = "https://github.com/vectora-ltda/vectora/pull/355"

    context_with_only_app = SimpleNamespace(
        pages=[app_shell_page, localhost_page, devtools_page]
    )
    with pytest.raises(
        RuntimeError, match="Nenhuma aba do Browser Workbench está aberta"
    ):
        browser_session._find_agent_page(context_with_only_app)

    context_with_real_tab = SimpleNamespace(pages=[app_shell_page, workbench_tab_page])
    assert browser_session._find_agent_page(context_with_real_tab) is workbench_tab_page


@pytest.mark.asyncio
async def test_close_tab_da_ultima_aba_libera_guards_antes_de_reusar_a_pagina(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A última aba é reaproveitada, então seus guards não podem sobreviver ao
    reset; se o reset falhar, a aba continua na sessão e mantém todos os guards."""
    from backend.tools import browser as browser_tools

    unroutes: list[tuple[str, object]] = []
    released: list[object] = []
    redirect_released: list[str] = []
    handler = object()

    async def _release_redirect() -> None:
        redirect_released.append("redirect")

    class _Owner:
        async def unroute(self, pattern: str, route_handler: object) -> None:
            unroutes.append((pattern, route_handler))

    page = SimpleNamespace(
        goto=AsyncMock(),
        _vectora_route_owner=_Owner(),
        _vectora_route_handler=handler,
        _vectora_redirect_release=_release_redirect,
    )
    tab = SimpleNamespace(
        page=page,
        console_log=[],
        network_log=[],
        _request_entries=[],
        dialog_policy=None,
    )

    async def _release(target: object) -> None:
        released.append(target)

    monkeypatch.setattr(browser_tools, "release_navigation_guard", _release)
    monkeypatch.setitem(
        browser_session._sessions,
        "ws-last-tab-guard",
        {"tabs": {"only": tab}, "active_tab_id": "only"},
    )

    assert await browser_session.close_tab("ws-last-tab-guard", "missing") is False
    assert unroutes == []
    assert released == []

    page.goto.side_effect = RuntimeError("reset falhou")
    assert await browser_session.close_tab("ws-last-tab-guard", "only") is False
    assert unroutes == []
    assert released == []
    assert redirect_released == []
    assert hasattr(page, "_vectora_route_handler")
    assert "only" in browser_session._sessions["ws-last-tab-guard"]["tabs"]

    page.goto.side_effect = None
    assert await browser_session.close_tab("ws-last-tab-guard", "only") is True
    assert unroutes == [("**/*", handler)]
    assert released == [page]
    assert redirect_released == ["redirect"]
    assert not hasattr(page, "_vectora_route_handler")
    page.goto.assert_awaited_with("about:blank", wait_until="domcontentloaded")
