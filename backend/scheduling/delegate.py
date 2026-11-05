"""Delegate — worktree isolado por task.

Cada subagent task que edita arquivos pode rodar num git worktree próprio
em vez do working tree principal, evitando que tasks concorrentes pisem
uma na outra. Reaproveita a lógica de worktree já existente em
`backend/tools/git.py` (mesma implementação que a tool `git_worktree`
usa) — este módulo só automatiza a chamada por `task_id`, não reimplementa
nada.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DelegateError(RuntimeError):
    """Falha ao criar/remover o worktree de uma task — reportada com
    mensagem clara em vez de deixar a task presa num estado inconsistente."""


def _resolve_repo(workspace_id: str) -> Any:
    import git as gitpython

    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        raise DelegateError(f"Workspace '{workspace_id}' não encontrado.")
    try:
        return gitpython.Repo(ws.cwd, search_parent_directories=True)
    except Exception as exc:
        raise DelegateError(
            f"Workspace '{workspace_id}' não é um repositório git válido: {exc}"
        ) from exc


async def create_task_worktree(workspace_id: str, task_id: str) -> str:
    """Cria um worktree isolado pra `task_id` e devolve o path absoluto.

    Falha ao criar (branch inválida, disco cheio, workspace sem git) levanta
    `DelegateError` com mensagem clara — a task nunca fica presa achando
    que tem um worktree que não existe.
    """
    from backend.tools.git import _git_worktree_impl

    repo = _resolve_repo(workspace_id)
    result = _git_worktree_impl(repo, workspace_id, "add", name=task_id)
    if result["status"] != "ok":
        raise DelegateError(
            f"Falha ao criar worktree pra task '{task_id}': {result.get('message')}"
        )
    return result["path"]


async def remove_task_worktree(workspace_id: str, task_id: str) -> None:
    """Remove o worktree da task ao concluir. Idempotente — worktree já
    removido (ou nunca criado) não é erro, só um warning no log.

    `git worktree add <task_id>` (sem `-b` explícito) cria implicitamente
    uma branch `task_id` a partir do HEAD atual — sem deletá-la aqui, ela
    fica órfã no repositório pra sempre a cada task concluída. Deleção é
    best-effort: branch já removida manualmente ou checked out em outro
    lugar não deve impedir a limpeza do worktree, que já valeu."""
    from backend.tools.git import _git_worktree_impl

    repo = _resolve_repo(workspace_id)
    result = _git_worktree_impl(repo, workspace_id, "remove", name=task_id)
    if result["status"] != "ok":
        logger.warning(
            "delegate: falha ao remover worktree da task %s (%s) — ignorado",
            task_id,
            result.get("message"),
        )
        return
    try:
        repo.git.branch("-D", task_id)
    except Exception as exc:
        logger.warning(
            "delegate: falha ao deletar branch da task %s (%s) — ignorado",
            task_id,
            exc,
        )
