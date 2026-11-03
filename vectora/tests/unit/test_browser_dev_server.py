"""resolve_dev_server_url: só aponta pra dev servers que o workspace já subiu."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.browser.dev_server import resolve_dev_server_url


@pytest.mark.asyncio
async def test_resolve_dev_server_url_returns_port_of_running_server(monkeypatch):
    cfg = MagicMock()
    cfg.configurations = [MagicMock(name="web", port=5173)]
    cfg.configurations[0].name = "web"

    running_proc = MagicMock(returncode=None)

    import backend.api.handlers.workspaces as workspaces_mod

    monkeypatch.setattr(workspaces_mod, "get_launch_json", AsyncMock(return_value=cfg))
    monkeypatch.setattr(workspaces_mod, "_browser_procs", {"ws1::web": running_proc})
    monkeypatch.setattr(workspaces_mod, "_is_port_open", AsyncMock(return_value=True))

    url = await resolve_dev_server_url("ws1")

    assert url == "http://localhost:5173"


@pytest.mark.asyncio
async def test_resolve_dev_server_url_returns_none_when_process_alive_but_port_closed(
    monkeypatch,
):
    """Bug reproduzido ao vivo: processo do dev server já existe (recém
    spawnado) mas ainda não terminou de compilar/bindar a porta — navegar
    pra lá cedo demais dá ERR_CONNECTION_REFUSED. `resolve_dev_server_url` só
    deve considerar "pronto" com a porta de fato aceitando conexão."""
    cfg = MagicMock()
    cfg.configurations = [MagicMock(name="web", port=5173)]
    cfg.configurations[0].name = "web"

    running_proc = MagicMock(returncode=None)

    import backend.api.handlers.workspaces as workspaces_mod

    monkeypatch.setattr(workspaces_mod, "get_launch_json", AsyncMock(return_value=cfg))
    monkeypatch.setattr(workspaces_mod, "_browser_procs", {"ws1::web": running_proc})
    monkeypatch.setattr(workspaces_mod, "_is_port_open", AsyncMock(return_value=False))

    url = await resolve_dev_server_url("ws1")

    assert url is None


@pytest.mark.asyncio
async def test_resolve_dev_server_url_returns_none_when_nothing_running(monkeypatch):
    cfg = MagicMock()
    cfg.configurations = []

    import backend.api.handlers.workspaces as workspaces_mod

    monkeypatch.setattr(workspaces_mod, "get_launch_json", AsyncMock(return_value=cfg))

    url = await resolve_dev_server_url("ws-sem-dev-server")

    assert url is None


@pytest.mark.asyncio
async def test_snapshot_libera_porta_do_dev_server_em_execucao(monkeypatch):
    """Navegar para o dev server do workspace passa pela política de egress
    mesmo quando a porta não está na lista configurada."""
    from unittest.mock import AsyncMock

    from backend.services import policy_compiler
    from backend.services.egress_policy import EgressPolicy
    from backend.tools import browser as browser_mod

    monkeypatch.setattr(
        browser_mod,
        "resolve_dev_server_url",
        AsyncMock(return_value="http://localhost:5173"),
    )
    snapshot = policy_compiler.PolicySnapshot(
        workspace_id="ws1", egress_policy=EgressPolicy()
    )

    ajustado = await browser_mod._snapshot_com_dev_server(
        ctx=_ctx_com_workspace("ws1"), snapshot=snapshot
    )

    assert ajustado.validate_call(
        "browser_navigate", {"url": "http://localhost:5173/app"}
    ) == (True, "")
    assert 5173 in ajustado.egress_policy.allow_localhost_dev_ports
    assert snapshot.egress_policy.allow_localhost_dev_ports == []


@pytest.mark.asyncio
async def test_snapshot_sem_dev_server_nao_libera_porta_local(monkeypatch):
    """Erro/borda: sem dev server no ar, localhost continua bloqueado."""
    from unittest.mock import AsyncMock

    from backend.services import policy_compiler
    from backend.services.egress_policy import EgressPolicy
    from backend.tools import browser as browser_mod

    monkeypatch.setattr(
        browser_mod, "resolve_dev_server_url", AsyncMock(return_value=None)
    )
    snapshot = policy_compiler.PolicySnapshot(
        workspace_id="ws1", egress_policy=EgressPolicy()
    )

    ajustado = await browser_mod._snapshot_com_dev_server(
        ctx=_ctx_com_workspace("ws1"), snapshot=snapshot
    )

    assert ajustado is snapshot
    allowed, _ = ajustado.validate_call(
        "browser_navigate", {"url": "http://localhost:5173/app"}
    )
    assert allowed is False


def _ctx_com_workspace(workspace_id: str):
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.workspace_id = workspace_id
    return ctx
