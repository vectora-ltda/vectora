"""Guarda de redirect por CDP: cada salto é validado antes de o navegador enviá-lo."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from backend.browser.redirect_guard import install_redirect_guard


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


def _page(cdp: _FakeCdp) -> Any:
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

    release = await install_redirect_guard(_page(cdp), check, blocked)
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

    await install_redirect_guard(_page(cdp), check, blocked)
    await cdp.pause("1", "https://qualquer.example/")

    assert cdp.sent[-1][0] == "Fetch.failRequest"
    assert blocked["reason"] == "falha ao validar a URL"


async def test_pagina_sem_sessao_cdp_nao_instala_guarda() -> None:
    async def check(url: str) -> tuple[bool, str]:
        return True, ""

    assert await install_redirect_guard(SimpleNamespace(), check, {}) is None


async def test_navigation_guard_instala_e_libera_a_guarda_de_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools import browser as browser_tools

    cdp = _FakeCdp()
    routed: list[str] = []
    page = _page(cdp)

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
