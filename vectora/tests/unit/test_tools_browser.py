"""Tools de browser: navegação livre (`browser_navigate`) + automação
(screenshot/click/scroll/fill/read_dom) + gestão de dev server.

`browser_navigate` aceita URLs http(s) permitidas pelo snapshot de egress.
As demais tools de automação operam sobre a página já navegada
(sessão persistente); sem nada navegado ainda, caem no dev server ativo do
workspace (`.vectora/launch.json` + `browser_start`). Playwright é mockado
nestes testes (não depende de Chromium instalado).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from backend.services.egress_policy import EgressPolicy
from backend.services.policy_compiler import PolicySnapshot
from backend.tools import browser as browser_tools
from backend.tools.context import ToolContext


def _ctx(workspace_id: str = "ws1") -> ToolContext:
    return ToolContext(workspace_id=workspace_id)


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.goto = AsyncMock(side_effect=self._goto)
        self.title = AsyncMock(return_value="Example Domain")
        self.screenshot = AsyncMock(return_value=b"fake-png-bytes")
        self.click = AsyncMock()
        self.fill = AsyncMock()
        self.inner_text = AsyncMock(return_value="Olá mundo")
        self.mouse = AsyncMock()
        self.mouse.wheel = AsyncMock()
        self.wait_for_selector = AsyncMock()
        self.drag_and_drop = AsyncMock()
        self.set_input_files = AsyncMock()
        self.route = AsyncMock()
        self.unroute = AsyncMock()

    async def _goto(self, url: str, **_kwargs: object) -> None:
        """Update the fake page URL after a navigation call."""
        self.url = url


@pytest.fixture
def fake_page(monkeypatch: pytest.MonkeyPatch) -> _FakePage:
    """Provide a fake browser page for navigation tool tests."""
    page = _FakePage()
    monkeypatch.setattr(browser_tools, "get_browser_page", AsyncMock(return_value=page))
    return page


@pytest.mark.asyncio
async def test_browser_navigate_navega_para_url_http(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Navigate to an allowed HTTP URL and return its structured result."""
    """URL http/https é aceita e navega a página persistente do workspace."""
    result = await browser_tools.browser_navigate(url="https://example.com", ctx=_ctx())

    assert result.startswith("[OK]")
    assert "https://example.com" in result
    fake_page.goto.assert_awaited_once_with(
        "https://example.com", wait_until="domcontentloaded", timeout=15000
    )
    assert "título: Example Domain" in result


@pytest.mark.asyncio
async def test_navigation_guard_aborta_url_bloqueada_e_remove_o_mesmo_handler(
    fake_page: _FakePage,
) -> None:
    """Abort blocked navigation and remove the exact registered handler."""
    snapshot = PolicySnapshot(egress_policy=EgressPolicy(allowed_hosts=["example.com"]))
    handler, blocked = await browser_tools._install_navigation_guard(
        fake_page, snapshot
    )
    assert handler is not None
    route = SimpleNamespace(
        request=SimpleNamespace(url="http://127.0.0.1/private"),
        abort=AsyncMock(),
        continue_=AsyncMock(),
    )

    await handler(route)

    route.abort.assert_awaited_once()
    route.continue_.assert_not_awaited()
    assert blocked["reason"]
    await browser_tools._remove_navigation_guard(fake_page, handler)
    fake_page.unroute.assert_awaited_once_with("**/*", handler)


@pytest.mark.asyncio
async def test_navigation_guard_continua_url_permitida(fake_page: _FakePage) -> None:
    """Allow navigation when the egress policy accepts the destination."""
    snapshot = PolicySnapshot(egress_policy=EgressPolicy(allowed_hosts=["example.com"]))
    handler, blocked = await browser_tools._install_navigation_guard(
        fake_page, snapshot
    )
    assert handler is not None
    route = SimpleNamespace(
        request=SimpleNamespace(url="https://example.com/docs"),
        abort=AsyncMock(),
        continue_=AsyncMock(),
    )

    await handler(route)

    route.continue_.assert_awaited_once()
    route.abort.assert_not_awaited()
    assert blocked == {}


