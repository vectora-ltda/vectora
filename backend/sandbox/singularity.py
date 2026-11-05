"""Backend de sandbox via Singularity/Apptainer — alternativa ao Docker em
hosts Linux sem daemon Docker (comum em ambientes HPC/cluster, onde
containers com root daemon são vetados por política). Singularity/Apptainer
roda sem daemon, com namespaces unprivilegiados nativos do kernel — mesma
classe de isolamento do backend `docker`, mecanismo diferente.

Hardening (mesmo espírito de `docker.py`): `--containall` isola PID/IPC/UTS/
filesystem do host completamente (sem herdar `$HOME`/`/tmp` do usuário
real); `--no-home` bloqueia o auto-bind do `$HOME` real que o Singularity
faz por padrão (comportamento surpreendente — sem isso, dotfiles/credenciais
do usuário vazariam pro worker mesmo com `--containall`); `--writable-tmpfs`
dá um `/tmp` gravável efêmero sem persistir nada no host.

Aceita tanto o binário `singularity` quanto seu fork `apptainer` (mesma
CLI, nomes de comando intercambiáveis) — detecta qual está disponível na
primeira chamada e cacheia.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

from backend.sandbox.linux import SandboxResult
from backend.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)

DEFAULT_SINGULARITY_IMAGE = "docker://python:3.13-slim"

_BASE_SECURITY_ARGS: list[str] = [
    "--containall",
    "--no-home",
    "--writable-tmpfs",
]

_binary_cache: str | None = None


def _resolve_binary() -> str | None:
    """`singularity` e `apptainer` são o mesmo produto (Apptainer é o fork
    que herdou o projeto após a Sylabs relicenciar o original) — qualquer
    um dos dois binários serve. Cacheado por processo: a disponibilidade
    não muda em runtime."""
    global _binary_cache
    if _binary_cache is not None:
        return _binary_cache
    for candidate in ("singularity", "apptainer"):
        if shutil.which(candidate):
            _binary_cache = candidate
            return candidate
    return None


def build_singularity_command(
    policy: SandboxPolicy, workspace_dir: str, command: list[str], *, binary: str
) -> list[str]:
    """Monta o argv de `singularity exec` — separado da execução pra ser
    testável sem o binário instalado (mesmo espírito de
    `docker.build_docker_command`)."""
    image = policy.docker_image or DEFAULT_SINGULARITY_IMAGE
    argv = [binary, "exec"]
    argv += _BASE_SECURITY_ARGS
    argv += ["--bind", f"{workspace_dir}:{workspace_dir}"]
    argv += ["--pwd", workspace_dir]
    if policy.lockdown:
        # Singularity não tem um "--network none" de primeira classe como o
        # Docker — `--net` isola pra uma rede privada sem rota externa, o
        # equivalente prático disponível na CLI.
        argv += ["--net"]
    argv += [image, *command]
    return argv


async def run_singularity_sandboxed(
    command: list[str],
    workspace_dir: str,
    policy: SandboxPolicy,
    *,
    timeout_s: float = 60.0,
) -> SandboxResult:
    """Roda `command` num container Singularity/Apptainer efêmero. Nenhum
    dos dois binários instalado devolve `exit_code=127` com mensagem clara;
    sem permissão de execução devolve `exit_code=126` — nunca levanta
    exceção (tools defensivas, CLAUDE.md regra 11)."""
    binary = _resolve_binary()
    if binary is None:
        logger.warning("sandbox.singularity: nem singularity nem apptainer encontrados")
        return SandboxResult(
            stdout="",
            stderr=(
                "Error: Singularity/Apptainer não está instalado neste "
                "sistema — sandbox indisponível."
            ),
            exit_code=127,
        )

    argv = build_singularity_command(policy, workspace_dir, command, binary=binary)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning(
            "sandbox.singularity: binário %s não encontrado no sistema", binary
        )
        return SandboxResult(
            stdout="",
            stderr=(
                f"Error: {binary} não está instalado neste sistema — "
                "sandbox indisponível."
            ),
            exit_code=127,
        )
    except PermissionError:
        logger.warning("sandbox.singularity: sem permissão para executar %s", binary)
        return SandboxResult(
            stdout="",
            stderr=f"Error: sem permissão para executar {binary} — sandbox indisponível.",
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


def _reset_binary_cache_for_tests() -> None:
    """Só para testes: força `_resolve_binary()` a re-detectar."""
    global _binary_cache
    _binary_cache = None
