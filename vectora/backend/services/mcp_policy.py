"""Allowlist de servidores MCP por instância e workspace.

As regras são deliberadamente pequenas: identificam apenas o nome estável do
servidor, nunca comando, URL ou credenciais. A ausência de regra preserva a
compatibilidade; uma regra existente com lista vazia bloqueia tudo.
"""

from __future__ import annotations

import json
import uuid
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
_policy_error = False
_loaded_mtime_ns: int | None = None
_origin = uuid.uuid4().hex


def _policy_file() -> Path:
    return Path.home() / ".vectora" / "mcp" / "policy.json"


def _load() -> None:
    global _loaded, _version, _policy_error, _loaded_mtime_ns, _origin
    path = _policy_file()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        mtime_ns = None
    if _loaded and not _policy_error and mtime_ns == _loaded_mtime_ns:
        return
    _rules.clear()
    _policy_error = False
    _loaded = True
    _loaded_mtime_ns = mtime_ns
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _version = int(raw.get("version", 0))
        _origin = str(raw.get("origin", _origin))
        for item in raw.get("rules", []):
            rule = McpPolicyRule.model_validate(item)
            key = (rule.scope, rule.workspace_id)
            _rules[key] = rule
    except FileNotFoundError:
        return
    except Exception:
        # Uma política ilegível nunca deve liberar implicitamente servidores.
        _version += 1
        _policy_error = True
        _loaded_mtime_ns = None


def _save() -> None:
    path = _policy_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "version": _version,
                "origin": _origin,
                "rules": [r.model_dump() for r in _rules.values()],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def _publish_change() -> None:
    """Publica a versão e o snapshot validado para as demais réplicas."""
    import json as _json

    from backend.persistence.kv import publish_soon

    publish_soon(
        "vectora:mcp-policy",
        _json.dumps(
            {
                "version": _version,
                "origin": _origin,
                "rules": [rule.model_dump(mode="json") for rule in _rules.values()],
            }
        ),
    )


def apply_remote_version(
    version: int,
    rules: list[dict[str, object]] | None = None,
    origin: str | None = None,
) -> None:
    """Aplica um snapshot remoto antes de invalidar os caches dependentes."""
    global _loaded, _loaded_mtime_ns, _policy_error, _version, _origin
    if _loaded and (
        version < _version or (version == _version and (origin or "") <= _origin)
    ):
        return
    if rules is not None:
        try:
            remote_rules = [McpPolicyRule.model_validate(item) for item in rules]
            _rules.clear()
            _rules.update(
                {(rule.scope, rule.workspace_id): rule for rule in remote_rules}
            )
            _policy_error = False
            _loaded = True
            try:
                _loaded_mtime_ns = _policy_file().stat().st_mtime_ns
            except FileNotFoundError:
                _loaded_mtime_ns = None
            _version = version
            if origin:
                _origin = origin
        except Exception:
            _rules.clear()
            _policy_error = True
            _loaded = True
            _loaded_mtime_ns = None
            _version = version
    else:
        _loaded = False
        _loaded_mtime_ns = None
        _policy_error = False
        _version = max(_version, version)
    from backend.workspace import plugins

    plugins.invalidate_mcp_cache()


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
    _publish_change()
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
    _publish_change()
    return True


def evaluate(server_id: str, workspace_id: str | None = None) -> McpPolicyDecision:
    _load()
    if _policy_error:
        return McpPolicyDecision(
            allowed=False,
            code="policy_unavailable",
            version=_version,
        )
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
    global _loaded, _version, _policy_error, _loaded_mtime_ns, _origin
    _rules.clear()
    _version = 0
    _loaded = False
    _policy_error = False
    _loaded_mtime_ns = None
    _origin = uuid.uuid4().hex


__all__ = [
    "McpPolicyDecision",
    "McpPolicyRule",
    "McpPolicyScope",
    "apply_remote_version",
    "evaluate",
    "list_rules",
    "policy_version",
    "remove_rule",
    "require_allowed",
    "set_rule",
]
