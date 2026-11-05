"""Identidade, RBAC e política de acesso — "quem pode o quê".

``auth.py`` (usuários, roles, JWT, hashing), ``permissions.py`` (hierarquia
RBAC + regras de filesystem), ``tool_policy.py`` (allowlist de tools por
usuário), ``subscription.py`` (gate de feature por plano) e
``safe_roots.py`` (raízes de filesystem permitidas por usuário).

Lazy imports (``__getattr__``) — mesmo padrão de ``backend.mcp`` — evitam
puxar bcrypt/PyJWT no import do pacote quando só um submódulo específico é
necessário.
"""

from __future__ import annotations

__all__ = [
    "User",
    "require_min_role",
    "require_pro",
]


def __getattr__(name: str) -> object:
    if name == "User":
        from backend.rbac.auth import User

        return User
    if name == "require_min_role":
        from backend.rbac.permissions import require_min_role

        return require_min_role
    if name == "require_pro":
        from backend.rbac.subscription import require_pro

        return require_pro
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
