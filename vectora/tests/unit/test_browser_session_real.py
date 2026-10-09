"""Sessão de browser via CDP contra o Chromium real do Electron.

Diferente de test_tools_browser.py, que mocka Playwright deliberadamente,
este arquivo conecta ao Electron ativo e valida o ciclo de vida completo de
`backend/browser/session.py`, sem iniciar um navegador próprio.
"""

from __future__ import annotations

import http.server
import os
import threading

import httpx
import pytest

from backend.browser import session as browser_session


class _EchoHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>ok</body></html>")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silencia o log padrão do BaseHTTPRequestHandler nos testes


@pytest.fixture
def local_http_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _electron_cdp_available() -> bool:
    endpoint = os.environ.get("VECTORA_ELECTRON_CDP_URL", "").strip()
    if not endpoint:
        port = os.environ.get("VECTORA_ELECTRON_CDP_PORT", "9223").strip()
        if port.isdigit():
            endpoint = f"http://127.0.0.1:{port}"
    if not endpoint:
        return False
    try:
        return httpx.get(f"{endpoint}/json/version", timeout=1).is_success
    except Exception:
        return False


pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not _electron_cdp_available(),
        reason="Electron não está ativo ou não publicou o endpoint CDP",
    ),
]


@pytest.fixture(autouse=True)
async def _clean_sessions():
    """Garante nenhuma sessão de browser sobrando de um teste anterior."""
    await browser_session.close_all_browser_sessions()
    yield
    await browser_session.close_all_browser_sessions()


@pytest.mark.asyncio
async def test_get_browser_page_sobe_chromium_real_e_navega():
    page = await browser_session.get_browser_page("ws-real-1")
    await page.goto("about:blank")

    assert page.url == "about:blank"

    # Par de erro no mesmo teste: mesmo workspace_id reaproveita a MESMA
    # Page (contrato documentado no docstring do módulo) — não abre um
    # segundo Chromium por chamada.
    page_again = await browser_session.get_browser_page("ws-real-1")
    assert page_again is page


