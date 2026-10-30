"""Redirect guard contra um Chromium real e dois servidores HTTP locais.

O primeiro servidor redireciona (em um ou dois saltos) para o segundo; o
segundo contabiliza as requisições recebidas. Com a guarda ativa o destino
bloqueado não pode receber nenhuma, e sem ela (controle) recebe uma: isso
prova que o teste detecta o redirect e que ``page.route`` sozinho não basta.
Sem navegador utilizável neste ambiente o teste é ``skipped``.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from functools import partial
from typing import Any

import pytest
from playwright.async_api import Browser, Page, async_playwright

from backend.browser.redirect_guard import install_redirect_guard


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
def target() -> Iterator[tuple[http.server.HTTPServer, _Counter]]:
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
async def browser() -> AsyncIterator[Browser]:
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
    browser: Browser,
    start_url: str,
    blocked_port: int,
    *,
    guarded: bool,
) -> tuple[str | None, dict[str, str]]:
    """Navega com ``page.route`` ativo e, opcionalmente, a guarda de redirect."""
    context = await browser.new_context()
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
        release = await install_redirect_guard(page, check_url, blocked)
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
    browser: Browser,
    target: tuple[http.server.HTTPServer, _Counter],
    hops: int,
) -> None:
    server, counter = target
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
        await _navigate(browser, start, port, guarded=False)
        assert counter.hits == 1

        counter.hits = 0
        error, blocked = await _navigate(browser, start, port, guarded=True)
        assert counter.hits == 0
        assert error is not None and "ERR_BLOCKED_BY_CLIENT" in error
        assert blocked["reason"] == "destino privado"
    finally:
        middle.shutdown()
        if entry is not middle:
            entry.shutdown()


async def test_redirect_entre_hosts_permitidos_continua_funcionando(
    browser: Browser,
    target: tuple[http.server.HTTPServer, _Counter],
) -> None:
    """Par do caso de erro: um redirect permitido carrega a página final."""
    server, counter = target
    port = server.server_port
    entry = _serve(_redirector(lambda: f"http://127.0.0.1:{port}/ok"))
    try:
        error, blocked = await _navigate(
            browser, f"http://127.0.0.1:{entry.server_port}/inicio", 0, guarded=True
        )
        assert error is None
        assert blocked == {}
        assert counter.hits == 1
    finally:
        entry.shutdown()
