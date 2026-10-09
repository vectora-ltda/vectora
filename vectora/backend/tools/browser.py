"""Tools de browser do agente: automação sobre a página atual (dev server
local do workspace OU qualquer URL http/https navegada livremente) mais
gestão dos dev servers declarados em `.vectora/launch.json`.

`browser_navigate` é a porta de entrada pra navegação livre — nenhum
guardrail de host/porta, só esquema (`http`/`https`). As demais tools de
automação (`browser_click`/`browser_fill`/etc.) operam sobre a página já
carregada na sessão persistente do workspace conectada ao Electron via CDP
(`backend.browser.session.get_browser_page`); se nada foi navegado ainda,
caem no dev server ativo do workspace (mesmo fallback de sempre).
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.browser.dev_server import resolve_dev_server_url
from backend.browser.session import (
    get_browser_page,
    get_tab_state,
    has_browser_session,
    resolve_uid_center,
    set_value_by_uid,
)
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_NO_PAGE_ERROR = (
    "Error: nenhuma página aberta neste workspace. Use `browser_navigate` "
    "(URL livre) ou inicie um dev server (`browser_start`) antes de usar "
    "tools de browser."
)

_ALLOWED_SCHEMES = ("http", "https")


async def _resolve_page(ctx: ToolContext) -> tuple[Any, str]:
    """Retorna (page, "") em sucesso, ou (None, error) se não houver página
    resolvível — nem já navegada, nem dev server ativo do workspace.

    Só conecta ao browser do Electron (`get_browser_page`) quando já tem algo
    pra mostrar nele — uma sessão existente (navegação anterior via
    `browser_navigate`) ou um dev server confirmado — nunca inicia outro
    Chromium.
    """
    workspace_id = ctx.workspace_id
    session_key = workspace_id or "default"
    if has_browser_session(session_key):
        page = await get_browser_page(session_key)
        if page.url != "about:blank":
            return page, ""
        base_url = await resolve_dev_server_url(workspace_id)
        if base_url is None:
            return None, _NO_PAGE_ERROR
        await page.goto(base_url, wait_until="domcontentloaded")
        return page, ""

    base_url = await resolve_dev_server_url(workspace_id)
    if base_url is None:
        return None, _NO_PAGE_ERROR
    page = await get_browser_page(session_key)
    await page.goto(base_url, wait_until="domcontentloaded")
    return page, ""


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="browser",
        destructive=False,
        icon="globe",
    )
)
async def browser_navigate(url: str, ctx: ToolContext) -> str:
    """Navega a página do browser do workspace pra qualquer URL http(s) —
    site externo (Google, GitHub, docs, ...) ou dev server local, sem
    depender de nenhum servidor já rodando.

    Args:
        url: URL completa (`http://` ou `https://`) a navegar.

    Returns:
        Confirmação com a URL final carregada, ou mensagem de erro
        (esquema não permitido, timeout, DNS, etc.)
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        return (
            f"Error: esquema {scheme!r} não permitido — só http:// e "
            "https:// são aceitos pra navegação."
        )
    try:
        page = await get_browser_page(ctx.workspace_id or "default")
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        return f"[OK] Navegado para {page.url}"
    except Exception:
        logger.exception("browser_navigate failed", extra={"url": url})
        return f"Error: falha ao navegar para {url!r}. Veja logs."


