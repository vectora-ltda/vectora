"""Contratos da sessão conectada ao Chromium do Electron via CDP."""

from __future__ import annotations

import asyncio
import http.server
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from functools import partial
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import Browser, Page, async_playwright

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


# --- Guarda de redirect por CDP ------------------------------------------


class _FakeCdp:
    """Sessão CDP que registra os comandos e deixa o teste disparar eventos."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, Any] = {}
        self.detached = False

    def on(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler

    async def send(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.sent.append((method, params or {}))

    async def detach(self) -> None:
        self.detached = True

    async def pause(self, request_id: str, url: str) -> None:
        self.handlers["Fetch.requestPaused"](
            {"requestId": request_id, "request": {"url": url}}
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)


def _page_with_cdp(cdp: _FakeCdp) -> Any:
    async def new_cdp_session(_page: object) -> _FakeCdp:
        return cdp

    return SimpleNamespace(context=SimpleNamespace(new_cdp_session=new_cdp_session))


async def test_cada_salto_e_validado_e_o_bloqueado_falha_antes_do_envio() -> None:
    cdp = _FakeCdp()
    seen: list[str] = []
    blocked: dict[str, str] = {}

    async def check(url: str) -> tuple[bool, str]:
        seen.append(url)
        return ("interno" not in url, "destino privado")

    release = await browser_session.install_redirect_guard(
        _page_with_cdp(cdp), check, blocked
    )
    assert release is not None
    assert cdp.sent[0] == ("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})

    await cdp.pause("1", "https://permitido.example/inicio")
    await cdp.pause("2", "http://interno.local/segredo")
    await cdp.pause("3", "data:text/plain,ok")

    assert seen == ["https://permitido.example/inicio", "http://interno.local/segredo"]
    assert cdp.sent[1:] == [
        ("Fetch.continueRequest", {"requestId": "1"}),
        (
            "Fetch.failRequest",
            {"requestId": "2", "errorReason": "BlockedByClient"},
        ),
        ("Fetch.continueRequest", {"requestId": "3"}),
    ]
    assert blocked == {"reason": "destino privado"}

    await release()
    assert ("Fetch.disable", {}) in cdp.sent
    assert cdp.detached


async def test_falha_ao_validar_bloqueia_em_vez_de_liberar() -> None:
    cdp = _FakeCdp()
    blocked: dict[str, str] = {}

    async def check(url: str) -> tuple[bool, str]:
        raise RuntimeError("política indisponível")

    await browser_session.install_redirect_guard(_page_with_cdp(cdp), check, blocked)
    await cdp.pause("1", "https://qualquer.example/")

    assert cdp.sent[-1][0] == "Fetch.failRequest"
    assert blocked["reason"] == "falha ao validar a URL"


async def test_pagina_sem_sessao_cdp_nao_instala_guarda() -> None:
    async def check(url: str) -> tuple[bool, str]:
        return True, ""

    assert (
        await browser_session.install_redirect_guard(SimpleNamespace(), check, {})
        is None
    )


async def test_navigation_guard_instala_e_libera_a_guarda_de_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools import browser as browser_tools

    cdp = _FakeCdp()
    routed: list[str] = []
    page = _page_with_cdp(cdp)

    async def route(pattern: str, handler: Any) -> None:
        routed.append(pattern)

    async def unroute(pattern: str, handler: Any) -> None:
        routed.remove(pattern)

    page.route, page.unroute = route, unroute
    snapshot: Any = SimpleNamespace(
        egress_policy=SimpleNamespace(allowed_hosts=["permitido.example"])
    )

    async def allowed(url: str, policy: object) -> tuple[bool, str, None]:
        return ("permitido.example" in url, "fora da allowlist", None)

    monkeypatch.setattr(browser_tools, "async_is_url_allowed", allowed)
    handler, blocked = await browser_tools._install_navigation_guard(page, snapshot)
    assert handler is not None and routed == ["**/*"]

    await cdp.pause("1", "https://permitido.example/")
    await cdp.pause("2", "http://169.254.169.254/")
    assert [method for method, _ in cdp.sent[1:]] == [
        "Fetch.continueRequest",
        "Fetch.failRequest",
    ]
    assert blocked["reason"] == "fora da allowlist"

    await browser_tools.release_navigation_guard(page)
    assert routed == [] and cdp.detached


# --- Guarda de redirect contra um Chromium real --------------------------


class _Counter:
    def __init__(self) -> None:
        self.hits = 0


def _serve(handler: type[http.server.BaseHTTPRequestHandler]) -> http.server.HTTPServer:
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _redirector(
    location: Callable[[], str],
) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", location())
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


@pytest.fixture
def redirect_target() -> Iterator[tuple[http.server.HTTPServer, _Counter]]:
    counter = _Counter()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            counter.hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"target")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = _serve(Handler)
    yield server, counter
    server.shutdown()


@pytest.fixture
async def real_browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser | None = None
        launchers = (
            playwright.chromium.launch,
            partial(playwright.chromium.launch, channel="msedge"),
            partial(playwright.chromium.launch, channel="chrome"),
        )
        for launch in launchers:
            try:
                launched = await launch()
                break
            except Exception:  # noqa: S112 - tentativa de outro canal
                continue
        if launched is None:
            pytest.skip("nenhum Chromium utilizável neste ambiente")
        try:
            yield launched
        finally:
            await launched.close()


async def _navigate(
    real_browser: Browser,
    start_url: str,
    blocked_port: int,
    *,
    guarded: bool,
) -> tuple[str | None, dict[str, str]]:
    """Navega com ``page.route`` ativo e, opcionalmente, a guarda de redirect."""
    context = await real_browser.new_context()
    page: Page = await context.new_page()
    blocked: dict[str, str] = {}

    async def check_url(url: str) -> tuple[bool, str]:
        if f":{blocked_port}/" in url:
            return False, "destino privado"
        return True, ""

    async def route_handler(route: Any) -> None:
        allowed, reason = await check_url(route.request.url)
        if allowed:
            await route.continue_()
        else:
            blocked["route"] = reason
            await route.abort()

    await page.route("**/*", route_handler)
    release = None
    if guarded:
        release = await browser_session.install_redirect_guard(page, check_url, blocked)
        assert release is not None
    error: str | None = None
    try:
        await page.goto(start_url, timeout=8000)
    except Exception as exc:
        error = str(exc).splitlines()[0]
    if release is not None:
        await release()
    await context.close()
    return error, blocked


@pytest.mark.parametrize("hops", [1, 2])
async def test_redirect_para_destino_bloqueado_nao_chega_ao_servidor(
    real_browser: Browser,
    redirect_target: tuple[http.server.HTTPServer, _Counter],
    hops: int,
) -> None:
    server, counter = redirect_target
    port = server.server_port
    middle = _serve(_redirector(lambda: f"http://127.0.0.1:{port}/segredo"))
    entry = (
        _serve(_redirector(lambda: f"http://127.0.0.1:{middle.server_port}/meio"))
        if hops == 2
        else middle
    )
    start = f"http://127.0.0.1:{entry.server_port}/inicio"
    try:
        # Controle: sem a guarda, o ``route`` não vê o salto e o destino é atingido.
        await _navigate(real_browser, start, port, guarded=False)
        assert counter.hits == 1

        counter.hits = 0
        error, blocked = await _navigate(real_browser, start, port, guarded=True)
        assert counter.hits == 0
        assert error is not None and "ERR_BLOCKED_BY_CLIENT" in error
        assert blocked["reason"] == "destino privado"
    finally:
        middle.shutdown()
        if entry is not middle:
            entry.shutdown()


async def test_redirect_entre_hosts_permitidos_continua_funcionando(
    real_browser: Browser,
    redirect_target: tuple[http.server.HTTPServer, _Counter],
) -> None:
    """Par do caso de erro: um redirect permitido carrega a página final."""
    server, counter = redirect_target
    port = server.server_port
    entry = _serve(_redirector(lambda: f"http://127.0.0.1:{port}/ok"))
    try:
        error, blocked = await _navigate(
            real_browser,
            f"http://127.0.0.1:{entry.server_port}/inicio",
            0,
            guarded=True,
        )
        assert error is None
        assert blocked == {}
        assert counter.hits == 1
    finally:
        entry.shutdown()
