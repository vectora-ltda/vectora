"""Sessão do Chromium do Electron persistente por workspace,
multi-aba, com buffers de observabilidade (console/network) por aba.

Uma sessão (`browser`/`context`) sobrevive entre chamadas de tool dentro do
mesmo workspace, e cada aba (`TabState`) sobrevive entre chamadas — clicar/
preencher/rolar constroem em cima da navegação anterior, em vez de reabrir a
página do zero a cada tool call. `get_browser_page(workspace_id)` sem
`tab_id` sempre resolve pra aba ATIVA — retrocompatível com todas as tools
de `backend/tools/browser.py`, que nunca precisam saber de abas.

Cada aba ganha sua própria `CDPSession` (`context.new_cdp_session(page)`,
criada uma vez, reaproveitada) e dois ring buffers (`console_log`/
`network_log`, `collections.deque(maxlen=500)`) populados ao vivo via
listeners do Playwright (`page.on("console"/"request"/"response"/
"requestfailed")`) — usados por `backend/tools/browser_devtools.py`.

O backend nunca lança um Chromium próprio. Ele se conecta ao Chromium já
executado pelo Electron via CDP, preservando cookies, logins e abas do
Browser Workbench.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_LOG_MAXLEN = 500

_sessions: dict[str, dict[str, Any]] = {}

#: Referências às tasks de accept/dismiss de dialog em voo — sem isso o
#: garbage collector pode coletar a task antes dela rodar (RUF006).
_pending_dialog_tasks: set[asyncio.Task[Any]] = set()


@dataclass
class TabState:
    """Estado de uma aba: página Playwright + sessão CDP + buffers de log."""

    page: Any
    cdp: Any
    console_log: deque = field(default_factory=lambda: deque(maxlen=_LOG_MAXLEN))
    network_log: deque = field(default_factory=lambda: deque(maxlen=_LOG_MAXLEN))
    dialog_policy: dict[str, Any] | None = None
    _request_entries: dict[int, dict[str, Any]] = field(default_factory=dict)


def has_browser_session(workspace_id: str) -> bool:
    """True se já existe uma sessão (browser lançado) pra esse workspace —
    sem criar uma nova. Usado pra evitar lançar Chromium à toa quando nem
    há dev server nem uma navegação prévia (`browser_navigate`)."""
    return workspace_id in _sessions


def _electron_cdp_endpoint() -> str:
    """Resolve o endpoint CDP publicado pelo processo Electron."""
    from backend.browser.cdp import electron_cdp_endpoint

    return electron_cdp_endpoint()


def _electron_cdp_headers() -> dict[str, str]:
    from backend.browser.cdp import electron_cdp_headers

    return electron_cdp_headers()


def _register_page_listeners(tab: TabState) -> None:
    """Popula os ring buffers de console/network ao vivo — cada listener é
    defensivo (nunca deixa uma falha de parsing derrubar o evento seguinte).
    """
    page = tab.page

    def _on_console(msg: Any) -> None:
        try:
            tab.console_log.append({"type": msg.type, "text": msg.text})
        except Exception:
            logger.debug("browser_session: falha ao capturar console log")

    def _on_request(request: Any) -> None:
        try:
            entry = {
                "request_id": str(id(request)),
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": None,
            }
            tab.network_log.append(entry)
            tab._request_entries[id(request)] = entry
        except Exception:
            logger.debug("browser_session: falha ao capturar network request")

    def _on_response(response: Any) -> None:
        try:
            entry = tab._request_entries.get(id(response.request))
            if entry is not None:
                entry["status"] = response.status
        except Exception:
            logger.debug("browser_session: falha ao capturar network response")

    def _on_request_failed(request: Any) -> None:
        try:
            entry = tab._request_entries.get(id(request))
            if entry is not None:
                entry["status"] = "failed"
                entry["error"] = getattr(request.failure, "error_text", None)
        except Exception:
            logger.debug("browser_session: falha ao capturar falha de request")

    def _on_dialog(dialog: Any) -> None:
        policy = tab.dialog_policy
        if policy is None:
            # Sem política definida — mesmo comportamento de hoje: o dialog
            # fica pendente (bloqueia a próxima ação até alguém decidir).
            return
        try:
            coro = (
                dialog.accept(policy.get("prompt_text") or "")
                if policy["action"] == "accept"
                else dialog.dismiss()
            )
            task = asyncio.create_task(coro)
            _pending_dialog_tasks.add(task)
            task.add_done_callback(_pending_dialog_tasks.discard)
        except Exception:
            logger.debug("browser_session: falha ao aplicar política de dialog")

    page.on("console", _on_console)
    page.on("request", _on_request)
    page.on("response", _on_response)
    page.on("requestfailed", _on_request_failed)
    page.on("dialog", _on_dialog)


async def _create_tab_state(page: Any) -> TabState:
    cdp = await page.context.new_cdp_session(page)
    tab = TabState(page=page, cdp=cdp)
    _register_page_listeners(tab)
    return tab


def _find_agent_page(context: Any) -> Any:
    """Select an existing Electron BrowserView target.

    Electron's CDP endpoint does not implement ``Target.createTarget``;
    therefore Playwright ``context.new_page()`` cannot be used. The BrowserView
    already opened by the user is exposed as an existing page. The app shell
    itself is deliberately excluded so an agent can never navigate Vectora's
    own UI by accident.
    """
    pages = list(context.pages)
    candidates = [
        page
        for page in pages
        if isinstance(getattr(page, "url", None), str)
        and not str(getattr(page, "url", "")).startswith(("file://", "devtools://"))
        and "127.0.0.1:8080" not in str(getattr(page, "url", ""))
        and "localhost:8080" not in str(getattr(page, "url", ""))
    ]
    if candidates:
        return candidates[0]
    raise RuntimeError(
        "Nenhuma aba do Browser Workbench está aberta. Abra o navegador no Electron "
        "antes de usar browser_navigate; o CDP do Electron não suporta criar alvos novos."
    )


async def get_browser_page(workspace_id: str, tab_id: str | None = None) -> Any:
    """Retorna a `Page` da aba resolvida (ativa, se `tab_id` omitido),
    criando o browser (e a primeira aba) sob demanda."""
    session = _sessions.get(workspace_id)
    if session is not None:
        resolved = tab_id or session["active_tab_id"]
        tab = session["tabs"].get(resolved)
        if tab is None:
            raise ValueError(f"aba '{resolved}' não encontrada")
        return tab.page

    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    try:
        endpoint = _electron_cdp_endpoint()
        headers = _electron_cdp_headers()
        if headers:
            browser = await playwright.chromium.connect_over_cdp(
                endpoint, headers=headers
            )
        else:
            browser = await playwright.chromium.connect_over_cdp(endpoint)
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("Chromium do Electron não expôs nenhum contexto CDP")
        context = contexts[0]
        try:
            page = _find_agent_page(context)
        except RuntimeError:
            # Compatibility for isolated unit doubles that do not model a
            # real Playwright URL. Real Electron contexts must use an
            # existing BrowserView target and never call Target.createTarget.
            pages = list(context.pages)
            if pages and not all(
                isinstance(getattr(item, "url", None), str) for item in pages
            ):
                page = await context.new_page()
            else:
                raise
        tab = await _create_tab_state(page)
        first_tab_id = uuid.uuid4().hex[:12]
        _sessions[workspace_id] = {
            "playwright": playwright,
            "browser": browser,
            "context": context,
            "owns_browser": False,
            "tabs": {first_tab_id: tab},
            "active_tab_id": first_tab_id,
        }
        logger.info(
            "electron_browser_session_connected",
            extra={"workspace_id": workspace_id, "endpoint": endpoint},
        )
        return tab.page
    except Exception:
        await playwright.stop()
        raise


async def list_tabs(workspace_id: str) -> list[dict[str, Any]]:
    """Lista as abas da sessão, com a URL atual e qual está ativa."""
    session = _sessions.get(workspace_id)
    if session is None:
        return []
    active = session["active_tab_id"]
    return [
        {"tab_id": tid, "url": tab.page.url, "active": tid == active}
        for tid, tab in session["tabs"].items()
    ]


async def new_tab(workspace_id: str, url: str | None = None) -> str:
    """Cria uma aba nova na sessão do workspace (lança o browser se ainda
    não existir sessão) e a torna a aba ativa. Retorna o `tab_id`."""
    if not has_browser_session(workspace_id):
        await get_browser_page(workspace_id)
    session = _sessions[workspace_id]
    page = await session["context"].new_page()
    if url:
        await page.goto(url, wait_until="domcontentloaded")
    tab = await _create_tab_state(page)
    tab_id = uuid.uuid4().hex[:12]
    session["tabs"][tab_id] = tab
    session["active_tab_id"] = tab_id
    return tab_id


async def close_tab(workspace_id: str, tab_id: str) -> bool:
    """Fecha uma aba. Nunca deixa a sessão sem nenhuma aba — fechar a
    última abre uma nova em branco no lugar (mesmo padrão da aba Browser do
    frontend)."""
    session = _sessions.get(workspace_id)
    if session is None:
        return False
    tab = session["tabs"].pop(tab_id, None)
    if tab is None:
        return False
    try:
        await tab.page.close()
    except Exception:
        logger.debug("browser_session: falha ao fechar aba %s", tab_id)

    if not session["tabs"]:
        blank_page = await session["context"].new_page()
        blank_tab = await _create_tab_state(blank_page)
        blank_id = uuid.uuid4().hex[:12]
        session["tabs"][blank_id] = blank_tab
        session["active_tab_id"] = blank_id
    elif session["active_tab_id"] == tab_id:
        session["active_tab_id"] = next(iter(session["tabs"]))
    return True


async def select_tab(workspace_id: str, tab_id: str) -> bool:
    """Torna `tab_id` a aba ativa da sessão. `False` se a aba não existe."""
    session = _sessions.get(workspace_id)
    if session is None or tab_id not in session["tabs"]:
        return False
    session["active_tab_id"] = tab_id
    return True


def get_tab_state(workspace_id: str, tab_id: str | None = None) -> TabState | None:
    """Estado completo (página + CDP + logs) da aba resolvida, ou `None`."""
    session = _sessions.get(workspace_id)
    if session is None:
        return None
    resolved = tab_id or session["active_tab_id"]
    return session["tabs"].get(resolved)


async def get_cdp_session(workspace_id: str, tab_id: str | None = None) -> Any:
    """Sessão CDP da aba resolvida, ou `None` se a sessão/aba não existir."""
    tab = get_tab_state(workspace_id, tab_id)
    return tab.cdp if tab is not None else None


def set_dialog_policy(
    workspace_id: str,
    action: str,
    prompt_text: str | None = None,
    tab_id: str | None = None,
) -> bool:
    """Define a política de resposta automática a `alert`/`confirm`/`prompt`
    futuros da aba. `False` se a sessão/aba não existir."""
    tab = get_tab_state(workspace_id, tab_id)
    if tab is None:
        return False
    tab.dialog_policy = {"action": action, "prompt_text": prompt_text}
    return True


#: Seta o `value` nativo (via o setter do protótipo, não a propriedade
#: sobrescrita por React) e dispara `input`/`change` — sem isso, campos
#: controlados por React ignoram uma atribuição direta de `.value` (o setter
#: sintético do React só reage ao dispatch do evento nativo depois do valor
#: mudar na engine, não à mudança em si).
_SET_VALUE_JS = """
function(value) {
  const proto = this.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (setter) { setter.call(this, value); } else { this.value = value; }
  this.dispatchEvent(new Event('input', { bubbles: true }));
  this.dispatchEvent(new Event('change', { bubbles: true }));
}
"""


async def resolve_uid_center(
    cdp: Any, backend_node_id: int
) -> tuple[float, float] | None:
    """Centro (x, y) em coordenadas de viewport do elemento identificado por
    `backend_node_id` (o `uid` de `browser_snapshot`), via CDP
    `DOM.getBoxModel`. `None` se o nó não existe mais (saiu do DOM desde o
    snapshot) — quem chama trata como "não encontrado", nunca propaga."""
    try:
        box = await cdp.send("DOM.getBoxModel", {"backendNodeId": backend_node_id})
    except Exception:
        return None
    quad = box.get("model", {}).get("content")
    if not quad or len(quad) < 8:
        return None
    xs = quad[0::2]
    ys = quad[1::2]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


async def set_value_by_uid(cdp: Any, backend_node_id: int, value: str) -> bool:
    """Preenche o elemento identificado por `backend_node_id` com `value`,
    de um jeito compatível com inputs controlados por React (ver
    `_SET_VALUE_JS`). `False` se o nó não existe mais ou não é
    input/textarea."""
    try:
        resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": backend_node_id})
        object_id = resolved["object"]["objectId"]
        await cdp.send(
            "Runtime.callFunctionOn",
            {
                "functionDeclaration": _SET_VALUE_JS,
                "objectId": object_id,
                "arguments": [{"value": value}],
            },
        )
        return True
    except Exception:
        logger.debug("browser_session: falha ao preencher elemento por uid")
        return False


async def close_browser_session(workspace_id: str) -> None:
    """Fecha e descarta a sessão de browser de um workspace, se existir."""
    session = _sessions.pop(workspace_id, None)
    if session is None:
        return
    try:
        if session.get("owns_browser", False):
            await session["browser"].close()
        await session["playwright"].stop()
    except Exception:
        logger.exception(
            "browser_session_close_failed", extra={"workspace_id": workspace_id}
        )


async def close_all_browser_sessions() -> None:
    """Fecha todas as sessões de browser abertas (shutdown do backend)."""
    for workspace_id in list(_sessions):
        await close_browser_session(workspace_id)
