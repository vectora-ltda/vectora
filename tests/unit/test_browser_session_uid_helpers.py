"""`resolve_uid_center`/`set_value_by_uid` — helpers CDP puros usados por
`browser_click`/`browser_fill` (parâmetro `uid`) e testáveis sem Chromium
real (CDP mockado via AsyncMock, mesmo padrão de test_browser_session_jail.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.browser import session as browser_session


class TestResolveUidCenter:
    @pytest.mark.asyncio
    async def test_calcula_centro_do_quad_de_conteudo(self):
        cdp = SimpleNamespace(
            send=AsyncMock(
                return_value={"model": {"content": [0, 0, 10, 0, 10, 20, 0, 20]}}
            )
        )

        center = await browser_session.resolve_uid_center(cdp, 42)

        assert center == (5.0, 10.0)
        cdp.send.assert_awaited_once_with("DOM.getBoxModel", {"backendNodeId": 42})

    @pytest.mark.asyncio
    async def test_no_inexistente_retorna_none_sem_lancar(self):
        cdp = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("No node found")))

        center = await browser_session.resolve_uid_center(cdp, 999)

        assert center is None

    @pytest.mark.asyncio
    async def test_resposta_sem_quad_de_conteudo_retorna_none(self):
        cdp = SimpleNamespace(send=AsyncMock(return_value={"model": {}}))

        center = await browser_session.resolve_uid_center(cdp, 1)

        assert center is None


class TestSetValueByUid:
    @pytest.mark.asyncio
    async def test_resolve_node_e_chama_setter_via_cdp(self):
        cdp = SimpleNamespace(
            send=AsyncMock(
                side_effect=[
                    {"object": {"objectId": "obj-1"}},
                    {},
                ]
            )
        )

        ok = await browser_session.set_value_by_uid(cdp, 103, "a@b.com")

        assert ok is True
        assert cdp.send.await_count == 2
        first_call = cdp.send.await_args_list[0]
        assert first_call.args == ("DOM.resolveNode", {"backendNodeId": 103})
        second_call = cdp.send.await_args_list[1]
        assert second_call.args[0] == "Runtime.callFunctionOn"
        assert second_call.args[1]["objectId"] == "obj-1"
        assert second_call.args[1]["arguments"] == [{"value": "a@b.com"}]

    @pytest.mark.asyncio
    async def test_no_inexistente_retorna_false_sem_lancar(self):
        cdp = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("No node found")))

        ok = await browser_session.set_value_by_uid(cdp, 999, "x")

        assert ok is False
