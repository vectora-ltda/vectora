"""Aprovação das regras de hook presa ao digest do ``vectora.toml``."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.requests import Request

from backend.api.handlers.workspaces import _to_info
from backend.workspace.workspace import WorkspaceRegistry

RULE = '[[hooks.rules]]\nid = "guarda"\nevent = "pre_tool"\ncommand = ["true"]\n'


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry._loaded = True
    monkeypatch.setattr(registry, "_save", lambda: None)
    return registry


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _workspace(registry: WorkspaceRegistry, tmp_path: Path, toml: str):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "vectora.toml").write_text(toml, encoding="utf-8")
    return registry.create(str(project), trust=True)


def test_aprovar_grava_o_digest_das_regras(
    registry: WorkspaceRegistry, tmp_path: Path
) -> None:
    ws = _workspace(registry, tmp_path, RULE)
    assert _to_info(ws).hooks_rules_pending is True
    registry.approve_hooks(ws.id)
    assert ws.hooks_rules_digest
    info = _to_info(ws)
    assert info.hooks_approved is True
    assert info.hooks_rules_pending is False


def test_regra_alterada_volta_a_ficar_pendente(
    registry: WorkspaceRegistry, tmp_path: Path
) -> None:
    ws = _workspace(registry, tmp_path, RULE)
    registry.approve_hooks(ws.id)
    (Path(ws.cwd) / "vectora.toml").write_text(
        RULE.replace('["true"]', '["curl", "x"]'), encoding="utf-8"
    )
    assert _to_info(ws).hooks_rules_pending is True
    registry.approve_hooks(ws.id)
    assert _to_info(ws).hooks_rules_pending is False


def test_post_file_write_tambem_exige_aprovacao_e_a_alteracao_a_invalida(
    registry: WorkspaceRegistry, tmp_path: Path
) -> None:
    ws = _workspace(registry, tmp_path, '[hooks]\npost_file_write = ["true"]\n')
    assert _to_info(ws).hooks_rules_pending is True
    registry.approve_hooks(ws.id)
    assert ws.hooks_rules_digest
    assert _to_info(ws).hooks_rules_pending is False


def test_workspace_sem_nenhum_hook_nao_fica_pendente(
    registry: WorkspaceRegistry, tmp_path: Path
) -> None:
    ws = _workspace(registry, tmp_path, "[hooks]\n")
    assert _to_info(ws).hooks_rules_pending is False
    registry.approve_hooks(ws.id)
    assert ws.hooks_rules_digest is None


async def test_endpoint_lista_as_regras_e_o_estado_da_aprovacao(
    registry: WorkspaceRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from backend.api.handlers import workspaces as handlers

    monkeypatch.setattr("backend.workspace.workspace.workspace_registry", registry)
    monkeypatch.setattr(handlers, "require_workspace_access", lambda *_a, **_k: None)
    request = _request()
    ws = _workspace(registry, tmp_path, RULE)

    pending = await handlers.get_workspace_hooks(request, ws.id)
    assert pending.approval == "pending"
    assert [(rule.id, rule.event, rule.mode) for rule in pending.rules] == [
        ("guarda", "pre_tool", "blocking")
    ]
    assert pending.rules[0].command == ["true"]

    registry.approve_hooks(ws.id)
    approved = await handlers.get_workspace_hooks(request, ws.id)
    assert approved.approval == "approved"

    with pytest.raises(HTTPException) as missing:
        await handlers.get_workspace_hooks(request, "nao-existe")
    assert missing.value.status_code == 404


async def test_endpoint_sem_regras_responde_no_rules(
    registry: WorkspaceRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import workspaces as handlers

    monkeypatch.setattr("backend.workspace.workspace.workspace_registry", registry)
    monkeypatch.setattr(handlers, "require_workspace_access", lambda *_a, **_k: None)
    ws = _workspace(registry, tmp_path, "[hooks]\n")
    response = await handlers.get_workspace_hooks(
        _request(),
        ws.id,
    )
    assert response.approval == "no_rules"
    assert response.rules == []
