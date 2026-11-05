"""Ponto de integração único do sandbox — tools que hoje spawnam subprocess
direto (`terminal`, git) chamam `run_sandboxed` em vez de
`asyncio.create_subprocess_exec` quando querem respeitar `vectora.toml`.

Sem `vectora.toml`/seção `[sandbox]` no workspace: `run_sandboxed` roda o
comando normalmente (sem wrapper) — comportamento atual preservado, callers
podem chamar isto incondicionalmente sem regressão.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from backend.sandbox.docker import run_docker_sandboxed
from backend.sandbox.linux import SandboxResult, run_local_sandboxed
from backend.sandbox.macos import run_macos_sandboxed
from backend.sandbox.modal import run_modal_sandboxed
from backend.sandbox.policy import SandboxPolicy, parse_policy
from backend.sandbox.singularity import run_singularity_sandboxed
from backend.sandbox.ssh import run_ssh_sandboxed

logger = logging.getLogger(__name__)

_BACKENDS = {
    "local": run_local_sandboxed,
    "docker": run_docker_sandboxed,
    "ssh": run_ssh_sandboxed,
    "modal": run_modal_sandboxed,
    "macos": run_macos_sandboxed,
    "singularity": run_singularity_sandboxed,
}

#: Backends que criam um ambiente novo (container/sandbox cloud) por
#: execução. Cada um custa recurso real de máquina ou de conta cobrada, e
#: nada no fluxo do agente impede N chamadas paralelas — `local`, `macos`
#: e `ssh` não entram porque reusam um worker/conexão existente (ou, no
#: caso do `macos`, rodam direto no host sem criar um ambiente novo).
_BATCH_BACKENDS = frozenset({"docker", "modal", "singularity"})

MAX_CONCURRENT_BATCH_RUNS_PER_WORKSPACE = 3

#: Contagem de execuções em voo por workspace. A quota é **por workspace**,
#: não global: um workspace saturado nunca impede outro de executar.
_active_batch_runs: dict[str, int] = {}


async def _run_unsandboxed(
    command: list[str], workspace_dir: str, timeout_s: float
) -> SandboxResult:
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=workspace_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
        return SandboxResult(
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            exit_code=proc.returncode or 0,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return SandboxResult(
            stdout="",
            stderr=f"Error: comando excedeu o timeout de {timeout_s}s.",
            exit_code=124,
            timed_out=True,
        )


async def run_sandboxed(
    command: list[str], workspace_dir: str, *, timeout_s: float = 60.0
) -> SandboxResult:
    """Lê `vectora.toml` do workspace e despacha pro backend certo.

    `backend` desconhecido em `[sandbox]` falha fechado (nega execução,
    `exit_code=126`) — nunca cai silenciosamente pro modo sem sandbox.
    """
    policy = parse_policy(Path(workspace_dir) / "vectora.toml")
    if not policy.enabled:
        return await _run_unsandboxed(command, workspace_dir, timeout_s)

    backend_fn = _BACKENDS.get(policy.backend)
    if backend_fn is None:
        logger.error(
            "sandbox: backend '%s' não suportado — negando execução (fail-closed)",
            policy.backend,
        )
        return SandboxResult(
            stdout="",
            stderr=f"Error: backend de sandbox '{policy.backend}' não suportado.",
            exit_code=126,
        )
    if policy.backend in _BATCH_BACKENDS:
        return await _run_batch_limited(
            backend_fn, command, workspace_dir, policy, timeout_s
        )
    return await backend_fn(command, workspace_dir, policy, timeout_s=timeout_s)


async def _run_batch_limited(
    backend_fn: Callable[..., Awaitable[SandboxResult]],
    command: list[str],
    workspace_dir: str,
    policy: SandboxPolicy,
    timeout_s: float,
) -> SandboxResult:
    """Rejeita (não enfileira) execuções acima da quota do workspace: o
    caller é uma tool do agente, e uma espera indefinida numa fila seria
    indistinguível de travamento pra quem está no chat."""
    active = _active_batch_runs.get(workspace_dir, 0)
    if active >= MAX_CONCURRENT_BATCH_RUNS_PER_WORKSPACE:
        logger.warning(
            "sandbox: workspace %s já tem %d execuções '%s' em voo — rejeitando",
            workspace_dir,
            active,
            policy.backend,
        )
        return SandboxResult(
            stdout="",
            stderr=(
                f"Error: limite de {MAX_CONCURRENT_BATCH_RUNS_PER_WORKSPACE} "
                f"execuções simultâneas no backend '{policy.backend}' atingido "
                "para este workspace — aguarde uma terminar e tente de novo."
            ),
            exit_code=126,
        )
    _active_batch_runs[workspace_dir] = active + 1
    try:
        return await backend_fn(command, workspace_dir, policy, timeout_s=timeout_s)
    finally:
        remaining = _active_batch_runs.get(workspace_dir, 1) - 1
        if remaining > 0:
            _active_batch_runs[workspace_dir] = remaining
        else:
            _active_batch_runs.pop(workspace_dir, None)
