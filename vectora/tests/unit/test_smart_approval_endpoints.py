"""Endpoints REST da allowlist de aprovação inteligente."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from backend.api.handlers.threads import (
    SmartApprovalAllowlistRemoveRequest,
    SmartApprovalAllowlistRequest,
    add_smart_approval_allowlist,
    get_smart_approval_allowlist,
    remove_smart_approval_allowlist,
)
from backend.vtypes.workspace import Workspace


class _FakeRequestImpl:
    class _State:
        user = None

    def __init__(self) -> None:
        self.state = self._State()


def _fake_request() -> Request:
    return cast("Request", _FakeRequestImpl())


def _authenticated_request(user_id: str = "user-1") -> Request:
    request = _FakeRequestImpl()
    request.state.user = SimpleNamespace(id=user_id)
    return cast("Request", request)


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


@pytest.mark.asyncio
async def test_adicao_retorna_503_quando_persistencia_falha(
    _runtime_settings_isolado, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Não confirma a adição quando o banco rejeita a gravação."""
    from backend.services import smart_approval

    monkeypatch.setattr(
        _runtime_settings_isolado,
        "_persist",
        lambda *_args: (_ for _ in ()).throw(OSError("disco")),
    )
    with pytest.raises(HTTPException) as exc_info:
        await add_smart_approval_allowlist(
            SmartApprovalAllowlistRequest(
                workspace_id="ws-falha", tool_name="terminal", args={}
            ),
            _fake_request(),
        )
    assert exc_info.value.status_code == 503
    assert smart_approval.get_allowlist("ws-falha") == []


@pytest.mark.asyncio
async def test_revogacao_retorna_503_quando_persistencia_falha(
    _runtime_settings_isolado, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Não confirma a revogação quando o banco rejeita a gravação."""
    from backend.services.smart_approval import add_to_allowlist, get_allowlist

    add_to_allowlist("ws-falha-remove", "terminal", {"command": "pwd"})
    antes = get_allowlist("ws-falha-remove")
    from backend.services.smart_approval import allowlist_id

    rule_id = allowlist_id(antes[0])
    monkeypatch.setattr(
        _runtime_settings_isolado,
        "_persist",
        lambda *_args: (_ for _ in ()).throw(OSError("disco")),
    )
    with pytest.raises(HTTPException) as exc_info:
        await remove_smart_approval_allowlist(
            SmartApprovalAllowlistRemoveRequest(
                workspace_id="ws-falha-remove", rule_id=rule_id
            ),
            _fake_request(),
        )
    assert exc_info.value.status_code == 503
    assert get_allowlist("ws-falha-remove") == antes


@pytest.mark.asyncio
async def test_endpoints_rejeitam_workspace_nao_autorizado(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda _workspace_id, _request: None,
    )
    request = _authenticated_request()

    with pytest.raises(HTTPException) as get_error:
        await get_smart_approval_allowlist("privado", request)
    assert get_error.value.status_code == 404

    with pytest.raises(HTTPException) as add_error:
        await add_smart_approval_allowlist(
            SmartApprovalAllowlistRequest(
                workspace_id="privado", tool_name="terminal", args={}
            ),
            request,
        )
    assert add_error.value.status_code == 404

    with pytest.raises(HTTPException) as remove_error:
        await remove_smart_approval_allowlist(
            SmartApprovalAllowlistRemoveRequest(workspace_id="privado", rule_id="rule"),
            request,
        )
    assert remove_error.value.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_rejeitam_workspace_de_outro_usuario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services.smart_approval import add_to_allowlist, get_allowlist
    from backend.workspace.workspace import workspace_registry

    add_to_allowlist("ws-outro", "terminal", {"command": "git status"})
    antes = get_allowlist("ws-outro")
    monkeypatch.setitem(
        workspace_registry._workspaces,
        "ws-outro",
        Workspace(
            id="ws-outro",
            name="Workspace privado",
            cwd="/tmp/vectora-ws-outro",
            created_at="2026-09-10T00:00:00+00:00",
            owner_id="owner-1",
        ),
    )
    request = _authenticated_request("outro-usuario")

    with pytest.raises(HTTPException) as get_error:
        await get_smart_approval_allowlist("ws-outro", request)
    assert get_error.value.status_code == 403

    with pytest.raises(HTTPException) as add_error:
        await add_smart_approval_allowlist(
            SmartApprovalAllowlistRequest(
                workspace_id="ws-outro", tool_name="terminal", args={"command": "pwd"}
            ),
            request,
        )
    assert add_error.value.status_code == 403

    with pytest.raises(HTTPException) as remove_error:
        await remove_smart_approval_allowlist(
            SmartApprovalAllowlistRemoveRequest(
                workspace_id="ws-outro", rule_id="rule"
            ),
            request,
        )
    assert remove_error.value.status_code == 403
    assert get_allowlist("ws-outro") == antes


@pytest.mark.asyncio
async def test_mutacoes_concorrentes_preservam_todas_as_regras():
    from backend.services.smart_approval import add_to_allowlist, get_allowlist

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: add_to_allowlist(
                    "ws-concorrente", "terminal", {"command": f"cmd-{index}"}
                ),
                range(8),
            )
        )

    assert len(get_allowlist("ws-concorrente")) == 8


@pytest.mark.asyncio
async def test_auditoria_de_mutacao_nao_expoe_assinatura(monkeypatch):
    from backend.rbac import auth

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda _workspace_id, _request: SimpleNamespace(id="workspace"),
    )
    eventos: list[dict[str, object]] = []

    async def fake_db():
        return object()

    async def fake_audit(_db, _user_id, action, **kwargs):
        eventos.append({"action": action, **kwargs})

    monkeypatch.setattr(auth, "get_db_for_audit", fake_db)
    monkeypatch.setattr(auth, "write_audit", fake_audit)
    request = _authenticated_request("auditor-1")
    resposta = await add_smart_approval_allowlist(
        SmartApprovalAllowlistRequest(
            workspace_id="ws-audit", tool_name="terminal", args={"command": "pwd"}
        ),
        request,
    )
    await remove_smart_approval_allowlist(
        SmartApprovalAllowlistRemoveRequest(
            workspace_id="ws-audit", rule_id=resposta.allowlist[0].id
        ),
        request,
    )

    assert [event["action"] for event in eventos] == [
        "smart_approval.add",
        "smart_approval.remove",
    ]
    assert all("signature" not in event for event in eventos)
