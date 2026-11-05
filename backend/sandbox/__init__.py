"""Sandbox nativo — isolamento de execução por workspace (`vectora.toml`, seção
`[sandbox]`). Linux-first via bubblewrap. Ponto de integração real para
as tools do agente e o terminal interativo do usuário: `workspace_jail.
jail_manager` — um worker jailado persistente por workspace (`terminal`,
tools de arquivo e PTY interativo compartilham o mesmo processo jailado em
vez de reabrir `bwrap` a cada chamada). `run_sandboxed` continua disponível
para execução batch pontual (backends `docker`/`ssh`/`modal`).
"""

from __future__ import annotations

from backend.sandbox.linux import SandboxResult
from backend.sandbox.policy import SandboxPolicy, parse_policy
from backend.sandbox.runner import run_sandboxed
from backend.sandbox.workspace_jail import (
    JailedWorker,
    WorkerSpawnError,
    WorkspaceJailManager,
    jail_manager,
)

__all__ = [
    "JailedWorker",
    "SandboxPolicy",
    "SandboxResult",
    "WorkerSpawnError",
    "WorkspaceJailManager",
    "jail_manager",
    "parse_policy",
    "run_sandboxed",
]
