"""Backend de sandbox via Modal (sandbox cloud sob demanda) — via SDK
oficial `modal`. Opt-in e com custo externo (billing por uso) — nunca o
default; só ativa quando `vectora.toml` declara `backend = "modal"`
explicitamente.

API usada (`modal.Sandbox.create` / `.exec` / `ContainerProcess`):
https://modal.com/docs/guide/sandbox-spawn
"""

from __future__ import annotations

import asyncio
import logging

from backend.sandbox.linux import SandboxResult
from backend.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)

#: Teto de recurso por perfil (mesma divisão normal/lockdown de
#: `rlimits.py` e do backend docker). Aqui a execução é remota e cobrada —
#: sem teto, um comando em loop roda no limite da conta e gera custo real.
_RESOURCE_PROFILES: dict[str, dict[str, int]] = {
    "normal": {"cpu": 2, "memory": 2048},
    "lockdown": {"cpu": 1, "memory": 1024},
}


async def run_modal_sandboxed(
    command: list[str],
    workspace_dir: str,
    policy: SandboxPolicy,
    *,
    timeout_s: float = 60.0,
) -> SandboxResult:
    """Roda `command` num Modal Sandbox efêmero. Sem o SDK instalado ou sem
    credenciais configuradas, degrada com mensagem clara oferecendo os
    outros backends — nunca trava o caller nem cai silenciosamente pro
    processo local (fail-closed: o usuário pediu 'modal' explicitamente).

    `workspace_dir` não é usado: diferente de `local`/`docker`/`ssh`, o
    Modal Sandbox roda num filesystem cloud isolado, não um bind-mount do
    workspace local — subir o conteúdo do workspace pra lá (via
    `modal.Volume`) fica pra quando este backend tiver uso real, não faz
    sentido implementar às cegas. De `policy` só o perfil de recurso
    importa aqui (ver `_RESOURCE_PROFILES`).
    """
    del workspace_dir
    try:
        import modal  # ty: ignore[unresolved-import]
    except ImportError:
        return SandboxResult(
            stdout="",
            stderr=(
                "Error: pacote 'modal' não instalado — backend 'modal' "
                "indisponível. Instale com `pip install modal` ou use "
                "backend 'local', 'docker' ou 'ssh'."
            ),
            exit_code=127,
        )

    def _run_sync() -> tuple[str, str, int]:
        app = modal.App.lookup("vectora-sandbox", create_if_missing=True)
        # Teto de recurso explícito: sem isso um comando em loop roda no
        # limite default da conta e o custo é cobrado de verdade — aqui a
        # execução é remota e paga, diferente do backend local.
        limits = _RESOURCE_PROFILES["lockdown" if policy.lockdown else "normal"]
        sb = modal.Sandbox.create(app=app, cpu=limits["cpu"], memory=limits["memory"])
        try:
            process = sb.exec(*command, timeout=int(timeout_s))
            exit_code = process.wait()
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            return stdout, stderr, exit_code
        finally:
            sb.terminate()

    try:
        stdout, stderr, exit_code = await asyncio.wait_for(
            asyncio.to_thread(_run_sync), timeout=timeout_s + 10.0
        )
        return SandboxResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
    except TimeoutError:
        return SandboxResult(
            stdout="",
            stderr=f"Error: comando excedeu o timeout de {timeout_s}s.",
            exit_code=124,
            timed_out=True,
        )
    except Exception as exc:
        logger.warning("sandbox.modal: falha (%s)", exc)
        return SandboxResult(
            stdout="",
            stderr=(
                f"Error: falha no Modal ({exc}). Verifique credenciais "
                "(modal token) ou use outro backend."
            ),
            exit_code=125,
        )
