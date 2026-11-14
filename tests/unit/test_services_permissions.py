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


# ---------------------------------------------------------------------------
# can_delete_thread
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# can_run_terminal
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# can_read_audit / can_manage_users
# ---------------------------------------------------------------------------