@vtool(
    extras=ToolExtras(
        render_hint="image",
        category="browser",
        destructive=False,
        icon="camera",
    )
)
async def browser_screenshot(ctx: ToolContext) -> str:
    """Tira um screenshot da página atual do browser (base64 PNG, data URL).

    Returns:
        Data URL `data:image/png;base64,...` ou mensagem de erro.
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    try:
        png_bytes = await page.screenshot(type="png")
        encoded = base64.b64encode(png_bytes).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        logger.exception("browser_screenshot failed")
        return "Error: falha ao capturar screenshot. Veja logs."


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="browser",
        destructive=False,
        icon="mouse-pointer-click",
    )
)
async def browser_click(
    ctx: ToolContext,
    selector: str | None = None,
    uid: str | None = None,
) -> str:
    """Clica no elemento da página atual — por seletor CSS ou por `uid` (de
    `browser_snapshot`, mais confiável em markup sem classe/id estável).

    Args:
        selector: Seletor CSS (ex.: "button.submit", "#login-form input[type=email]").
            Ignorado se `uid` for informado.
        uid: uid de `browser_snapshot` (`backendDOMNodeId`) — usa CDP em vez
            de seletor, mais resistente a componentes gerados dinamicamente.

    Returns:
        Confirmação ou mensagem de erro (elemento não encontrado, nem
        `selector` nem `uid` informados, etc.)
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    if uid is not None:
        tab = get_tab_state(ctx.workspace_id)
        if tab is None:
            return _NO_PAGE_ERROR
        center = await resolve_uid_center(tab.cdp, int(uid)) if uid.isdigit() else None
        if center is None:
            return (
                f"Error: não foi possível localizar elemento com uid={uid!r} — "
                "pode ter saído do DOM desde o último `browser_snapshot`."
            )
        try:
            await tab.page.mouse.click(*center)
            return f"[OK] Clicado: uid={uid}"
        except Exception:
            logger.exception("browser_click (uid) failed", extra={"uid": uid})
            return f"Error: falha ao clicar no elemento uid={uid}."
    if not selector:
        return "Error: informe `selector` ou `uid`."
    try:
        await page.click(selector, timeout=5000)
        return f"[OK] Clicado: {selector}"
    except Exception:
        logger.exception("browser_click failed", extra={"selector": selector})
        return f"Error: não foi possível clicar em '{selector}' (elemento não encontrado ou não clicável)."


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="browser",
        destructive=False,
        icon="scroll",
    )
)
async def browser_scroll(
    ctx: ToolContext,
    direction: str = "down",
    amount: int = 600,
) -> str:
    """Rola a página atual do browser.

    Args:
        direction: "down" ou "up"
        amount: pixels a rolar

    Returns:
        Confirmação ou mensagem de erro.
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    if direction not in ("down", "up"):
        return "Error: `direction` deve ser 'down' ou 'up'."
    try:
        delta = amount if direction == "down" else -amount
        await page.mouse.wheel(0, delta)
        return f"[OK] Rolado {direction} ({amount}px)"
    except Exception:
        logger.exception("browser_scroll failed")
        return "Error: falha ao rolar a página. Veja logs."


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="browser",
        destructive=True,
        icon="keyboard",
    )
)
async def browser_fill(
    value: str,
    ctx: ToolContext,
    selector: str | None = None,
    uid: str | None = None,
) -> str:
    """Preenche um campo da página atual (input/textarea) — por seletor CSS
    ou por `uid` (de `browser_snapshot`).

    Args:
        value: Texto a preencher.
        selector: Seletor CSS do campo. Ignorado se `uid` for informado.
        uid: uid de `browser_snapshot` (`backendDOMNodeId`).

    Returns:
        Confirmação ou mensagem de erro.
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    if uid is not None:
        tab = get_tab_state(ctx.workspace_id)
        if tab is None:
            return _NO_PAGE_ERROR
        ok = (
            await set_value_by_uid(tab.cdp, int(uid), value) if uid.isdigit() else False
        )
        if not ok:
            return (
                f"Error: não foi possível preencher elemento com uid={uid!r} — "
                "pode ter saído do DOM ou não ser input/textarea."
            )
        return f"[OK] Preenchido: uid={uid}"
    if not selector:
        return "Error: informe `selector` ou `uid`."
    try:
        await page.fill(selector, value, timeout=5000)
        return f"[OK] Preenchido: {selector}"
    except Exception:
        logger.exception("browser_fill failed", extra={"selector": selector})
        return (
            f"Error: não foi possível preencher '{selector}' (elemento não encontrado)."
        )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="browser",
        destructive=False,
        icon="file-text",
    )
)
async def browser_read_dom(ctx: ToolContext, selector: str = "body") -> str:
    """Lê o texto visível da página atual (não o HTML cru).

    Args:
        selector: Seletor CSS da raiz da leitura (padrão: página inteira)

    Returns:
        Texto visível do elemento, ou mensagem de erro se o seletor não existir.
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    try:
        text = await page.inner_text(selector, timeout=5000)
        return text or "(vazio)"
    except Exception:
        logger.exception("browser_read_dom failed", extra={"selector": selector})
        return f"Error: seletor '{selector}' não encontrado na página."


_WAIT_FOR_STATES = ("visible", "hidden", "attached", "detached")


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="browser",
        destructive=False,
        icon="clock",
    )
)
async def browser_wait_for(
    selector: str,
    ctx: ToolContext,
    state: str = "visible",
    timeout_ms: int = 5000,
) -> str:
    """Espera até o elemento atingir o estado indicado antes de continuar —
    use antes de clicar/ler algo que pode não estar pronto ainda (ex.:
    conteúdo carregado via fetch depois da navegação).

    Args:
        selector: seletor CSS do elemento a esperar.
        state: "visible" | "hidden" | "attached" | "detached".
        timeout_ms: tempo máximo de espera em milissegundos.

    Returns:
        Confirmação ou erro (timeout, `state` inválido).
    """
    if state not in _WAIT_FOR_STATES:
        return f"Error: `state` deve ser um de {_WAIT_FOR_STATES}."
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    try:
        await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        return f"[OK] '{selector}' atingiu o estado '{state}'."
    except Exception:
        logger.exception(
            "browser_wait_for failed", extra={"selector": selector, "state": state}
        )
        return (
            f"Error: '{selector}' não atingiu o estado '{state}' dentro de "
            f"{timeout_ms}ms."
        )


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="browser",
        destructive=True,
        icon="move",
    )
)
async def browser_drag(
    source_selector: str,
    target_selector: str,
    ctx: ToolContext,
) -> str:
    """Arrasta o elemento `source_selector` e solta sobre `target_selector`
    (drag and drop) — útil pra reordenar listas, mover cards de kanban, etc.

    Args:
        source_selector: seletor CSS do elemento a arrastar.
        target_selector: seletor CSS de onde soltar.

    Returns:
        Confirmação ou mensagem de erro.
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    try:
        await page.drag_and_drop(source_selector, target_selector, timeout=5000)
        return f"[OK] Arrastado: '{source_selector}' -> '{target_selector}'"
    except Exception:
        logger.exception(
            "browser_drag failed",
            extra={"source": source_selector, "target": target_selector},
        )
        return (
            f"Error: falha ao arrastar '{source_selector}' até "
            f"'{target_selector}' (elemento não encontrado ou drag não suportado)."
        )


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="browser",
        destructive=True,
        icon="upload",
    )
)
async def browser_upload_file(
    selector: str,
    file_path: str,
    ctx: ToolContext,
) -> str:
    """Define o arquivo de um `<input type=file>` — sempre um caminho local
    do host (nunca simula clique/upload via UI de sistema, que não existe
    em modo headless).

    Args:
        selector: seletor CSS do input[type=file].
        file_path: caminho absoluto do arquivo no host a anexar.

    Returns:
        Confirmação ou erro (arquivo inexistente, elemento não é input de
        arquivo, etc.)
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    if not Path(file_path).is_file():
        return f"Error: arquivo '{file_path}' não existe no host."
    try:
        await page.set_input_files(selector, file_path, timeout=5000)
        return f"[OK] Arquivo anexado: '{file_path}' em '{selector}'"
    except Exception:
        logger.exception("browser_upload_file failed", extra={"selector": selector})
        return (
            f"Error: não foi possível anexar arquivo em '{selector}' (elemento "
            "não encontrado ou não é input[type=file])."
        )


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="browser",
        destructive=True,
        icon="keyboard",
    )
)
async def browser_fill_form(fields: dict[str, str], ctx: ToolContext) -> str:
    """Preenche múltiplos campos da página atual numa só chamada — cada
    chave de `fields` é um seletor CSS, cada valor o texto a preencher.
    Continua pros campos seguintes mesmo se um falhar (reporta por campo,
    nunca aborta no primeiro erro).

    Args:
        fields: mapa `{seletor_css: valor}`.

    Returns:
        JSON `{"status": "ok"|"partial"|"error", "results": {seletor:
        "ok"|"error: ..."}}`.
    """
    page, err = await _resolve_page(ctx)
    if page is None:
        return err
    if not fields:
        return json.dumps({"status": "error", "error": "`fields` vazio."})

    results: dict[str, str] = {}
    for selector, value in fields.items():
        try:
            await page.fill(selector, value, timeout=5000)
            results[selector] = "ok"
        except Exception:
            logger.exception(
                "browser_fill_form failed for field", extra={"selector": selector}
            )
            results[selector] = "error: elemento não encontrado ou não preenchível"

    failed = [k for k, v in results.items() if v != "ok"]
    status = (
        "ok" if not failed else ("error" if len(failed) == len(fields) else "partial")
    )
    return json.dumps({"status": status, "results": results})


async def _resolve_dev_server_name(
    workspace_id: str, name: str | None
) -> tuple[str | None, str]:
    """Resolve qual configuração de `.vectora/launch.json` usar.

    Retorna `(name, "")` em sucesso, ou `(None, mensagem_de_erro)` — nunca
    adivinha entre múltiplas configs ambíguas.
    """
    from backend.api.handlers.workspaces import get_launch_json

    launch = await get_launch_json(workspace_id)
    if not launch.configurations:
        return (
            None,
            (
                "Error: nenhuma configuração de dev server encontrada "
                "(.vectora/launch.json vazio ou ausente)."
            ),
        )
    if name:
        if not any(c.name == name for c in launch.configurations):
            available = ", ".join(c.name for c in launch.configurations)
            return (
                None,
                f"Error: configuração '{name}' não encontrada. Disponíveis: {available}.",
            )
        return name, ""
    if len(launch.configurations) == 1:
        return launch.configurations[0].name, ""
    available = ", ".join(c.name for c in launch.configurations)
    return (
        None,
        (
            f"Error: múltiplas configurações de dev server existem ({available}) "
            "— especifique `name`."
        ),
    )


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="browser",
        destructive=False,
        icon="play",
    )
)
async def browser_start(ctx: ToolContext, name: str | None = None) -> str:
    """Inicia o dev server do workspace ativo (mesmo efeito que o usuário
    clicar em "play" na aba Browser).

    Args:
        name: Nome da configuração em `.vectora/launch.json`. Se omitido e
            houver só uma configuração, usa ela; se houver mais de uma,
            retorna erro pedindo pra especificar.

    Returns:
        JSON `{"status": "ok"|"pending"|"error", "message": "..."}`. Em
        `"pending"`/`"error"`, chame `browser_logs` pra ver a saída real do
        processo antes de tentar de novo.
    """
    from backend.api.handlers.workspaces import BrowserStartRequest
    from backend.api.handlers.workspaces import browser_start as _http_browser_start

    workspace_id = ctx.workspace_id
    resolved_name, err = await _resolve_dev_server_name(workspace_id, name)
    if resolved_name is None:
        return err
    result = await _http_browser_start(
        workspace_id, BrowserStartRequest(name=resolved_name)
    )
    return json.dumps({"status": result.status, "message": result.message})


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="browser",
        destructive=False,
        icon="square",
    )
)
async def browser_stop(ctx: ToolContext, name: str | None = None) -> str:
    """Para o dev server do workspace ativo.

    Args:
        name: Nome da configuração em `.vectora/launch.json` (mesma regra de
            resolução de `browser_start`).

    Returns:
        JSON `{"status": "ok"|"error", "message": "..."}`.
    """
    from backend.api.handlers.workspaces import BrowserStopRequest
    from backend.api.handlers.workspaces import browser_stop as _http_browser_stop

    workspace_id = ctx.workspace_id
    resolved_name, err = await _resolve_dev_server_name(workspace_id, name)
    if resolved_name is None:
        return err
    result = await _http_browser_stop(
        workspace_id, BrowserStopRequest(name=resolved_name)
    )
    return json.dumps({"status": result.status, "message": result.message})


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="browser",
        destructive=False,
        icon="refresh-cw",
    )
)
async def browser_restart(ctx: ToolContext, name: str | None = None) -> str:
    """Reinicia o dev server (para se estiver rodando, depois inicia de
    novo) — útil depois de corrigir a causa de uma falha (ex.: rodar
    `bun install`) pra confirmar que resolveu.

    Args:
        name: Nome da configuração em `.vectora/launch.json` (mesma regra de
            resolução de `browser_start`).

    Returns:
        JSON `{"status": "ok"|"pending"|"error", "message": "..."}` do novo
        start.
    """
    from backend.api.handlers.workspaces import BrowserStartRequest, BrowserStopRequest
    from backend.api.handlers.workspaces import browser_start as _http_browser_start
    from backend.api.handlers.workspaces import browser_stop as _http_browser_stop

    workspace_id = ctx.workspace_id
    resolved_name, err = await _resolve_dev_server_name(workspace_id, name)
    if resolved_name is None:
        return err
    await _http_browser_stop(workspace_id, BrowserStopRequest(name=resolved_name))
    result = await _http_browser_start(
        workspace_id, BrowserStartRequest(name=resolved_name)
    )
    return json.dumps({"status": result.status, "message": result.message})


@vtool(
    extras=ToolExtras(
        render_hint="terminal_output",
        category="browser",
        destructive=False,
        icon="terminal",
    )
)
async def browser_logs(
    ctx: ToolContext, name: str | None = None, lines: int = 100
) -> str:
    """Lê as últimas linhas de stdout/stderr do dev server — inclusive
    depois dele ter travado/morrido (o histórico não é apagado ao parar).
    Use pra se autodiagnosticar quando `browser_start` retornar
    `"error"`/`"pending"`, ou quando as tools de automação
    (`browser_screenshot` etc.) falharem.

    Args:
        name: Nome da configuração em `.vectora/launch.json` (mesma regra de
            resolução de `browser_start`).
        lines: Quantidade de linhas mais recentes a retornar (padrão 100).

    Returns:
        As últimas `lines` linhas de saída, ou uma mensagem indicando que
        esse dev server nunca foi iniciado.
    """
    from backend.api.handlers.workspaces import browser_logs as _http_browser_logs

    workspace_id = ctx.workspace_id
    resolved_name, err = await _resolve_dev_server_name(workspace_id, name)
    if resolved_name is None:
        return err
    result = await _http_browser_logs(workspace_id, resolved_name)
    if not result.lines:
        return (
            f"(nenhum log disponível — dev server '{resolved_name}' nunca foi iniciado)"
        )
    return "\n".join(result.lines[-lines:])
