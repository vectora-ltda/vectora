"""Backend de sandbox via Docker — roda o comando dentro de um container
efêmero (`docker run --rm`). Imagem configurável em `vectora.toml`
(`[sandbox] docker_image`), default uma imagem mínima. `lockdown` nega
rede (`--network none`), mesmo princípio do backend `local` (bwrap).

Hardening: o container padrão do Docker roda com um conjunto amplo de
capabilities e sem teto de recurso — um comando do agente podia consumir
a máquina inteira ou escalar privilégio dentro do container. As flags de
segurança são sempre aplicadas; já os limites de recurso (cpu/memória/
PIDs) dependem de controlador cgroup delegado, que não existe em todo
host (LXC não-privilegiado, por exemplo) — aplicá-los cegamente faria o
container não subir, então são opcionais e degradam com aviso.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from backend.sandbox.linux import SandboxResult
from backend.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)

DEFAULT_DOCKER_IMAGE = "python:3.13-slim"

#: Capabilities e flags que não dependem de cgroup — sempre seguras de
#: aplicar. `no-new-privileges` impede escalada mesmo se o processo achar
#: um binário setuid; `/tmp` continua gravável (pip/npm precisam), mas
#: `nosuid` e com teto de tamanho.
_BASE_SECURITY_ARGS: list[str] = [
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--tmpfs",
    "/tmp:rw,nosuid,size=512m",  # noqa: S108  # nosec B108 — /tmp do container, não do host
]

#: Limites por perfil (normal/lockdown), no mesmo espírito dos 2 perfis de
#: `rlimits.py`. Lockdown é pra código não-confiável: metade do orçamento.
_RESOURCE_PROFILES: dict[str, dict[str, str]] = {
    "normal": {"memory": "1g", "cpus": "2", "pids-limit": "512"},
    "lockdown": {"memory": "512m", "cpus": "1", "pids-limit": "256"},
}


def _cgroup_limits_available() -> bool:
    """True quando dá pra aplicar --memory/--cpus/--pids-limit.

    Exigem controlador cgroup delegado ao Docker. Em host sem isso (LXC
    não-privilegiado é o caso clássico) o `docker run` falha na largada —
    melhor rodar sem teto de recurso, com as flags de segurança ainda
    aplicadas, do que não rodar.
    """
    return Path("/sys/fs/cgroup").is_dir()


def build_docker_command(
    policy: SandboxPolicy, workspace_dir: str, command: list[str]
) -> list[str]:
    """Monta o argv de `docker run` — separado da execução pra ser
    testável sem o binário `docker` instalado (mesmo espírito de
    `dry_run.build_bwrap_command`)."""
    image = policy.docker_image or DEFAULT_DOCKER_IMAGE
    argv = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workspace_dir}:{workspace_dir}",
        "-w",
        workspace_dir,
    ]
    argv += _BASE_SECURITY_ARGS

    if _cgroup_limits_available():
        profile = _RESOURCE_PROFILES["lockdown" if policy.lockdown else "normal"]
        for flag, value in profile.items():
            argv += [f"--{flag}", value]
    else:
        logger.warning(
            "sandbox.docker: cgroup indisponível — container roda sem teto de "
            "cpu/memória/PIDs (flags de segurança seguem aplicadas)"
        )

    if policy.lockdown:
        # Sem rede e com raiz read-only: só o /tmp em tmpfs aceita escrita
        # fora dos paths que o usuário liberou no bind do workspace.
        argv += ["--network", "none", "--read-only"]
    argv += [image, *command]
    return argv


async def run_docker_sandboxed(
    command: list[str],
    workspace_dir: str,
    policy: SandboxPolicy,
    *,
    timeout_s: float = 60.0,
) -> SandboxResult:
    """Roda `command` num container Docker efêmero. `docker` ausente do
    sistema devolve `exit_code=127` com mensagem clara; sem permissão de
    execução (`PermissionError`) devolve `exit_code=126` (convenção POSIX)
    — nunca levanta exceção (tools defensivas)."""
    argv = build_docker_command(policy, workspace_dir, command)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("sandbox.docker: binário docker não encontrado no sistema")
        return SandboxResult(
            stdout="",
            stderr="Error: Docker não está instalado neste sistema — sandbox indisponível.",
            exit_code=127,
        )
    except PermissionError:
        logger.warning("sandbox.docker: sem permissão para executar o binário docker")
        return SandboxResult(
            stdout="",
            stderr="Error: sem permissão para executar docker — sandbox indisponível.",
            exit_code=126,
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
