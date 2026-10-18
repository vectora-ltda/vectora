"""Contratos da sessão conectada ao Chromium do Electron via CDP."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
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
