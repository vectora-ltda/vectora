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


def test_list_for_thread_filters():
    reg = PtyRegistry()
    reg.add(_FakeSession("a", thread_id="t1"))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    reg.add(_FakeSession("b", thread_id="t1"))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    reg.add(_FakeSession("c", thread_id="t2"))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert {s.terminal_id for s in reg.list_for_thread("t1")} == {"a", "b"}


def test_resolve_for_context_requires_thread_and_workspace():
    reg = PtyRegistry()
    session = _FakeSession("a", thread_id="t1", workspace_id="ws1")
    reg.add(session)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert (
        reg.resolve_for_context("a", user_id="u1", thread_id="t1", workspace_id="ws1")
        is session
    )
    assert (
        reg.resolve_for_context("a", user_id="u1", thread_id="t2", workspace_id="ws1")
        is None
    )
    assert (
        reg.resolve_for_context("a", user_id="u1", thread_id="t1", workspace_id="ws2")
        is None
    )
    assert (
        reg.resolve_for_context("a", user_id="u1", thread_id="", workspace_id="ws1")
        is None
    )


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


def test_user_isolation_and_closed_state() -> None:
    reg = PtyRegistry()
    session = _FakeSession("a", user_id="owner")
    reg.add(session)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert (
        reg.resolve_for_context(
            "a", user_id="other", thread_id="t1", workspace_id="ws1"
        )
        is None
    )
    assert reg.close("a") is True
    assert reg.was_closed_for_context(
        "a", user_id="owner", thread_id="t1", workspace_id="ws1"
    )
    assert not reg.was_closed_for_context(
        "a", user_id="other", thread_id="t1", workspace_id="ws1"
    )
