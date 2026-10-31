"""Endpoints REST da allowlist de aprovação inteligente."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

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
        user: object | None = None

    def __init__(self) -> None:
        self.state = self._State()


def _fake_request() -> Request:
    return cast("Request", _FakeRequestImpl())


def _authenticated_request(user_id: str = "user-1") -> Request:
    request = _FakeRequestImpl()
    request.state.user = SimpleNamespace(id=user_id)
    return cast("Request", request)


def _grant_request(
    *, workspace_id: str = "ws1", args: dict[str, object] | None = None
) -> SmartApprovalAllowlistRequest:
    return SmartApprovalAllowlistRequest(
        workspace_id=workspace_id,
        thread_id="thread-1",
        interrupt_id="interrupt-1",
        run_id="run-1",
        tool_name="terminal",
        args=args or {"command": "git status"},
    )


def _install_pending(monkeypatch, body: SmartApprovalAllowlistRequest) -> None:
    from backend.api.handlers import threads

    class Store:
        async def get_pending_approval(self, _thread_id: str) -> dict[str, object]:
            return {
                "interrupt_id": body.interrupt_id,
                "run_id": body.run_id,
                "tool_name": body.tool_name,
                "args": body.args,
            }

        async def get_agent_run(self, _run_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                run_id=body.run_id,
                thread_id=body.thread_id,
                workspace_id=body.workspace_id,
            )

    async def get_store():
        return Store()

    async def ownership(_thread_id: str, _request: Request) -> None:
        return None

    monkeypatch.setattr(threads, "_get_session_store", get_store)
    monkeypatch.setattr(threads, "_assert_existing_thread_ownership", ownership)


@pytest.fixture(autouse=True)
def _runtime_settings_isolado(tmp_path, monkeypatch):
    from backend.workspace.runtime_settings import RuntimeSettings

    isolado = RuntimeSettings(tmp_path / "rt.db")
    import backend.services.smart_approval as sa

    monkeypatch.setattr(sa, "_runtime_settings", lambda: isolado)
    return isolado


@pytest.mark.asyncio
async def test_add_e_remove_via_endpoint(monkeypatch):
    body = _grant_request()
    _install_pending(monkeypatch, body)
    resposta = await add_smart_approval_allowlist(
        body,
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

    add_to_allowlist(
        "ws-local", "terminal", {"command": "git status"}, run_id="run-local"
    )
    resposta = await get_smart_approval_allowlist("ws-local", _fake_request())
    assert resposta.allowlist[0].label == "Concessão 1"
    assert resposta.allowlist[0].id


@pytest.mark.asyncio
async def test_regra_longa_usa_id_opaco_e_pode_ser_revogada():
    from backend.services.smart_approval import add_to_allowlist

    comando = "x" * 200
    add_to_allowlist("ws-long", "terminal", {"command": comando}, run_id="run-long")
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
async def test_workspace_vazio_vira_400_nao_500(monkeypatch):
    """Erro/borda: `ValueError` do módulo vira HTTP 400 com mensagem clara,
    não um 500 cru que não diz o que corrigir."""
    from fastapi import HTTPException

    body = _grant_request(workspace_id="")
    _install_pending(monkeypatch, body)
    with pytest.raises(HTTPException) as exc_info:
        await add_smart_approval_allowlist(
            body,
            _fake_request(),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "remove"])
@pytest.mark.parametrize("failure", ["write", "commit"])
async def test_persistence_failure_returns_503_and_rolls_back(
    _runtime_settings_isolado,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: str,
) -> None:
    """HTTP failures preserve grants in cache, SQLite and subsequent reloads."""
    from backend.services.smart_approval import add_to_allowlist, get_allowlist
    from backend.workspace.runtime_settings import RuntimeSettings

    settings = _runtime_settings_isolado
    body = _grant_request(workspace_id="ws-failure")
    _install_pending(monkeypatch, body)
    if operation == "remove":
        add_to_allowlist(
            body.workspace_id, body.tool_name, body.args, run_id=body.run_id
        )
    before = get_allowlist(body.workspace_id)
    cached_before = dict(settings._data)
    connection = settings._conn

    class FailingConnection:
        def execute(
            self, sql: str, parameters: tuple[object, ...] = ()
        ) -> sqlite3.Cursor:
            if failure == "write" and sql.startswith("INSERT INTO app_settings"):
                raise sqlite3.OperationalError("simulated write failure")
            return connection.execute(sql, parameters)

        def commit(self) -> None:
            if failure == "commit":
                raise sqlite3.OperationalError("simulated commit failure")
            connection.commit()

        def rollback(self) -> None:
            connection.rollback()

    app = FastAPI()
    app.post("/grants")(add_smart_approval_allowlist)
    app.delete("/grants")(remove_smart_approval_allowlist)
    with monkeypatch.context() as patch:
        patch.setattr(settings, "_conn", FailingConnection())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            if operation == "add":
                response = await client.post("/grants", json=body.model_dump())
            else:
                response = await client.request(
                    "DELETE",
                    "/grants",
                    json={"workspace_id": body.workspace_id, "rule_id": before[0]},
                )
        assert response.status_code == 503
        assert settings._data == cached_before
        assert not connection.in_transaction
        assert get_allowlist(body.workspace_id) == before

    settings.reload()
    assert get_allowlist(body.workspace_id) == before
    other = RuntimeSettings(settings._path)
    try:
        assert other._data == settings._data
    finally:
        other._conn.close()
    # The failed transaction must release its lock and allow a subsequent write.
    settings.update_list("recovery", lambda values: [*values, "ok"])
    settings.reload()
    assert settings.get("recovery") == ["ok"]


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
            _grant_request(workspace_id="privado"),
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

    add_to_allowlist("ws-outro", "terminal", {"command": "git status"}, run_id="run")
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
            _grant_request(workspace_id="ws-outro", args={"command": "pwd"}),
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
                    "ws-concorrente",
                    "terminal",
                    {"command": f"cmd-{index}"},
                    run_id=f"run-{index}",
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
    body = _grant_request(workspace_id="ws-audit", args={"command": "pwd"})
    _install_pending(monkeypatch, body)
    resposta = await add_smart_approval_allowlist(
        body,
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
