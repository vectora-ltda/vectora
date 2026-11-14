"""Tests para src/services/pty_registry.py — registry de PTYs ativas (T1).

Testa o comportamento do registry com sessões fake, evitando dependência do
backend de PTY (que requer pywinpty/ptyprocess + shell real).
"""

from __future__ import annotations

from backend.services.pty_registry import PtyRegistry


class _FakeSession:
    def __init__(
        self,
        terminal_id: str,
        thread_id: str = "t1",
        workspace_id: str = "ws1",
        user_id: str = "u1",
    ) -> None:
        self.terminal_id = terminal_id
        self.thread_id = thread_id
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_add_and_get():
    reg = PtyRegistry()
    s = _FakeSession("a")
    reg.add(s)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert reg.get("a") is s
    assert reg.get("nao") is None


def test_close_pops_and_closes():
    reg = PtyRegistry()
    s = _FakeSession("a")
    reg.add(s)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert reg.close("a") is True
    assert s.closed is True
    assert reg.get("a") is None


def test_close_unknown_returns_false():
    reg = PtyRegistry()
    assert reg.close("nao") is False


def test_list_for_user_workspace_prioriza_anexo_mais_recente() -> None:
    reg = PtyRegistry()
    older = _FakeSession("older", thread_id="shared")
    newer = _FakeSession("newer", thread_id="shared")
    reg.add(older)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    reg.add(newer)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    sessions = reg.list_for_user_workspace(user_id="u1", workspace_id="ws1")

    assert [session.terminal_id for session in sessions] == ["newer", "older"]


def test_close_all_clears():
    reg = PtyRegistry()
    a, b = _FakeSession("a"), _FakeSession("b")
    reg.add(a)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    reg.add(b)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    reg.close_all()
    assert a.closed
    assert b.closed
    assert reg.get("a") is None
    assert reg.get("b") is None
