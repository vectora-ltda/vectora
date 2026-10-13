"""Registry de sessões PTY ativas.

Singleton ``pty_registry`` keyed por ``terminal_id``. A unicidade do id é
responsabilidade do criador (handler WS gera UUID curto). Múltiplas sessões
por thread/workspace são permitidas (cada split é uma sessão própria).
"""

from __future__ import annotations

import logging

from backend.services.pty_session import PtySession

logger = logging.getLogger(__name__)


class PtyRegistry:
    """Mantém as sessões PTY vivas em memória do processo."""

    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._closed: dict[str, tuple[str, str, str]] = {}
        self._attachment_order: dict[str, int] = {}
        self._next_attachment_order = 0

    def _mark_attached(self, terminal_id: str) -> None:
        self._next_attachment_order += 1
        self._attachment_order[terminal_id] = self._next_attachment_order

    def add(self, session: PtySession) -> None:
        self._closed.pop(session.terminal_id, None)
        self._sessions[session.terminal_id] = session
        self._mark_attached(session.terminal_id)

    def get(self, terminal_id: str) -> PtySession | None:
        return self._sessions.get(terminal_id)

    def list_for_thread(self, thread_id: str) -> list[PtySession]:
        return [s for s in self._sessions.values() if s.thread_id == thread_id]

    def resolve_for_context(
        self, terminal_id: str, *, user_id: str, thread_id: str, workspace_id: str
    ) -> PtySession | None:
        """Resolve only a terminal owned by the exact execution context."""
        if not terminal_id or not thread_id or not workspace_id:
            return None
        session = self._sessions.get(terminal_id)
        if session is None:
            return None
        if (
            getattr(session, "user_id", "local") != user_id
            or session.thread_id != thread_id
            or session.workspace_id != workspace_id
        ):
            return None
        return session

    def resolve_for_user_workspace(
        self, terminal_id: str, *, user_id: str, workspace_id: str
    ) -> PtySession | None:
        """Resolve a live PTY shared by the user across agent/UI threads.

        A terminal is owned by the user and workspace, while ``thread_id`` is
        only the currently attached conversation.  Keeping those concerns
        separate lets the UI reconnect to a PTY opened by the agent (and lets
        the agent continue using one opened by the user) without weakening
        cross-user/workspace isolation.
        """
        if not terminal_id or not workspace_id:
            return None
        session = self._sessions.get(terminal_id)
        if session is None:
            return None
        if (
            getattr(session, "user_id", "local") != user_id
            or session.workspace_id != workspace_id
        ):
            return None
        return session

    def list_for_context(
        self, *, user_id: str, thread_id: str, workspace_id: str
    ) -> list[PtySession]:
        if not thread_id or not workspace_id:
            return []
        return [
            s
            for s in self._sessions.values()
            if (
                getattr(s, "user_id", "local") == user_id
                and s.thread_id == thread_id
                and s.workspace_id == workspace_id
            )
        ]

    def list_for_user_workspace(
        self, *, user_id: str, workspace_id: str
    ) -> list[PtySession]:
        """Lista sessões do usuário no workspace, independente da thread."""
        if not workspace_id:
            return []
        sessions = [
            s
            for s in self._sessions.values()
            if getattr(s, "user_id", "local") == user_id
            and s.workspace_id == workspace_id
            and bool(getattr(s, "is_alive", lambda: True)())
        ]
        return sorted(
            sessions,
            key=lambda session: self._attachment_order.get(session.terminal_id, 0),
            reverse=True,
        )

    def claim_for_context(
        self,
        terminal_id: str,
        *,
        user_id: str,
        workspace_id: str,
        thread_id: str,
    ) -> PtySession | None:
        """Vincula uma sessão aberta pela UI ao turno atual do agente.

        A identidade do usuário e o workspace nunca mudam durante o claim;
        somente o ``thread_id`` é atualizado, permitindo que a tool assuma o
        terminal que o usuário abriu em outra aba/thread.
        """
        if not terminal_id or not workspace_id or not thread_id:
            return None
        session = self._sessions.get(terminal_id)
        if session is None or not session.is_alive():
            return None
        if (
            getattr(session, "user_id", "local") != user_id
            or session.workspace_id != workspace_id
        ):
            return None
        session.thread_id = thread_id
        self._mark_attached(session.terminal_id)
        return session

    def close(self, terminal_id: str) -> bool:
        session = self._sessions.pop(terminal_id, None)
        if session is None:
            return False
        self._closed[terminal_id] = (
            getattr(session, "user_id", "local"),
            session.thread_id,
            session.workspace_id,
        )
        self._attachment_order.pop(terminal_id, None)
        session.close()
        return True

    def was_closed_for_context(
        self, terminal_id: str, *, user_id: str, thread_id: str, workspace_id: str
    ) -> bool:
        """Indica se um terminal fechado pertencia ao contexto informado."""
        return self._closed.get(terminal_id) == (user_id, thread_id, workspace_id)

    def close_all(self) -> None:
        """Encerra todas as PTYs — chamado no shutdown do servidor."""
        for sid in list(self._sessions.keys()):
            try:
                self._sessions[sid].close()
            except Exception:
                logger.debug("pty_registry: erro ao encerrar %s", sid)
        self._sessions.clear()
        self._attachment_order.clear()


#: Instância global usada pelo handler WS e pelo lifespan do servidor.
pty_registry: PtyRegistry = PtyRegistry()
