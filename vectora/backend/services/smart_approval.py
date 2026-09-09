"""Aprovação inteligente de comandos — avaliador auxiliar + allowlist.

Camada de UX sobre o HITL já vinculante do produto, nunca um substituto:
`evaluate_command` devolve só um booleano de anotação (`pre_approved`) pro
`HITLEvent` — quem decide se a tool executa continua sendo
`REQUIRE_APPROVAL`/`should_require_approval` em `backend/engine/hitl.py`, que
nem importa este módulo. O humano sempre confirma; no máximo o clique fica
marcado como reconhecido antes.

Duas fontes de pré-aprovação, nesta ordem:

1. **Allowlist persistente por workspace** — usuário marcou "sempre permitir
   isso" pra um padrão específico (comando exato do terminal, ou a tool
   inteira pras demais). Determinístico, sem custo de LLM.
2. **Avaliador auxiliar** — um LLM leve classifica o comando como
   reconhecido/seguro ou "precisa de humano". Só entra em jogo pras tools já
   em `_REQUIRE_APPROVAL` (não expande o que já era livre).

Falha em qualquer uma das duas nunca propaga — degrada pra `False` (sem
pré-aprovação, HITL normal), regra 11 do CLAUDE.md.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

_ALLOWLIST_KEY_PREFIX = "smart_approval_allowlist"

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
    """Assinatura determinística do comando pra comparar com a allowlist.

    Terminal usa o comando exato — comparar só pelo nome da tool liberaria
    qualquer comando futuro assim que um for aprovado uma vez. As demais
    tools usam o nome: seus args não têm um "comando" livre equivalente, e
    a chamada em si já é a unidade de risco.
    """
    if tool_name in ("terminal", "terminal_tool"):
        return f"{tool_name}:{str(args.get('command', '')).strip()}"
    return tool_name


def allowlist_id(signature: str) -> str:
    """Identificador opaco e estável de uma regra, sem expor seu conteúdo."""
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


def get_allowlist(workspace_id: str) -> list[str]:
    """Assinaturas pré-aprovadas do workspace. Workspace desconhecido/vazio
    devolve lista vazia, nunca lança."""
    if not workspace_id:
        return []
    raw = _runtime_settings().get(_allowlist_key(workspace_id), [])
    return [str(s) for s in raw] if isinstance(raw, list) else []


def add_to_allowlist(workspace_id: str, tool_name: str, args: dict) -> list[str]:
    """Persiste a assinatura de `(tool_name, args)` na allowlist do workspace."""
    if not workspace_id:
        msg = "workspace_id vazio — allowlist é sempre por workspace"
        raise ValueError(msg)
    sig = _signature(tool_name, args)
    current = get_allowlist(workspace_id)
    if sig not in current:
        current = [*current, sig]
        _runtime_settings().set(_allowlist_key(workspace_id), current)
    return current


def remove_from_allowlist(workspace_id: str, signature: str) -> list[str]:
    """Revoga uma assinatura — volta a exigir aprovação normal."""
    if not workspace_id:
        return []
    current = [s for s in get_allowlist(workspace_id) if s != signature]
    _runtime_settings().set(_allowlist_key(workspace_id), current)
    return current


def remove_from_allowlist_by_id(workspace_id: str, rule_id: str) -> list[str]:
    """Revoga uma regra usando apenas seu identificador opaco."""
    signature = next(
        (
            candidate
            for candidate in get_allowlist(workspace_id)
            if hmac.compare_digest(allowlist_id(candidate), rule_id)
        ),
        None,
    )
    if signature is None:
        return get_allowlist(workspace_id)
    return remove_from_allowlist(workspace_id, signature)


def is_allowlisted(workspace_id: str, tool_name: str, args: dict) -> bool:
    if not workspace_id:
        return False
    return _signature(tool_name, args) in get_allowlist(workspace_id)


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
    """`True` = pré-aprovado (a UI mostra reconhecido; o HITL pausa igual).

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
    "evaluate_command",
    "get_allowlist",
    "is_allowlisted",
    "remove_from_allowlist",
    "remove_from_allowlist_by_id",
]
