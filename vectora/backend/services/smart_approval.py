"""Aprovação auxiliar e concessões pré-aprovadas de uso único.

O avaliador de linguagem só produz uma anotação visual. Uma concessão explícita
do usuário é diferente: autoriza uma chamada idêntica uma única vez, dentro do
mesmo workspace e run, antes do prazo. Ferramentas irreversíveis não aceitam
concessões. Falhas de armazenamento degradam para o HITL normal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_ALLOWLIST_KEY_PREFIX = "smart_approval_allowlist"
_DEFAULT_GRANT_TTL_SECONDS = 900
_GRANTABLE_TOOLS = frozenset(
    {
        "terminal",
        "terminal_tool",
        "file_write",
        "file_write_tool",
        "file_edit",
        "kanban_create",
        "kanban_update_status",
    }
)

#: Comando fora deste conjunto nunca é reconhecido automaticamente pelo
#: avaliador auxiliar como "seguro" — mesmo que o LLM diga que sim, o prompt
#: pede pra ele considerar só leitura/consulta como candidato.
_PROMPT_SISTEMA = (
    "Você avalia se um comando de terminal ou chamada de tool é seguro o "
    "bastante para ser marcado como reconhecido, sem executar nada sozinho — "
    "um humano ainda vai confirmar depois. Responda apenas com a palavra "
    "SAFE se for uma operação de leitura/consulta rotineira e sem efeito "
    "colateral perigoso (ex.: git status, listar arquivos). Responda REVIEW "
    "para qualquer coisa destrutiva, irreversível, ou que você não reconheça "
    "com confiança (ex.: rm -rf, git push --force, apagar recursos)."
)


_AskLLM = Callable[[str, dict], Awaitable[bool]]


def _runtime_settings() -> Any:
    from backend.workspace.runtime_settings import runtime_settings

    return runtime_settings


def _allowlist_key(workspace_id: str) -> str:
    return f"{_ALLOWLIST_KEY_PREFIX}:{workspace_id}"


def _signature(tool_name: str, args: dict) -> str:
    """Hash determinístico dos argumentos completos da chamada."""
    payload = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{tool_name}:{digest}"


def args_digest(tool_name: str, args: dict) -> str:
    """Return the hash of a tool call's complete canonical arguments."""
    return _signature(tool_name, args).partition(":")[2]


def allowlist_id(signature: str) -> str:
    """Identificador opaco e estável de uma regra, sem expor seu conteúdo."""
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


def rule_id(tool_name: str, args: dict) -> str:
    """Retorna o identificador opaco da regra derivada dos argumentos."""
    return allowlist_id(_signature(tool_name, args))


def run_grant_id(workspace_id: str, run_id: str, tool_name: str, args: dict) -> str:
    """Return the opaque id used to audit and revoke this exact run grant."""
    return allowlist_id(f"{workspace_id}:{run_id}:{_signature(tool_name, args)}")


def get_allowlist(workspace_id: str) -> list[str]:
    """Lista IDs opacos de concessões ainda válidas do workspace."""
    if not workspace_id:
        return []
    raw = _runtime_settings().get(_allowlist_key(workspace_id), [])
    if not isinstance(raw, list):
        return []
    return [
        str(item["grant_id"])
        for item in raw
        if isinstance(item, dict)
        and item.get("grant_id")
        and not _is_expired_grant(item)
    ]


def add_to_allowlist(
    workspace_id: str,
    tool_name: str,
    args: dict,
    *,
    run_id: str,
    ttl_seconds: int = _DEFAULT_GRANT_TTL_SECONDS,
) -> list[str]:
    """Create a one-use grant scoped to exact arguments, workspace and run."""
    if not workspace_id:
        msg = "workspace_id vazio — allowlist é sempre por workspace"
        raise ValueError(msg)
    if not run_id.strip():
        raise ValueError("run_id vazio — concessões precisam pertencer a uma execução")
    if tool_name not in _GRANTABLE_TOOLS:
        raise ValueError("esta ferramenta não aceita concessão pré-aprovada")
    if not 1 <= ttl_seconds <= 3600:
        raise ValueError("ttl_seconds deve estar entre 1 e 3600")
    sig = _signature(tool_name, args)
    grant_id = run_grant_id(workspace_id, run_id, tool_name, args)
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
    grant = {
        "grant_id": grant_id,
        "workspace_id": workspace_id,
        "tool_name": tool_name,
        "args_hash": sig.partition(":")[2],
        "run_id": run_id,
        "expires_at": expires_at,
    }

    def append_once(current: list[object]) -> list[object]:
        current[:] = [item for item in current if not _is_expired_grant(item)]
        if not any(
            isinstance(item, dict) and item.get("grant_id") == grant_id
            for item in current
        ):
            current.append(grant)
        return current

    _runtime_settings().update_list(_allowlist_key(workspace_id), append_once)
    return get_allowlist(workspace_id)


