"""Comandos ``vectora hooks``: lista as regras do workspace e testa uma delas.

``test`` é um dry-run: executa o comando da regra com um contexto sintético e
mostra a decisão, sem afetar nenhum run. Quem o roda está na própria máquina e
escolheu a regra, então ele não exige a aprovação do workspace; o que a
aprovação protege é a execução automática durante o agente.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Protocol

from backend.engine import hooks as engine_hooks
from backend.tools.context import ToolContext
from backend.workspace.workspace_config import (
    HookRule,
    hooks_approval_valid,
    load_workspace_config,
)


class HooksArgs(Protocol):
    action: str
    workspace: str
    output: str
    rule_id: str
    tool: str
    args: str
    response: str
    result: str


def _result(
    status: str, data: object = None, error: str | None = None
) -> dict[str, object]:
    value: dict[str, object] = {"schema_version": "1", "status": status, "data": data}
    if error is not None:
        value["error"] = error
    return value


def _print(value: dict[str, object], output: str) -> int:
    if output == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    elif value["status"] == "error":
        print(f"Erro: {value.get('error', 'falha')}", file=sys.stderr)
    else:
        data = value.get("data")
        print(
            data
            if isinstance(data, str)
            else json.dumps(data, ensure_ascii=False, indent=2)
        )
    return 0 if value["status"] == "ok" else 1


def _approval_state(root: Path) -> str:
    """``approved``, ``pending`` ou ``unregistered`` para as regras do workspace."""
    from backend.workspace.workspace import workspace_registry

    resolved = root.resolve()
    for workspace in workspace_registry.list_all():
        if Path(workspace.cwd).resolve() != resolved:
            continue
        config = load_workspace_config(root)
        if config is None:
            return "no_rules"
        valid = hooks_approval_valid(
            config.hooks,
            approved=bool(workspace.hooks_approved),
            approved_digest=workspace.hooks_rules_digest,
        )
        return "approved" if valid else "pending"
    return "unregistered"


def _rules_of(root: Path) -> list[HookRule]:
    config = load_workspace_config(root)
    return list(config.hooks.rules) if config is not None else []


def _list(root: Path) -> dict[str, object]:
    rules = _rules_of(root)
    return _result(
        "ok",
        {
            "workspace": str(root),
            "approval": _approval_state(root) if rules else "no_rules",
            "rules": [
                {
                    "id": rule.id,
                    "event": rule.event,
                    "mode": rule.mode,
                    "tools": rule.tools,
                    "timeout_s": rule.timeout_s,
                    "command": rule.command,
                }
                for rule in rules
            ],
        },
    )


def _synthetic_arguments(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _test(root: Path, args: HooksArgs) -> dict[str, object]:
    rule = next((item for item in _rules_of(root) if item.id == args.rule_id), None)
    if rule is None:
        return _result("error", error=f"regra não encontrada: {args.rule_id}")
    tool_args = _synthetic_arguments(args.args)
    if tool_args is None:
        return _result("error", error="--args deve ser um objeto JSON")
    from backend.engine.tool_batch import _redact_secrets

    is_response = rule.event == "pre_response"
    outcome = asyncio.run(
        engine_hooks.run_hook(
            rule,
            ctx=ToolContext(user_id="local", thread_id="hooks-test"),
            tool_name="" if is_response else args.tool,
            tool_args={} if is_response else tool_args,
            result=args.response
            if is_response
            else (args.result if rule.event == "post_tool" else None),
            root=root,
            approved=True,
            sandboxed=False,
            workspace_id=str(root),
            redact=_redact_secrets,
        )
    )
    data = outcome.evidence()
    data.pop("type", None)
    return _result("ok", data)


def run_hooks(args: HooksArgs) -> None:
    """Executa ``vectora hooks list|test`` e encerra com o código de saída."""
    root = Path(args.workspace or ".").resolve()
    value = _list(root) if args.action == "list" else _test(root, args)
    raise SystemExit(_print(value, args.output))