@pytest.mark.asyncio
async def test_browser_navigate_exercita_guard_e_remove_handler_em_falha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public navigation tool aborts blocked routes and keeps the guard."""
    registered: dict[str, object] = {}
    abort = AsyncMock()
    continue_ = AsyncMock()

    class _RoutedPage:
        url = "https://example.com"
        title = AsyncMock(return_value="Example")

        async def route(self, pattern: str, handler: object) -> None:
            registered[pattern] = handler

        async def unroute(self, pattern: str, handler: object) -> None:
            assert registered[pattern] is handler

        async def goto(self, url: str, **_kwargs: object) -> None:
            route_handler = cast("Any", registered["**/*"])
            route = SimpleNamespace(
                request=SimpleNamespace(url="http://127.0.0.1/private"),
                abort=abort,
                continue_=continue_,
            )
            await route_handler(route)  # type: ignore[operator]
            raise RuntimeError("blocked by route")

    page = _RoutedPage()
    monkeypatch.setattr(browser_tools, "get_browser_page", AsyncMock(return_value=page))
    result = await browser_tools.browser_navigate(url="https://example.com", ctx=_ctx())

    assert result.startswith("Error: navegação bloqueada")
    abort.assert_awaited_once()
    continue_.assert_not_awaited()
    assert "**/*" in registered


@pytest.mark.asyncio
async def test_browser_navigate_guard_continua_rota_permitida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public navigation tool continues an allowed route and keeps its guard."""
    registered: dict[str, object] = {}
    abort = AsyncMock()
    continue_ = AsyncMock()

    class _RoutedPage:
        url = "https://example.com/docs"
        title = AsyncMock(return_value="Example")

        async def route(self, pattern: str, handler: object) -> None:
            registered[pattern] = handler

        async def unroute(self, pattern: str, handler: object) -> None:
            assert registered[pattern] is handler

        async def goto(self, url: str, **_kwargs: object) -> None:
            route_handler = cast("Any", registered["**/*"])
            route = SimpleNamespace(
                request=SimpleNamespace(url=url),
                abort=abort,
                continue_=continue_,
            )
            await route_handler(route)  # type: ignore[operator]

    page = _RoutedPage()
    monkeypatch.setattr(browser_tools, "get_browser_page", AsyncMock(return_value=page))
    result = await browser_tools.browser_navigate(
        url="https://example.com/docs", ctx=_ctx()
    )

    assert result.startswith("[OK]")
    continue_.assert_awaited_once()
    abort.assert_not_awaited()
    assert "**/*" in registered


@pytest.mark.asyncio
async def test_release_navigation_guard_remove_handler() -> None:
    """Fechar explicitamente uma aba revoga a guarda persistente."""
    page = SimpleNamespace(unroute=AsyncMock())
    handler = object()
    browser_tools._navigation_guards[id(page)] = (page, handler, {})

    await browser_tools.release_navigation_guard(page)

    page.unroute.assert_awaited_once_with("**/*", handler)
    assert id(page) not in browser_tools._navigation_guards


