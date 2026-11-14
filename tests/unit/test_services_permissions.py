"""Testes unitários para src/services/permissions.py.

Cobre:
- role_level: hierarquia correta
- has_min_role: todos os roles vs todos os mínimos
- require_min_role: levanta HTTPException 403 quando insuficiente
- can_access_thread: root/admin acessa tudo; member apenas própria
- can_delete_thread: mesma lógica de acesso
- can_run_terminal: member só no próprio workspace; viewer nunca
- can_read_audit: apenas admin/root
- can_manage_users: apenas root
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helper para criar user mock
# ---------------------------------------------------------------------------


def make_user(role: str, uid: str = "u-1"):
    class _User:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_

    return _User(uid, role)


# ---------------------------------------------------------------------------
# role_level
# ---------------------------------------------------------------------------


class TestRoleLevel:
    def test_hierarchy_order(self):
        from backend.rbac.permissions import role_level

        assert role_level("viewer") < role_level("member")
        assert role_level("member") < role_level("admin")
        assert role_level("admin") < role_level("root")

    def test_unknown_role_returns_negative(self):
        from backend.rbac.permissions import role_level

        assert role_level("unknown") < 0


# ---------------------------------------------------------------------------
# has_min_role
# ---------------------------------------------------------------------------


class TestHasMinRole:
    @pytest.mark.parametrize(
        ("user_role", "min_role", "expected"),
        [
            ("root", "root", True),
            ("root", "admin", True),
            ("root", "member", True),
            ("root", "viewer", True),
            ("admin", "root", False),
            ("admin", "admin", True),
            ("admin", "member", True),
            ("member", "admin", False),
            ("member", "member", True),
            ("member", "viewer", True),
            ("viewer", "member", False),
            ("viewer", "viewer", True),
        ],
    )
    def test_role_comparisons(self, user_role, min_role, expected):
        from backend.rbac.permissions import has_min_role

        user = make_user(user_role)
        assert has_min_role(user, min_role) == expected

    def test_none_user_returns_false(self):
        from backend.rbac.permissions import has_min_role

        assert has_min_role(None, "viewer") is False


# ---------------------------------------------------------------------------
# require_min_role
# ---------------------------------------------------------------------------


class TestRequireMinRole:
    def test_sufficient_role_does_not_raise(self):
        from backend.rbac.permissions import require_min_role

        user = make_user("admin")
        require_min_role(user, "member")  # não deve levantar

    def test_insufficient_role_raises_403(self):
        from backend.rbac.permissions import require_min_role

        user = make_user("member")
        with pytest.raises(HTTPException) as exc_info:
            require_min_role(user, "admin")
        assert exc_info.value.status_code == 403

    def test_none_user_raises_403(self):
        from backend.rbac.permissions import require_min_role

        with pytest.raises(HTTPException) as exc_info:
            require_min_role(None, "viewer")
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# can_access_thread
# ---------------------------------------------------------------------------


class TestCanAccessThread:
    def test_root_accesses_any_thread(self):
        from backend.rbac.permissions import can_access_thread

        root = make_user("root", "root-id")
        assert can_access_thread(root, "other-user-id") is True

    def test_admin_accesses_any_thread(self):
        from backend.rbac.permissions import can_access_thread

        admin = make_user("admin", "admin-id")
        assert can_access_thread(admin, "other-user-id") is True

    def test_member_accesses_own_thread(self):
        from backend.rbac.permissions import can_access_thread

        member = make_user("member", "member-id")
        assert can_access_thread(member, "member-id") is True

    def test_member_cannot_access_other_thread(self):
        from backend.rbac.permissions import can_access_thread

        member = make_user("member", "member-id")
        assert can_access_thread(member, "other-id") is False

    def test_member_accesses_unowned_thread(self):
        from backend.rbac.permissions import can_access_thread

        member = make_user("member", "member-id")
        assert can_access_thread(member, None) is True

    def test_none_user_returns_false(self):
        from backend.rbac.permissions import can_access_thread

        assert can_access_thread(None, "any-id") is False


# ---------------------------------------------------------------------------
# can_delete_thread
# ---------------------------------------------------------------------------


class TestCanDeleteThread:
    def test_admin_deletes_any(self):
        from backend.rbac.permissions import can_delete_thread

        admin = make_user("admin", "a")
        assert can_delete_thread(admin, "other") is True

    def test_member_deletes_own(self):
        from backend.rbac.permissions import can_delete_thread

        member = make_user("member", "m")
        assert can_delete_thread(member, "m") is True

    def test_member_cannot_delete_other(self):
        from backend.rbac.permissions import can_delete_thread

        member = make_user("member", "m")
        assert can_delete_thread(member, "other") is False


# ---------------------------------------------------------------------------
# can_run_terminal
# ---------------------------------------------------------------------------


class TestCanRunTerminal:
    def test_root_runs_anywhere(self):
        from backend.rbac.permissions import can_run_terminal

        root = make_user("root", "r")
        assert can_run_terminal(root, "any-workspace") is True

    def test_admin_runs_anywhere(self):
        from backend.rbac.permissions import can_run_terminal

        admin = make_user("admin", "a")
        assert can_run_terminal(admin, "other-workspace") is True

    def test_member_runs_own_workspace(self):
        from backend.rbac.permissions import can_run_terminal

        member = make_user("member", "m")
        assert can_run_terminal(member, "m") is True

    def test_member_cannot_run_other_workspace(self):
        from backend.rbac.permissions import can_run_terminal

        member = make_user("member", "m")
        assert can_run_terminal(member, "other") is False

    def test_viewer_never_runs_terminal(self):
        from backend.rbac.permissions import can_run_terminal

        viewer = make_user("viewer", "v")
        assert can_run_terminal(viewer, "v") is False
        assert can_run_terminal(viewer, "other") is False

    def test_none_user_returns_false(self):
        from backend.rbac.permissions import can_run_terminal

        assert can_run_terminal(None, "ws") is False


# ---------------------------------------------------------------------------
# can_read_audit / can_manage_users
# ---------------------------------------------------------------------------


class TestAuditAndUserManagement:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("root", True),
            ("admin", True),
            ("member", False),
            ("viewer", False),
        ],
    )
    def test_can_read_audit(self, role, expected):
        from backend.rbac.permissions import can_read_audit

        user = make_user(role)
        assert can_read_audit(user) == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("root", True),
            ("admin", False),
            ("member", False),
            ("viewer", False),
        ],
    )
    def test_can_manage_users(self, role, expected):
        from backend.rbac.permissions import can_manage_users

        user = make_user(role)
        assert can_manage_users(user) == expected