def remove_from_allowlist(workspace_id: str, signature: str) -> list[str]:
    """Revoga uma assinatura — volta a exigir aprovação normal."""
    if not workspace_id:
        return []

    def remove_matching(current: list[object]) -> list[object]:
        return [
            item
            for item in current
            if not (isinstance(item, dict) and item.get("grant_id") == signature)
        ]

    _runtime_settings().update_list(_allowlist_key(workspace_id), remove_matching)
    return get_allowlist(workspace_id)


def remove_from_allowlist_by_id(workspace_id: str, rule_id: str) -> list[str]:
    """Revoga uma regra usando apenas seu identificador opaco."""
    if not workspace_id:
        return []

    def remove_matching_id(current: list[object]) -> list[object]:
        return [
            item
            for item in current
            if not (
                isinstance(item, dict)
                and isinstance(item.get("grant_id"), str)
                and hmac.compare_digest(item["grant_id"], rule_id)
            )
        ]

    _runtime_settings().update_list(_allowlist_key(workspace_id), remove_matching_id)
    return get_allowlist(workspace_id)


def is_allowlisted(workspace_id: str, tool_name: str, args: dict) -> bool:
    """Legacy visual recognition is not an execution grant."""
    return False


def _is_expired_grant(item: object) -> bool:
    if not isinstance(item, dict):
        return True
    expires_at = item.get("expires_at")
    if not isinstance(expires_at, str):
        return True
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(UTC)
    except (TypeError, ValueError):
        return True


def consume_run_grant(
    workspace_id: str, run_id: str, tool_name: str, args: dict
) -> str | None:
    """Atomically consume one exact, unexpired pre-approval grant."""
    if not workspace_id or not run_id or tool_name not in _GRANTABLE_TOOLS:
        return None
    args_hash = args_digest(tool_name, args)
    consumed: str | None = None

    def consume(current: list[object]) -> list[object]:
        nonlocal consumed
        retained: list[object] = []
        for item in current:
            if _is_expired_grant(item):
                continue
            if (
                consumed is None
                and isinstance(item, dict)
                and item.get("workspace_id") == workspace_id
                and item.get("run_id") == run_id
                and item.get("tool_name") == tool_name
                and item.get("args_hash") == args_hash
            ):
                consumed = str(item.get("grant_id") or "")
                continue
            retained.append(item)
        return retained

    _runtime_settings().update_list(_allowlist_key(workspace_id), consume)
    return consumed or None


async def _default_ask_llm(tool_name: str, args: dict) -> bool:
    """Avaliador auxiliar de verdade — modelo leve classifica o comando."""
    from backend.services.utils import load_native_llm
    from backend.vtypes.message import MessageRole, text_message

    model = load_native_llm()
    resposta = await model.agenerate(
        [
            text_message(MessageRole.SYSTEM, _PROMPT_SISTEMA),
            text_message(MessageRole.USER, f"tool: {tool_name}\nargs: {args}"),
        ]
    )
    texto = resposta.text().strip().upper()
    return texto.startswith("SAFE")


async def evaluate_command(
    tool_name: str,
    args: dict,
    *,
    workspace_id: str,
    ask_llm: _AskLLM | None = None,
) -> bool:
    """Classify a call for the UI; this result never authorizes execution.

    Nunca lança — qualquer falha (allowlist ilegível, LLM fora do ar) degrada
    pra `False`, que é o comportamento de hoje sem esta camada.
    """
    try:
        if is_allowlisted(workspace_id, tool_name, args):
            return True
        avaliador = ask_llm or _default_ask_llm
        return await avaliador(tool_name, args)
    except Exception:
        logger.debug(
            "smart_approval: avaliação falhou, seguindo sem pré-aprovação",
            exc_info=True,
        )
        return False


__all__ = [
    "add_to_allowlist",
    "allowlist_id",
    "args_digest",
    "consume_run_grant",
    "evaluate_command",
    "get_allowlist",
    "is_allowlisted",
    "remove_from_allowlist",
    "remove_from_allowlist_by_id",
    "rule_id",
    "run_grant_id",
]
