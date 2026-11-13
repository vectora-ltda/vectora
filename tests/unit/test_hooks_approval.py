"""Aprovação das regras de hook presa ao digest do ``vectora.toml``."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.api.handlers.workspaces import _to_info
from backend.workspace.workspace import WorkspaceRegistry

RULE = '[[hooks.rules]]\nid = "guarda"\nevent = "pre_tool"\ncommand = ["true"]\n'


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry._loaded = True
    monkeypatch.setattr(registry, "_save", lambda: None)
    return registry


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


def test_sem_regras_nada_fica_pendente(
    registry: WorkspaceRegistry, tmp_path: Path
) -> None:
    ws = _workspace(registry, tmp_path, '[hooks]\npost_file_write = ["true"]\n')
    assert _to_info(ws).hooks_rules_pending is False
    registry.approve_hooks(ws.id)
    assert ws.hooks_rules_digest is None