@pytest.mark.asyncio
async def test_browser_navigate_recusa_esquema_nao_http(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Erro/borda: file:// e outros esquemas não http(s) são recusados antes
    de qualquer tentativa de navegação."""
    result = await browser_tools.browser_navigate(url="file:///etc/passwd", ctx=_ctx())

    assert result.startswith("Error:")
    assert "esquema" in result.lower()
    fake_page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_browser_navigate_reusa_snapshot_e_bloqueia_redirect(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """O snapshot do lote governa também o destino final do redirect."""
    snapshot = PolicySnapshot(egress_policy=EgressPolicy(allowed_hosts=["example.com"]))
    ctx = _ctx()
    ctx._extra["policy_snapshot"] = snapshot

    def _must_not_compile(_ctx: ToolContext) -> PolicySnapshot:
        """Fail if the already compiled turn snapshot is ignored."""
        raise AssertionError("o snapshot do turno deveria ser reutilizado")

    monkeypatch.setattr(browser_tools, "compile_policy_snapshot", _must_not_compile)

    async def _goto(url: str, **_kwargs: object) -> None:
        """Record a permitted page navigation in the fake browser."""
        fake_page.url = (
            "http://127.0.0.1/private" if url == "https://example.com" else url
        )

    fake_page.goto = AsyncMock(side_effect=_goto)
    result = await browser_tools.browser_navigate(url="https://example.com", ctx=ctx)

    assert "redirect bloqueado" in result


@pytest.mark.asyncio
async def test_browser_navigate_javascript_scheme_recusado(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Erro/borda: `javascript:` (injeção via URL) também é recusado."""
    result = await browser_tools.browser_navigate(url="javascript:alert(1)", ctx=_ctx())

    assert result.startswith("Error:")
    fake_page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_browser_navigate_falha_de_rede_devolve_erro_claro(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Erro/borda: falha real de navegação (DNS, timeout) vira mensagem de
    erro estruturada, nunca exceção crua."""
    fake_page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))

    result = await browser_tools.browser_navigate(
        url="https://dominio-que-nao-existe-xyz.test", ctx=_ctx()
    )

    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_browser_navigate_bloqueia_redirect_para_destino_privado(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Erro/regressão: o destino final também passa pelo perímetro SSRF."""

    async def _goto(url: str, **_kwargs: object) -> None:
        """Record a navigation request for the redirect fixture."""
        fake_page.url = (
            "http://127.0.0.1:8080/private" if url == "https://example.com" else url
        )

    fake_page.goto = AsyncMock(side_effect=_goto)
    result = await browser_tools.browser_navigate(url="https://example.com", ctx=_ctx())

    assert "redirect bloqueado" in result
    assert fake_page.goto.await_count == 2
    assert fake_page.goto.await_args_list[-1].args[0] == "about:blank"


@pytest.mark.asyncio
async def test_browser_screenshot_returns_data_url_when_preview_running(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Preview ativo → screenshot navega pra URL do preview e devolve data URL base64."""
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )

    result = await browser_tools.browser_screenshot(ctx=_ctx())

    assert result.startswith("data:image/png;base64,")
    fake_page.goto.assert_awaited_once_with(
        "http://localhost:5173", wait_until="domcontentloaded"
    )


@pytest.mark.asyncio
async def test_browser_screenshot_returns_error_when_no_preview_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem preview rodando no workspace, a tool recusa (não navega pra internet livre)."""
    monkeypatch.setattr(
        browser_tools, "resolve_dev_server_url", AsyncMock(return_value=None)
    )

    result = await browser_tools.browser_screenshot(ctx=_ctx())

    assert result.startswith("Error:")
    assert "browser_navigate" in result.lower()


@pytest.mark.asyncio
async def test_browser_click_success_and_selector_not_found(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Clique bem-sucedido confirma o seletor; seletor inexistente devolve erro claro."""
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )

    ok = await browser_tools.browser_click(selector="#submit", ctx=_ctx())
    assert "OK" in ok
    assert "#submit" in ok

    fake_page.click.side_effect = TimeoutError("not found")
    err = await browser_tools.browser_click(selector="#ghost", ctx=_ctx())
    assert err.startswith("Error:")
    assert "#ghost" in err


@pytest.mark.asyncio
async def test_browser_fill_writes_value_and_rejects_missing_field(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )

    ok = await browser_tools.browser_fill(
        selector="input[name=email]", value="a@b.com", ctx=_ctx()
    )
    assert "OK" in ok
    fake_page.fill.assert_awaited_once_with(
        "input[name=email]", "a@b.com", timeout=5000
    )

    fake_page.fill.side_effect = TimeoutError("missing")
    err = await browser_tools.browser_fill(selector="#missing", value="x", ctx=_ctx())
    assert err.startswith("Error:")


@pytest.mark.asyncio
async def test_browser_click_e_fill_sem_selector_nem_uid_retornam_erro(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Borda: nem `selector` nem `uid` informados — nunca tenta clicar/
    preencher às cegas."""
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )

    click_err = await browser_tools.browser_click(ctx=_ctx())
    assert click_err.startswith("Error:")

    fill_err = await browser_tools.browser_fill(value="x", ctx=_ctx())
    assert fill_err.startswith("Error:")


@pytest.mark.asyncio
async def test_browser_click_por_uid_clica_no_centro_do_box_model(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """`uid` (de browser_snapshot) resolve via CDP box model e clica no
    centro — não depende de seletor CSS."""
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )
    tab_page = SimpleNamespace(mouse=SimpleNamespace(click=AsyncMock()))
    tab = SimpleNamespace(cdp=SimpleNamespace(), page=tab_page)
    monkeypatch.setattr(browser_tools, "get_tab_state", lambda _wid: tab)
    monkeypatch.setattr(
        browser_tools, "resolve_uid_center", AsyncMock(return_value=(10.0, 20.0))
    )

    result = await browser_tools.browser_click(uid="102", ctx=_ctx())

    assert result.startswith("[OK]")
    assert "uid=102" in result
    tab_page.mouse.click.assert_awaited_once_with(10.0, 20.0)


@pytest.mark.asyncio
async def test_browser_click_por_uid_nao_encontrado_retorna_erro(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Erro/borda: `uid` de um elemento que já saiu do DOM (snapshot
    desatualizado) devolve erro tipado, não exceção."""
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )
    tab = SimpleNamespace(cdp=SimpleNamespace(), page=SimpleNamespace())
    monkeypatch.setattr(browser_tools, "get_tab_state", lambda _wid: tab)
    monkeypatch.setattr(
        browser_tools, "resolve_uid_center", AsyncMock(return_value=None)
    )

    result = await browser_tools.browser_click(uid="999", ctx=_ctx())

    assert result.startswith("Error:")
    assert "999" in result


@pytest.mark.asyncio
async def test_browser_click_por_uid_nao_numerico_retorna_erro(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    """Borda: `uid` que não é o `backendDOMNodeId` numérico esperado."""
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )
    tab = SimpleNamespace(cdp=SimpleNamespace(), page=SimpleNamespace())
    monkeypatch.setattr(browser_tools, "get_tab_state", lambda _wid: tab)
    resolve_mock = AsyncMock()
    monkeypatch.setattr(browser_tools, "resolve_uid_center", resolve_mock)

    result = await browser_tools.browser_click(uid="not-a-number", ctx=_ctx())

    assert result.startswith("Error:")
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_fill_por_uid_preenche_via_cdp(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )
    tab = SimpleNamespace(cdp=SimpleNamespace(), page=SimpleNamespace())
    monkeypatch.setattr(browser_tools, "get_tab_state", lambda _wid: tab)
    set_value_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(browser_tools, "set_value_by_uid", set_value_mock)

    result = await browser_tools.browser_fill(uid="103", value="a@b.com", ctx=_ctx())

    assert result.startswith("[OK]")
    set_value_mock.assert_awaited_once_with(tab.cdp, 103, "a@b.com")


@pytest.mark.asyncio
async def test_browser_fill_por_uid_falha_retorna_erro(
    monkeypatch: pytest.MonkeyPatch, fake_page: _FakePage
) -> None:
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )
    tab = SimpleNamespace(cdp=SimpleNamespace(), page=SimpleNamespace())
    monkeypatch.setattr(browser_tools, "get_tab_state", lambda _wid: tab)
    monkeypatch.setattr(
        browser_tools, "set_value_by_uid", AsyncMock(return_value=False)
    )

    result = await browser_tools.browser_fill(uid="103", value="x", ctx=_ctx())

    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_browser_read_dom_returns_text_and_empty_on_missing_selector(
    monkeypatch, fake_page
):
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )

    text = await browser_tools.browser_read_dom(ctx=_ctx())
    assert text == "Olá mundo"

    fake_page.inner_text.side_effect = TimeoutError("missing")
    err = await browser_tools.browser_read_dom(selector="#nao-existe", ctx=_ctx())
    assert err.startswith("Error:")
    assert "#nao-existe" in err


@pytest.mark.asyncio
async def test_browser_scroll_accepts_down_up_and_rejects_invalid_direction(
    monkeypatch, fake_page
):
    monkeypatch.setattr(
        browser_tools,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )

    ok = await browser_tools.browser_scroll(direction="down", ctx=_ctx())
    assert "OK" in ok

    err = await browser_tools.browser_scroll(direction="sideways", ctx=_ctx())
    assert err.startswith("Error:")


# ---------------------------------------------------------------------------
# browser_start/stop/restart/logs (0.7): paridade de controle usuário↔agente
# — o agente ganha as mesmas ações que a aba Preview já oferece ao usuário.
# As tools importam as funções HTTP de backend.api.handlers.workspaces por
# import tardio (dentro do corpo, evita import circular) — o monkeypatch
# tem que mirar o módulo real, não o atributo do módulo de tools.
# ---------------------------------------------------------------------------


def _launch_json(names: list[str]):
    import backend.api.handlers.workspaces as ws_mod

    return ws_mod.LaunchJsonModel(
        configurations=[
            ws_mod.LaunchConfigModel(
                name=n, runtimeExecutable="bun", runtimeArgs=["run", "dev"], port=3000
            )
            for n in names
        ]
    )


class TestResolvePreviewName:
    @pytest.mark.asyncio
    async def test_single_config_auto_resolves_when_name_omitted(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json(["web"]))
        )

        name, err = await browser_tools._resolve_dev_server_name("ws1", None)

        assert name == "web"
        assert err == ""

    @pytest.mark.asyncio
    async def test_multiple_configs_without_name_returns_error(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod,
            "get_launch_json",
            AsyncMock(return_value=_launch_json(["web", "api"])),
        )

        name, err = await browser_tools._resolve_dev_server_name("ws1", None)

        assert name is None
        assert "web" in err and "api" in err

    @pytest.mark.asyncio
    async def test_unknown_name_returns_error(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json(["web"]))
        )

        name, err = await browser_tools._resolve_dev_server_name("ws1", "ghost")

        assert name is None
        assert "ghost" in err

    @pytest.mark.asyncio
    async def test_empty_launch_json_returns_clear_error(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json([]))
        )

        name, err = await browser_tools._resolve_dev_server_name("ws1", None)

        assert name is None
        assert "launch.json" in err


class TestPreviewStartStopRestartTools:
    @pytest.mark.asyncio
    async def test_preview_start_delegates_to_http_handler(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json(["web"]))
        )
        monkeypatch.setattr(
            ws_mod,
            "browser_start",
            AsyncMock(
                return_value=ws_mod.StatusResponse(status="ok", message="rodando")
            ),
        )

        result = await browser_tools.browser_start(ctx=_ctx())

        assert '"status": "ok"' in result
        assert "rodando" in result

    @pytest.mark.asyncio
    async def test_preview_start_with_ambiguous_name_returns_error_without_calling_http(
        self, monkeypatch
    ):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod,
            "get_launch_json",
            AsyncMock(return_value=_launch_json(["web", "api"])),
        )
        http_start = AsyncMock()
        monkeypatch.setattr(ws_mod, "browser_start", http_start)

        result = await browser_tools.browser_start(ctx=_ctx())

        assert result.startswith("Error:")
        http_start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preview_stop_delegates_to_http_handler(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json(["web"]))
        )
        monkeypatch.setattr(
            ws_mod,
            "browser_stop",
            AsyncMock(
                return_value=ws_mod.StatusResponse(status="ok", message="parado")
            ),
        )

        result = await browser_tools.browser_stop(ctx=_ctx())

        assert '"status": "ok"' in result
        assert "parado" in result

    @pytest.mark.asyncio
    async def test_preview_restart_stops_then_starts(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json(["web"]))
        )
        calls: list[str] = []

        async def _fake_stop(workspace_id, req):
            calls.append("stop")
            return ws_mod.StatusResponse(status="ok", message="parado")

        async def _fake_start(workspace_id, req):
            calls.append("start")
            return ws_mod.StatusResponse(status="ok", message="rodando")

        monkeypatch.setattr(ws_mod, "browser_stop", _fake_stop)
        monkeypatch.setattr(ws_mod, "browser_start", _fake_start)

        result = await browser_tools.browser_restart(ctx=_ctx())

        assert calls == ["stop", "start"]
        assert '"status": "ok"' in result
        assert "rodando" in result


class TestPreviewLogsTool:
    @pytest.mark.asyncio
    async def test_returns_joined_lines_from_buffer(self, monkeypatch):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json(["web"]))
        )
        monkeypatch.setattr(
            ws_mod,
            "browser_logs",
            AsyncMock(
                return_value=ws_mod.BrowserLogsResponse(lines=["compiling...", "ready"])
            ),
        )

        result = await browser_tools.browser_logs(ctx=_ctx())

        assert result == "compiling...\nready"

    @pytest.mark.asyncio
    async def test_never_started_returns_clear_message_not_empty_string(
        self, monkeypatch
    ):
        import backend.api.handlers.workspaces as ws_mod

        monkeypatch.setattr(
            ws_mod, "get_launch_json", AsyncMock(return_value=_launch_json(["web"]))
        )
        monkeypatch.setattr(
            ws_mod,
            "browser_logs",
            AsyncMock(return_value=ws_mod.BrowserLogsResponse(lines=[])),
        )

        result = await browser_tools.browser_logs(ctx=_ctx())

        assert result != ""
        assert "web" in result
        assert "nunca foi iniciado" in result


class TestBrowserWaitFor:
    @pytest.mark.asyncio
    async def test_estado_invalido_retorna_erro_sem_chamar_playwright(
        self, monkeypatch, fake_page
    ):
        result = await browser_tools.browser_wait_for(
            selector="#x", state="invalido", ctx=_ctx()
        )

        assert result.startswith("Error:")
        fake_page.wait_for_selector.assert_not_called()

    @pytest.mark.asyncio
    async def test_sucesso_confirma_estado_atingido(self, monkeypatch, fake_page):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )

        result = await browser_tools.browser_wait_for(
            selector="#ready", state="visible", timeout_ms=2000, ctx=_ctx()
        )

        assert result.startswith("[OK]")
        fake_page.wait_for_selector.assert_awaited_once_with(
            "#ready", state="visible", timeout=2000
        )

    @pytest.mark.asyncio
    async def test_timeout_devolve_erro_tipado_nao_excecao(
        self, monkeypatch, fake_page
    ):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )
        fake_page.wait_for_selector.side_effect = TimeoutError("timeout")

        result = await browser_tools.browser_wait_for(
            selector="#nunca-aparece", ctx=_ctx()
        )

        assert result.startswith("Error:")
        assert "#nunca-aparece" in result


class TestBrowserDrag:
    @pytest.mark.asyncio
    async def test_arrasta_com_sucesso(self, monkeypatch, fake_page):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )

        result = await browser_tools.browser_drag(
            source_selector="#card-1", target_selector="#column-done", ctx=_ctx()
        )

        assert result.startswith("[OK]")
        fake_page.drag_and_drop.assert_awaited_once_with(
            "#card-1", "#column-done", timeout=5000
        )

    @pytest.mark.asyncio
    async def test_elemento_nao_encontrado_retorna_erro(self, monkeypatch, fake_page):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )
        fake_page.drag_and_drop.side_effect = TimeoutError("not found")

        result = await browser_tools.browser_drag(
            source_selector="#ghost", target_selector="#target", ctx=_ctx()
        )

        assert result.startswith("Error:")


class TestBrowserUploadFile:
    @pytest.mark.asyncio
    async def test_arquivo_inexistente_no_host_retorna_erro_sem_chamar_playwright(
        self, monkeypatch, fake_page, tmp_path
    ):
        result = await browser_tools.browser_upload_file(
            selector="input[type=file]",
            file_path=str(tmp_path / "nao-existe.png"),
            ctx=_ctx(),
        )

        assert result.startswith("Error:")
        fake_page.set_input_files.assert_not_called()

    @pytest.mark.asyncio
    async def test_anexa_arquivo_existente_com_sucesso(
        self, monkeypatch, fake_page, tmp_path
    ):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )
        f = tmp_path / "avatar.png"
        f.write_bytes(b"fake-png")

        result = await browser_tools.browser_upload_file(
            selector="input[type=file]", file_path=str(f), ctx=_ctx()
        )

        assert result.startswith("[OK]")
        fake_page.set_input_files.assert_awaited_once_with(
            "input[type=file]", str(f), timeout=5000
        )


class TestBrowserFillForm:
    @pytest.mark.asyncio
    async def test_campos_vazios_retorna_erro(self, monkeypatch, fake_page):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )

        result = await browser_tools.browser_fill_form(fields={}, ctx=_ctx())

        import json as _json

        assert _json.loads(result)["status"] == "error"

    @pytest.mark.asyncio
    async def test_todos_os_campos_preenchidos_com_sucesso(
        self, monkeypatch, fake_page
    ):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )

        import json as _json

        result = _json.loads(
            await browser_tools.browser_fill_form(
                fields={"#name": "Bruno", "#email": "b@x.com"}, ctx=_ctx()
            )
        )

        assert result["status"] == "ok"
        assert result["results"] == {"#name": "ok", "#email": "ok"}

    @pytest.mark.asyncio
    async def test_falha_parcial_continua_pros_demais_campos_e_reporta_status_partial(
        self, monkeypatch, fake_page
    ):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )

        async def _fill(selector, value, **_kwargs):
            if selector == "#missing":
                raise TimeoutError("not found")

        fake_page.fill.side_effect = _fill

        import json as _json

        result = _json.loads(
            await browser_tools.browser_fill_form(
                fields={"#ok": "x", "#missing": "y"}, ctx=_ctx()
            )
        )

        assert result["status"] == "partial"
        assert result["results"]["#ok"] == "ok"
        assert result["results"]["#missing"].startswith("error:")

    @pytest.mark.asyncio
    async def test_todos_os_campos_falham_reporta_status_error(
        self, monkeypatch, fake_page
    ):
        monkeypatch.setattr(
            browser_tools,
            "resolve_dev_server_url",
            AsyncMock(return_value="http://localhost:5173"),
        )
        fake_page.fill.side_effect = TimeoutError("not found")

        import json as _json

        result = _json.loads(
            await browser_tools.browser_fill_form(
                fields={"#a": "1", "#b": "2"}, ctx=_ctx()
            )
        )

        assert result["status"] == "error"
