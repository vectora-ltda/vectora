"""Endpoints REST da allowlist de aprovação inteligente."""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import Request

from backend.api.handlers.threads import (
    SmartApprovalAllowlistRemoveRequest,
    SmartApprovalAllowlistRequest,
    add_smart_approval_allowlist,
    get_smart_approval_allowlist,
    remove_smart_approval_allowlist,
)


class _FakeRequestImpl:
    class _State:
        user = None

    state = _State()


def _fake_request() -> Request:
    return cast("Request", _FakeRequestImpl())


@pytest.fixture(autouse=True)
def _runtime_settings_isolado(tmp_path, monkeypatch):
    from backend.workspace.runtime_settings import RuntimeSettings

    isolado = RuntimeSettings(tmp_path / "rt.db")
    import backend.services.smart_approval as sa

    monkeypatch.setattr(sa, "_runtime_settings", lambda: isolado)
    return isolado


@pytest.mark.asyncio
async def test_add_e_remove_via_endpoint():
    resposta = await add_smart_approval_allowlist(
        SmartApprovalAllowlistRequest(
            workspace_id="ws1", tool_name="terminal", args={"command": "git status"}
        ),
        _fake_request(),
    )
    assert len(resposta.allowlist) == 1

    listado = await get_smart_approval_allowlist("ws1", _fake_request())
    assert listado.allowlist == resposta.allowlist

    removida = await remove_smart_approval_allowlist(
        SmartApprovalAllowlistRemoveRequest(
            workspace_id="ws1", rule_id=resposta.allowlist[0].id
        ),
        _fake_request(),
    )
    assert removida.allowlist == []


@pytest.mark.asyncio
async def test_get_workspace_desconhecido_no_modo_local_usa_store() -> None:
    """O launcher local não tem registry de workspaces, mas ainda deve
    conseguir listar regras persistidas como os endpoints POST/DELETE."""
    from backend.services.smart_approval import add_to_allowlist

    add_to_allowlist("ws-local", "terminal", {"command": "git status"})
    resposta = await get_smart_approval_allowlist("ws-local", _fake_request())
    assert resposta.allowlist[0].label == "Regra 1"
    assert resposta.allowlist[0].id


@pytest.mark.asyncio
async def test_regra_longa_usa_id_opaco_e_pode_ser_revogada():
    from backend.services.smart_approval import add_to_allowlist

    comando = "x" * 200
    add_to_allowlist("ws-long", "terminal", {"command": comando})
    resposta = await get_smart_approval_allowlist("ws-long", _fake_request())
    item = resposta.allowlist[0]
    assert len(item.id) == 64
    assert comando not in item.model_dump_json()

    removida = await remove_smart_approval_allowlist(
        SmartApprovalAllowlistRemoveRequest(workspace_id="ws-long", rule_id=item.id),
        _fake_request(),
    )
    assert removida.allowlist == []


@pytest.mark.asyncio
async def test_workspace_vazio_vira_400_nao_500():
    """Erro/borda: `ValueError` do módulo vira HTTP 400 com mensagem clara,
    não um 500 cru que não diz o que corrigir."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await add_smart_approval_allowlist(
            SmartApprovalAllowlistRequest(
                workspace_id="", tool_name="terminal", args={}
            ),
            _fake_request(),
        )
    assert exc_info.value.status_code == 400
