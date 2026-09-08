"""Allowlist de servidores MCP por instância e workspace.

As regras são deliberadamente pequenas: identificam apenas o nome estável do
servidor, nunca comando, URL ou credenciais. A ausência de regra preserva a
compatibilidade; uma regra existente com lista vazia bloqueia tudo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

McpPolicyScope = Literal["instance", "workspace"]


class McpPolicyRule(BaseModel):
    scope: McpPolicyScope
    workspace_id: str | None = None
    allowlist: list[str] = Field(default_factory=list)
    version: int = 1
    updated_by: str = ""


class McpPolicyDecision(BaseModel):
    allowed: bool
    code: Literal["allowed", "blocked", "policy_unavailable"]
    scope: McpPolicyScope | None = None
    version: int


_rules: dict[tuple[str, str | None], McpPolicyRule] = {}
_version = 0
_loaded = False


def _policy_file() -> Path:
    return Path.home() / ".vectora" / "mcp" / "policy.json"


def _load() -> None:
    global _loaded, _version
    if _loaded:
        return
    _loaded = True
    path = _policy_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _version = int(raw.get("version", 0))
        for item in raw.get("rules", []):
            rule = McpPolicyRule.model_validate(item)
            key = (rule.scope, rule.workspace_id)
            _rules[key] = rule
    except FileNotFoundError:
        return
    except Exception:
        # Uma política ilegível nunca deve liberar implicitamente servidores.
        _version += 1


def _save() -> None:
    path = _policy_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"version": _version, "rules": [r.model_dump() for r in _rules.values()]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def policy_version() -> int:
    _load()
    return _version


def list_rules() -> list[McpPolicyRule]:
    _load()
    return [rule.model_copy(deep=True) for rule in _rules.values()]


def set_rule(
    scope: McpPolicyScope,
    allowlist: list[str],
    *,
    workspace_id: str | None = None,
    updated_by: str = "",
) -> McpPolicyRule:
    global _version
    _load()
    if scope == "workspace" and not workspace_id:
        raise ValueError("workspace_id obrigatório para regra de workspace")
    if scope == "instance":
        workspace_id = None
    normalized = sorted({name.strip() for name in allowlist if name.strip()})
    _version += 1
    rule = McpPolicyRule(
        scope=scope,
        workspace_id=workspace_id,
        allowlist=normalized,
        version=_version,
        updated_by=updated_by,
    )
    _rules[(scope, workspace_id)] = rule
    _save()
    return rule


def remove_rule(scope: McpPolicyScope, *, workspace_id: str | None = None) -> bool:
    global _version
    _load()
    key = (scope, workspace_id if scope == "workspace" else None)
    if key not in _rules:
        return False
    del _rules[key]
    _version += 1
    _save()
    return True


def evaluate(server_id: str, workspace_id: str | None = None) -> McpPolicyDecision:
    _load()
    workspace_rule = _rules.get(("workspace", workspace_id)) if workspace_id else None
    rule = workspace_rule or _rules.get(("instance", None))
    if rule is None:
        return McpPolicyDecision(allowed=True, code="allowed", version=_version)
    allowed = server_id in rule.allowlist
    return McpPolicyDecision(
        allowed=allowed,
        code="allowed" if allowed else "blocked",
        scope=rule.scope,
        version=_version,
    )


def require_allowed(server_id: str, workspace_id: str | None = None) -> None:
    decision = evaluate(server_id, workspace_id)
    if not decision.allowed:
        raise PermissionError("servidor MCP bloqueado pela política")


def _reset_for_tests() -> None:
    global _loaded, _version
    _rules.clear()
    _version = 0
    _loaded = False


__all__ = [
    "McpPolicyDecision",
    "McpPolicyRule",
    "evaluate",
    "list_rules",
    "policy_version",
    "remove_rule",
    "require_allowed",
    "set_rule",
]
