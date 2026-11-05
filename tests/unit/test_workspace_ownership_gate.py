"""Testes do gate de ownership em `workspace_scoped_router`
(`backend/api/handlers/workspaces.py`).

Diferente de `test_workspaces_view.py` (chama handlers como função Python
direta, passando por cima do pipeline de `Depends()` do FastAPI), estes
testes sobem o app real via `TestClient` — só assim o dependency
`_enforce_workspace_ownership` é exercitado de verdade.

Autenticação é simplificada via um header de teste (`X-Test-User-Id`/
`X-Test-User-Role`) que substitui `_extract_user` — o fluxo real de
signup/JWT já tem cobertura própria em `test_api_middleware_auth.py`;
aqui o que importa é `request.state.user` carregar o `id`/`role` certos
pro `can_access_workspace` decidir.

Verifica:
- dono do workspace acessa uma rota `/{workspace_id}/...` normalmente
- outro usuário autenticado recebe 403 sem o handler rodar
- workspace sem `owner_id` (legado/desktop) continua livre pra qualquer
  autenticado — não regride o comportamento single-user de hoje
- workspace inexistente continua com o 404 específico do handler (não
  interceptado pela dependency, que só barra 403 de dono errado)
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def gate_client(monkeypatch):
    """App com auth habilitada; `_extract_user` lê `X-Test-User-*` em vez
    de decodificar JWT — foca o teste no gate de ownership, não no
    subsistema de auth (já coberto em test_api_middleware_auth.py)."""
    os.environ["VECTORA_AUTH_REQUIRED"] = "true"

    import backend.api.middleware.auth as auth_mw

    async def _fake_extract_user(request):
        uid = request.headers.get("X-Test-User-Id")
        if not uid:
            return None
        from backend.rbac.auth import User

        return User(
            id=uid,
            username=uid,
            email=f"{uid}@test.com",
            role=request.headers.get("X-Test-User-Role", "member"),
            name=uid,
            created_at="2024-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(auth_mw, "_extract_user", _fake_extract_user)

    from fastapi.testclient import TestClient

    from backend.api.server import create_app

    app = create_app(serve_static=False)
    client = TestClient(app, raise_server_exceptions=False)
    yield client

    os.environ["VECTORA_AUTH_REQUIRED"] = "false"


def _headers(user_id: str, role: str = "member") -> dict[str, str]:
    return {"X-Test-User-Id": user_id, "X-Test-User-Role": role}


def _register_workspace(monkeypatch, workspace_id: str, owner_id: str | None):
    from backend.vtypes import Workspace
    from backend.workspace import workspace as ws_mod

    ws = Workspace(
        id=workspace_id,
        name=workspace_id,
        cwd=f"/tmp/{workspace_id}",
        created_at="2024-01-01T00:00:00+00:00",
        trusted=True,
        owner_id=owner_id,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry,
        "get",
        lambda wid: ws if wid == workspace_id else None,
    )
    return ws


class TestWorkspaceOwnershipGate:
    def test_dono_acessa_normalmente(self, gate_client, monkeypatch):
        _register_workspace(monkeypatch, "ws-owned", "owner-1")

        r = gate_client.get("/workspaces/ws-owned/tree", headers=_headers("owner-1"))

        assert r.status_code == 200
        assert "entries" in r.json()

    def test_outro_usuario_recebe_403_sem_handler_rodar(self, gate_client, monkeypatch):
        _register_workspace(monkeypatch, "ws-alheio", "owner-2")

        r = gate_client.get("/workspaces/ws-alheio/tree", headers=_headers("intruso-1"))

        assert r.status_code == 403
        # A dependency intercepta antes do handler — resposta é o erro
        # genérico do gate, não o TreeResponse do handler.
        assert "entries" not in r.json()

    def test_workspace_sem_owner_id_continua_livre(self, gate_client, monkeypatch):
        """Regressão: workspace legado/desktop (sem owner_id) não pode
        passar a exigir dono depois desta migração — mesmo comportamento
        de hoje, qualquer autenticado acessa."""
        _register_workspace(monkeypatch, "ws-legado", None)

        r = gate_client.get(
            "/workspaces/ws-legado/tree", headers=_headers("qualquer-1")
        )

        assert r.status_code == 200

    def test_workspace_inexistente_mantem_404_do_handler(
        self, gate_client, monkeypatch
    ):
        """A dependency só barra 403 de dono errado — 404 de workspace
        inexistente continua sendo decisão de cada handler (aqui,
        `workspace_sandbox_init`, que já levanta 404 explícito)."""
        from backend.workspace import workspace as ws_mod

        monkeypatch.setattr(ws_mod.workspace_registry, "get", lambda wid: None)

        r = gate_client.post(
            "/workspaces/nao-existe/sandbox/init", headers=_headers("qualquer-2")
        )

        assert r.status_code == 404

    def test_admin_acessa_qualquer_workspace(self, gate_client, monkeypatch):
        """root/admin sempre têm acesso, mesmo sem ser dono — mesmo
        comportamento de `can_access_workspace`/`_is_privileged` já
        vigente antes desta migração."""
        _register_workspace(monkeypatch, "ws-de-outro", "owner-3")

        r = gate_client.get(
            "/workspaces/ws-de-outro/tree", headers=_headers("admin-1", role="root")
        )

        assert r.status_code == 200
