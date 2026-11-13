"""Hooks determinísticos do harness: ``pre_tool`` e ``post_tool``.

As regras ficam em ``[[hooks.rules]]`` do ``vectora.toml`` do workspace
(``backend/workspace/workspace_config.py``). Cada hook é um comando (lista de
argumentos, sem shell) que recebe o contexto da chamada como JSON na entrada
padrão e responde ``{"decision": "allow" | "deny", "reason": "..."}``.

Garantias:

- **Aprovação.** Comando vindo de ``vectora.toml`` pode ter vindo de um
  repositório clonado; só roda depois da aprovação do workspace
  (``workspace_registry.approve_hooks``). Sem ela o hook é registrado como
  ``skipped_not_approved`` e não executa.
- **Falha fechada.** Um hook ``blocking`` que nega, estoura o timeout, falha
  ou responde algo inválido impede a ação. Um hook ``informative`` nunca
  impede: o resultado só é registrado.
- **Isolamento.** O comando roda com um ambiente mínimo (sem as variáveis do
  backend, onde ficam credenciais), no diretório do workspace e, quando o
  sandbox do workspace está ativo, dentro do mesmo worker isolado.
- **Sem segredos no hook.** Argumentos e resultado passam por ``redact``
  antes de serem entregues; o resultado vai truncado.
- **Auditoria.** Cada execução devolve um ``HookOutcome`` sem o conteúdo da
  chamada; o chamador o registra como evidência do run.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from backend.tools.context import ToolContext
    from backend.workspace.workspace_config import HookRule

logger = logging.getLogger(__name__)

HookEvent = Literal["pre_tool", "post_tool"]
OutcomeKind = Literal["allowed", "denied", "error", "timeout", "skipped_not_approved"]

#: Versão do contrato JSON entregue ao hook.
PAYLOAD_VERSION = 1
#: Teto de caracteres do resultado da tool entregue a um hook ``post_tool``.
MAX_RESULT_CHARS = 20_000
#: Teto de bytes lidos da saída padrão do hook.
MAX_OUTPUT_BYTES = 64 * 1024
#: Teto do texto de motivo mostrado ao modelo.
MAX_REASON_CHARS = 500

_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "COMSPEC")


@dataclass(frozen=True, slots=True)
class HookOutcome:
    """Resultado de um hook, sem o conteúdo da chamada."""

    hook_id: str
    event: HookEvent
    tool_name: str
    outcome: OutcomeKind
    mode: Literal["blocking", "informative"]
    blocked: bool
    reason: str
    duration_ms: int

    def evidence(self) -> dict[str, Any]:
        """Entrada de evidência do run."""
        return {"type": "hook_run", **asdict(self)}


def blocking_outcome(outcomes: list[HookOutcome]) -> HookOutcome | None:
    """Primeiro hook que impede a ação, se houver."""
    return next((item for item in outcomes if item.blocked), None)


def block_message(outcome: HookOutcome) -> str:
    """Texto do resultado de tool quando um hook impede a ação."""
    detail = f": {outcome.reason}" if outcome.reason else ""
    return f"Error: hook_denied: {outcome.hook_id}{detail}"


def _matches(rule: HookRule, tool_name: str) -> bool:
    return not rule.tools or any(
        fnmatch.fnmatchcase(tool_name, pattern) for pattern in rule.tools
    )


def _hook_environment(rule: HookRule) -> dict[str, str]:
    env = {key: os.environ[key] for key in _BASE_ENV if key in os.environ}
    env["VECTORA_HOOK_ID"] = rule.id
    env["VECTORA_HOOK_EVENT"] = rule.event
    return env


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def build_payload(
    rule: HookRule,
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_args: dict[str, Any],
    result: str | None,
    redact: Callable[[str], str],
) -> str:
    """JSON entregue ao hook, com argumentos e resultado já redigidos.

    Os argumentos têm os valores de chaves sensíveis (token, senha, chave de
    API) substituídos; o texto final ainda passa por ``redact`` para os
    padrões de credencial.
    """
    from backend.persistence.native.agent_run import redact_sensitive_data

    tool_args = redact_sensitive_data(tool_args)
    payload: dict[str, Any] = {
        "version": PAYLOAD_VERSION,
        "event": rule.event,
        "hook_id": rule.id,
        "run_id": getattr(ctx, "run_id", None),
        "thread_id": getattr(ctx, "thread_id", None),
        "tool": {"name": tool_name, "args": tool_args},
    }
    if result is not None:
        payload["result"] = _clip(result, MAX_RESULT_CHARS)
    return redact(json.dumps(payload, ensure_ascii=False, default=str))


def parse_decision(stdout: str) -> tuple[Literal["allow", "deny"], str]:
    """Interpreta a saída do hook. Vazia equivale a ``allow``.

    Levanta ``ValueError`` quando a saída não é um objeto JSON com
    ``decision`` igual a ``allow`` ou ``deny``.
    """
    text = stdout.strip()
    if not text:
        return "allow", ""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("resposta do hook não é JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("resposta do hook não é um objeto JSON")
    decision = data.get("decision")
    if decision not in ("allow", "deny"):
        raise ValueError("campo decision deve ser allow ou deny")
    reason = data.get("reason", "")
    return decision, _clip(reason, MAX_REASON_CHARS) if isinstance(reason, str) else ""


async def _run_command(
    rule: HookRule, payload: str, root: Path, *, sandboxed: bool, workspace_id: str
) -> tuple[int, str]:
    """Executa o comando e devolve ``(código de saída, stdout)``.

    Levanta ``TimeoutError`` quando passa de ``rule.timeout_s``.
    """
    if sandboxed:
        from backend.sandbox.policy import parse_policy
        from backend.sandbox.workspace_jail import jail_manager

        policy = parse_policy(root / "vectora.toml")
        worker = await jail_manager.get_or_spawn(workspace_id, str(root), policy)
        try:
            response = await worker.request(
                "exec",
                timeout_s=rule.timeout_s,
                command=list(rule.command),
                stdin=payload,
            )
        except RuntimeError as exc:
            if "não respondeu" in str(exc):
                raise TimeoutError from exc
            raise
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return int(response.get("exit_code", 0)), str(response.get("stdout", ""))

    process = await asyncio.create_subprocess_exec(
        *rule.command,
        cwd=str(root),
        env=_hook_environment(rule),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout_bytes, _ = await asyncio.wait_for(
            process.communicate(payload.encode("utf-8")), timeout=rule.timeout_s
        )
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        raise
    return process.returncode or 0, stdout_bytes[:MAX_OUTPUT_BYTES].decode(
        "utf-8", errors="replace"
    )


async def _evaluate(
    rule: HookRule,
    payload: str,
    *,
    root: Path,
    sandboxed: bool,
    workspace_id: str,
) -> tuple[OutcomeKind, str]:
    """Executa o comando e interpreta a resposta: ``(desfecho, motivo)``."""
    try:
        code, stdout = await _run_command(
            rule, payload, root, sandboxed=sandboxed, workspace_id=workspace_id
        )
    except TimeoutError:
        return "timeout", f"passou de {rule.timeout_s:g}s sem responder"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("hook %s falhou ao executar: %s", rule.id, type(exc).__name__)
        return "error", "não foi possível executar o comando"
    if code != 0:
        return "error", f"o comando terminou com código {code}"
    try:
        decision, reason = parse_decision(stdout)
    except ValueError as exc:
        return "error", str(exc)
    if decision == "deny":
        return "denied", reason or "negado pelo hook"
    return "allowed", reason


async def run_hook(
    rule: HookRule,
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_args: dict[str, Any],
    result: str | None,
    root: Path,
    approved: bool,
    sandboxed: bool,
    workspace_id: str,
    redact: Callable[[str], str],
    not_approved_reason: str = "hooks do workspace ainda não foram aprovados",
) -> HookOutcome:
    """Executa uma regra e devolve o desfecho; nunca levanta (exceto cancelamento)."""
    started = time.monotonic()
    if approved:
        payload = build_payload(
            rule,
            ctx=ctx,
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            redact=redact,
        )
        kind, reason = await _evaluate(
            rule, payload, root=root, sandboxed=sandboxed, workspace_id=workspace_id
        )
    else:
        kind, reason = "skipped_not_approved", not_approved_reason
    # Só um hook bloqueante que não deu `allowed` impede a ação; um hook que
    # nunca rodou por falta de aprovação não bloqueia nada.
    blocked = rule.mode == "blocking" and kind not in (
        "allowed",
        "skipped_not_approved",
    )
    return HookOutcome(
        hook_id=rule.id,
        event=rule.event,
        tool_name=tool_name,
        outcome=kind,
        mode=rule.mode,
        blocked=blocked,
        reason=_clip(reason, MAX_REASON_CHARS),
        duration_ms=round((time.monotonic() - started) * 1000),
    )


def _workspace_for(ctx: ToolContext) -> Any | None:
    from backend.workspace.workspace import workspace_registry

    workspace_id = getattr(ctx, "workspace_id", "") or ""
    if workspace_id:
        found = workspace_registry.get(workspace_id)
        if found is not None:
            return found
    return workspace_registry.get_active(getattr(ctx, "user_id", None))


async def run_hooks(
    event: HookEvent,
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_args: dict[str, Any],
    result: str | None = None,
    redact: Callable[[str], str],
) -> list[HookOutcome]:
    """Executa, em ordem, as regras do workspace para ``event`` e ``tool_name``.

    Para no primeiro hook que impede a ação. Sem workspace, sem regras ou com
    a configuração ilegível, devolve lista vazia.
    """
    workspace = _workspace_for(ctx)
    if workspace is None:
        return []
    root = Path(workspace.cwd)
    from backend.workspace.workspace_config import load_workspace_config

    config = await asyncio.to_thread(load_workspace_config, root)
    if config is None or not config.hooks.rules:
        return []
    rules = [
        rule
        for rule in config.hooks.rules
        if rule.event == event and _matches(rule, tool_name)
    ]
    if not rules:
        return []

    from backend.sandbox.policy import parse_policy
    from backend.workspace.workspace_config import hook_rules_digest

    sandboxed = parse_policy(root / "vectora.toml").enabled
    granted = bool(getattr(workspace, "hooks_approved", False))
    approved = granted and getattr(
        workspace, "hooks_rules_digest", None
    ) == hook_rules_digest(config.hooks.rules)
    not_approved_reason = (
        "as regras de hook mudaram desde a aprovação do workspace"
        if granted and getattr(workspace, "hooks_rules_digest", None)
        else "hooks do workspace ainda não foram aprovados"
    )
    workspace_id = str(getattr(workspace, "id", root))
    outcomes: list[HookOutcome] = []
    for rule in rules:
        item = await run_hook(
            rule,
            ctx=ctx,
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            root=root,
            approved=approved,
            sandboxed=sandboxed,
            workspace_id=workspace_id,
            redact=redact,
            not_approved_reason=not_approved_reason,
        )
        outcomes.append(item)
        logger.info(
            "harness_hook",
            extra={
                "hook_id": item.hook_id,
                "event": item.event,
                "tool": item.tool_name,
                "outcome": item.outcome,
                "blocked": item.blocked,
                "duration_ms": item.duration_ms,
            },
        )
        if item.blocked:
            break
    return outcomes