async def test_browser_navega_github_publico_real_sem_login():
    """Chromium deve acessar a issue pública sem cookies do GitHub."""
    page = await browser_session.get_browser_page("ws-github-public")
    response = await page.goto(
        "https://github.com/vectora-ltda/vectora/issues/317",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    assert response is not None
    assert response.status == 200
    assert "vectora" in (await page.title()).lower()


@pytest.mark.asyncio
async def test_close_browser_session_encerra_processo_real_e_recria_ao_reabrir():
    first_page = await browser_session.get_browser_page("ws-real-2")
    await first_page.goto("about:blank")

    await browser_session.close_browser_session("ws-real-2")

    second_page = await browser_session.get_browser_page("ws-real-2")
    assert second_page is not first_page
    await second_page.goto("about:blank")
    assert second_page.url == "about:blank"


@pytest.mark.asyncio
async def test_close_all_browser_sessions_fecha_multiplos_workspaces_reais():
    for ws_id in ("ws-multi-a", "ws-multi-b", "ws-multi-c"):
        page = await browser_session.get_browser_page(ws_id)
        await page.goto("about:blank")

    await browser_session.close_all_browser_sessions()

    assert browser_session._sessions == {}


@pytest.mark.asyncio
async def test_close_browser_session_workspace_inexistente_e_noop():
    # Nenhuma sessão aberta pra esse workspace — não deve lançar.
    await browser_session.close_browser_session("workspace-que-nunca-existiu")


@pytest.mark.asyncio
async def test_new_tab_cria_segunda_aba_com_cdp_session_propria():
    first_page = await browser_session.get_browser_page("ws-tabs-1")
    await first_page.goto("about:blank")

    second_tab_id = await browser_session.new_tab("ws-tabs-1", url="about:blank")

    tabs = await browser_session.list_tabs("ws-tabs-1")
    assert len(tabs) == 2
    assert any(t["tab_id"] == second_tab_id and t["active"] for t in tabs)

    cdp_first = await browser_session.get_cdp_session(
        "ws-tabs-1", tabs[0]["tab_id"] if tabs[0]["tab_id"] != second_tab_id else None
    )
    cdp_second = await browser_session.get_cdp_session("ws-tabs-1", second_tab_id)
    assert cdp_first is not cdp_second


@pytest.mark.asyncio
async def test_select_tab_troca_a_aba_ativa():
    first_page = await browser_session.get_browser_page("ws-tabs-2")
    await first_page.goto("about:blank")
    first_tab_id = (await browser_session.list_tabs("ws-tabs-2"))[0]["tab_id"]
    second_tab_id = await browser_session.new_tab("ws-tabs-2", url="about:blank")

    ok = await browser_session.select_tab("ws-tabs-2", first_tab_id)
    assert ok is True

    tabs = {t["tab_id"]: t for t in await browser_session.list_tabs("ws-tabs-2")}
    assert tabs[first_tab_id]["active"] is True
    assert tabs[second_tab_id]["active"] is False


@pytest.mark.asyncio
async def test_select_tab_inexistente_retorna_false():
    await browser_session.get_browser_page("ws-tabs-3")

    assert await browser_session.select_tab("ws-tabs-3", "nao-existe") is False


@pytest.mark.asyncio
async def test_close_tab_fechando_a_ultima_abre_uma_aba_em_branco_no_lugar():
    page = await browser_session.get_browser_page("ws-tabs-4")
    await page.goto("about:blank")
    only_tab_id = (await browser_session.list_tabs("ws-tabs-4"))[0]["tab_id"]

    ok = await browser_session.close_tab("ws-tabs-4", only_tab_id)

    assert ok is True
    tabs = await browser_session.list_tabs("ws-tabs-4")
    assert len(tabs) == 1
    assert tabs[0]["tab_id"] != only_tab_id


@pytest.mark.asyncio
async def test_close_tab_nao_ativa_promove_outra_aba_a_ativa():
    first_page = await browser_session.get_browser_page("ws-tabs-5")
    await first_page.goto("about:blank")
    first_tab_id = (await browser_session.list_tabs("ws-tabs-5"))[0]["tab_id"]
    second_tab_id = await browser_session.new_tab("ws-tabs-5", url="about:blank")

    ok = await browser_session.close_tab("ws-tabs-5", first_tab_id)

    assert ok is True
    tabs = {t["tab_id"]: t for t in await browser_session.list_tabs("ws-tabs-5")}
    assert first_tab_id not in tabs
    assert tabs[second_tab_id]["active"] is True


@pytest.mark.asyncio
async def test_close_tab_id_inexistente_retorna_false():
    await browser_session.get_browser_page("ws-tabs-6")

    assert await browser_session.close_tab("ws-tabs-6", "nao-existe") is False


@pytest.mark.asyncio
async def test_console_log_captura_mensagens_reais_da_pagina():
    page = await browser_session.get_browser_page("ws-console-1")
    tab = browser_session.get_tab_state("ws-console-1")
    await page.goto("about:blank")
    await page.evaluate("console.log('hello from page')")
    await page.wait_for_timeout(200)

    assert tab is not None
    assert any("hello from page" in entry["text"] for entry in tab.console_log)


@pytest.mark.asyncio
async def test_dialog_policy_accept_evita_travamento_em_alert_real():
    page = await browser_session.get_browser_page("ws-dialog-1")
    await page.goto("about:blank")
    ok = browser_session.set_dialog_policy("ws-dialog-1", "accept")
    assert ok is True

    # Sem a política, um alert() trava a chamada até alguém decidir — com
    # "accept" registrado, o listener no session.py resolve sozinho.
    result = await page.evaluate("() => { alert('oi'); return 'depois-do-alert'; }")

    assert result == "depois-do-alert"


@pytest.mark.asyncio
async def test_dialog_policy_sem_sessao_retorna_false():
    assert browser_session.set_dialog_policy("workspace-sem-sessao", "accept") is False


@pytest.mark.asyncio
async def test_cdp_session_permite_emular_viewport_mobile_real():
    page = await browser_session.get_browser_page("ws-emulate-1")
    await page.goto("about:blank")

    await page.set_viewport_size({"width": 375, "height": 812})
    size = page.viewport_size

    assert size == {"width": 375, "height": 812}


@pytest.mark.asyncio
async def test_network_log_captura_navegacao_real(local_http_server):
    page = await browser_session.get_browser_page("ws-network-1")
    tab = browser_session.get_tab_state("ws-network-1")
    await page.goto(local_http_server, wait_until="domcontentloaded")

    assert tab is not None
    assert len(tab.network_log) > 0
    assert any(e["url"].startswith(local_http_server) for e in tab.network_log)
    assert any(e["status"] == 200 for e in tab.network_log)
