"""Endpoints REST de devtools do browser do agente
(`/workspaces/{id}/browser/devtools/*`) — espelham as tools de
`backend/tools/browser_devtools.py` pro painel visual do workbench.
Sessão ausente (agente nunca abriu página nesse workspace) sempre devolve
listas vazias / erro tipado, nunca 500."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.api.handlers.workspaces as ws_mod


class TestDevtoolsTabs:
    @pytest.mark.asyncio
    async def test_lista_abas_da_sessao_do_agente(self, monkeypatch):
        async def _fake_list_tabs(workspace_id):
            assert workspace_id == "ws1"
            return [{"tab_id": "t1", "url": "http://localhost:3000", "active": True}]

        monkeypatch.setattr("backend.browser.session.list_tabs", _fake_list_tabs)

        result = await ws_mod.devtools_tabs("ws1")

        assert len(result.tabs) == 1
        assert result.tabs[0].tab_id == "t1"
        assert result.tabs[0].active is True

    @pytest.mark.asyncio
    async def test_sem_sessao_devolve_lista_vazia(self, monkeypatch):
        async def _fake_list_tabs(workspace_id):
            return []

        monkeypatch.setattr("backend.browser.session.list_tabs", _fake_list_tabs)

        result = await ws_mod.devtools_tabs("ws-sem-sessao")

        assert result.tabs == []


class TestDevtoolsConsole:
    @pytest.mark.asyncio
    async def test_lista_mensagens_de_console(self, monkeypatch):
        tab = MagicMock()
        tab.console_log = [
            {"type": "log", "text": "hello"},
            {"type": "error", "text": "boom"},
        ]
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: tab
        )

        result = await ws_mod.devtools_console("ws1")

        assert [m.text for m in result.messages] == ["hello", "boom"]

    @pytest.mark.asyncio
    async def test_sem_aba_devolve_lista_vazia_nao_erro(self, monkeypatch):
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: None
        )

        result = await ws_mod.devtools_console("ws1", tab_id="inexistente")

        assert result.messages == []

    @pytest.mark.asyncio
    async def test_limpar_console(self, monkeypatch):
        tab = MagicMock()
        tab.console_log = MagicMock()
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: tab
        )

        result = await ws_mod.devtools_clear_console("ws1")

        tab.console_log.clear.assert_called_once()
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_limpar_console_sem_aba_retorna_erro_tipado(self, monkeypatch):
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: None
        )

        result = await ws_mod.devtools_clear_console("ws1")

        assert result.status == "error"


class TestDevtoolsNetwork:
    @pytest.mark.asyncio
    async def test_lista_requisicoes_filtrando_por_resource_type(self, monkeypatch):
        tab = MagicMock()
        tab.network_log = [
            {
                "request_id": "1",
                "url": "http://x/a.js",
                "method": "GET",
                "resource_type": "script",
                "status": 200,
            },
            {
                "request_id": "2",
                "url": "http://x/api",
                "method": "POST",
                "resource_type": "xhr",
                "status": 200,
            },
        ]
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: tab
        )

        result = await ws_mod.devtools_network("ws1", resource_type="xhr")

        assert len(result.requests) == 1
        assert result.requests[0].request_id == "2"

    @pytest.mark.asyncio
    async def test_sem_aba_devolve_lista_vazia(self, monkeypatch):
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: None
        )

        result = await ws_mod.devtools_network("ws1")

        assert result.requests == []


class TestDevtoolsEvaluate:
    @pytest.mark.asyncio
    async def test_avalia_script_com_sucesso(self, monkeypatch):
        tab = MagicMock()
        tab.page.evaluate = AsyncMock(return_value=42)
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: tab
        )

        result = await ws_mod.devtools_evaluate(
            "ws1", ws_mod.DevtoolsEvaluateRequest(script="1+41")
        )

        assert result.status == "ok"
        assert result.result == 42

    @pytest.mark.asyncio
    async def test_script_com_excecao_nao_propaga(self, monkeypatch):
        tab = MagicMock()
        tab.page.evaluate = AsyncMock(side_effect=RuntimeError("SyntaxError"))
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: tab
        )

        result = await ws_mod.devtools_evaluate(
            "ws1", ws_mod.DevtoolsEvaluateRequest(script="((")
        )

        assert result.status == "error"
        assert result.error is not None
        assert "SyntaxError" in result.error

    @pytest.mark.asyncio
    async def test_sem_aba_devolve_erro_tipado(self, monkeypatch):
        monkeypatch.setattr(
            "backend.browser.session.get_tab_state", lambda ws, tid=None: None
        )

        result = await ws_mod.devtools_evaluate(
            "ws1", ws_mod.DevtoolsEvaluateRequest(script="1")
        )

        assert result.status == "error"
        assert result.result is None
