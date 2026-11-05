"""Backend de sandbox via SSH — reaproveita `SshTransport` (asyncssh), a
mesma implementação já usada pra workspaces remotos. Útil pra quem já roda
o Vectora numa VPS e quer isolar a execução do agente ainda mais, rodando
comandos num host separado em vez do processo local.
"""

from __future__ import annotations

import logging

from backend.sandbox.linux import SandboxResult
from backend.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)


async def run_ssh_sandboxed(
    command: list[str],
    workspace_dir: str,
    policy: SandboxPolicy,
    *,
    timeout_s: float = 60.0,
) -> SandboxResult:
    """Executa `command` num host remoto via SSH. `remote_host` ausente na
    política falha fechado (`exit_code=126`) — nunca cai silenciosamente
    pro processo local."""
    if not policy.remote_host:
        return SandboxResult(
            stdout="",
            stderr="Error: backend 'ssh' requer 'remote_host' em [sandbox].",
            exit_code=126,
        )

    from backend.transport.ssh import SshTransport

    transport = SshTransport(
        remote_host=policy.remote_host,
        ssh_key_id=policy.ssh_key_id,
        user_id=None,
    )
    try:
        result = await transport.run(command, cwd=workspace_dir, timeout=timeout_s)
    except Exception as exc:
        logger.warning("sandbox.ssh: falha ao executar via SSH (%s)", exc)
        return SandboxResult(
            stdout="", stderr=f"Error: falha na execução via SSH: {exc}", exit_code=125
        )
    finally:
        await transport.close()

    return SandboxResult(
        stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code
    )
