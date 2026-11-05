"""Tests for backend/services/agent_factory.py — contexto de sessão.

Cobre `_load_workspaces_overview`: o Vectora precisa ter conhecimento proativo
dos seus workspaces (injetado no system prompt).
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.services import agent_factory as orchestrator


def _ws(ws_id: str, name: str, cwd: str, *, git: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=ws_id, name=name, cwd=cwd, is_git_repo=git)


def test_overview_lists_workspaces_and_marks_active(monkeypatch):
    """Lista os workspaces e marca o ativo com ◀; flag git aparece."""
    from backend.workspace import workspace as ws_mod

    monkeypatch.setattr(
        ws_mod.workspace_registry,
        "list_all",
        lambda: [
            _ws("aaa111", "proj-a", "/home/u/proj-a", git=True),
            _ws("bbb222", "proj-b", "/home/u/proj-b"),
        ],
    )

    out = orchestrator._load_workspaces_overview(active_id="bbb222")
    assert out is not None
    assert "## Seus Workspaces" in out
    assert "proj-a" in out and "aaa111" in out
    assert "proj-b" in out and "bbb222" in out
    # Ativo marcado, não-ativo sem marcador.
    assert "proj-b" in out
    assert "◀ ativo" in out
    # A linha do proj-b (ativo) tem o marcador; a do proj-a não.
    line_b = next(ln for ln in out.splitlines() if "bbb222" in ln)
    line_a = next(ln for ln in out.splitlines() if "aaa111" in ln)
    assert "◀ ativo" in line_b
    assert "◀ ativo" not in line_a
    # git flag só no repo git.
    assert "· git" in line_a
    assert "· git" not in line_b


def test_overview_returns_none_when_empty(monkeypatch):
    """Erro/borda: sem workspaces registrados → None (nada a injetar)."""
    from backend.workspace import workspace as ws_mod

    monkeypatch.setattr(ws_mod.workspace_registry, "list_all", list)
    assert orchestrator._load_workspaces_overview() is None


def test_overview_returns_none_on_registry_error(monkeypatch):
    """Erro/borda: falha no registry degrada para None, nunca propaga."""
    from backend.workspace import workspace as ws_mod

    def _boom() -> list:
        raise RuntimeError("registry indisponível")

    monkeypatch.setattr(ws_mod.workspace_registry, "list_all", _boom)
    assert orchestrator._load_workspaces_overview(active_id="x") is None


def test_overview_truncates_above_30(monkeypatch):
    """Acima de 30 workspaces, trunca e aponta para workspace_list."""
    from backend.workspace import workspace as ws_mod

    many = [_ws(f"id{i:03d}", f"w{i}", f"/p/{i}") for i in range(35)]
    monkeypatch.setattr(ws_mod.workspace_registry, "list_all", lambda: many)

    out = orchestrator._load_workspaces_overview()
    assert out is not None
    assert "e mais 5" in out
    assert "workspace_list" in out
