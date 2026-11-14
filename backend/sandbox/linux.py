"""Execução sandboxed real via `bwrap` (Linux-only).

`run_local_sandboxed` assume um Linux com `bwrap` disponível; ausência
do binário degrada com erro claro, nunca trava o caller (tools
defensivas, CLAUDE.md regra 11). macOS/Windows não têm `bwrap` nativo —
ver `backend/sandbox/workspace_jail.py` para o caminho de sandbox
nessas plataformas.
"""

from __future__ import annotations

import asyncio
import errno
import importlib
import logging
import os
from dataclasses import dataclass
from typing import Any

from backend.sandbox.dry_run import (
    CLONE_NAMESPACE_FLAGS,
    DENIED_SYSCALLS,
    TIOCSTI,
    build_bwrap_command,
)
from backend.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)


def _load_seccomp() -> Any | None:
    """Módulo do libseccomp: o pacote `pyseccomp` (o que o projeto instala) ou,
    em sistemas que o trazem pela distro, `seccomp`. `None` se nenhum existe."""
    for name in ("pyseccomp", "seccomp"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


def build_seccomp_filter() -> bytes | None:
    """Compila `DENIED_SYSCALLS` num programa BPF via libseccomp — `ALLOW`
    por default, `KILL` nas syscalls perigosas da denylist, `KILL` em `clone`
    que cria namespace e `EPERM` em `ioctl(TIOCSTI)`. `None` se o libseccomp não
    estiver disponível no sistema — o caller marca o isolamento como parcial
    (namespaces + Landlock do bwrap continuam valendo) e, com
    `require_full_isolation`, recusa iniciar."""
    seccomp = _load_seccomp()
    if seccomp is None:
        logger.warning(
            "sandbox: pyseccomp/libseccomp indisponível — rodando sem filtro "
            "seccomp (namespaces do bwrap continuam ativos)"
        )
        return None

    f = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    for name in DENIED_SYSCALLS:
        try:
            f.add_rule(seccomp.KILL, name)
        except Exception:
            logger.debug("sandbox: syscall %r não existe nesta arch/kernel", name)
    try:
        f.add_rule(
            seccomp.ERRNO(errno.EPERM), "ioctl", seccomp.Arg(1, seccomp.EQ, TIOCSTI)
        )
        for flag in CLONE_NAMESPACE_FLAGS:
            f.add_rule(
                seccomp.KILL,
                "clone",
                seccomp.Arg(0, seccomp.MASKED_EQ, flag, flag),
            )
    except Exception:
        logger.debug("sandbox: regra condicional de seccomp indisponível")
    # `export_bpf` precisa de um arquivo de verdade (usa o descritor), não de um
    # `BytesIO`: um arquivo anônimo em memória serve e não toca o disco.
    fd = os.memfd_create("vectora-seccomp")
    try:
        with os.fdopen(os.dup(fd), "wb") as sink:
            f.export_bpf(sink)
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


async def run_local_sandboxed(
    command: list[str],
    workspace_dir: str,
    policy: SandboxPolicy,
    *,
    timeout_s: float = 60.0,
) -> SandboxResult:
    """Roda `command` dentro de `bwrap` conforme `policy`. `bwrap` ausente
    do sistema (`FileNotFoundError`) devolve `exit_code=127` com mensagem
    clara em stderr; sem permissão de execução (`PermissionError`) devolve
    `exit_code=126` (convenção POSIX); timeout mata o processo e devolve
    `exit_code=124`."""
    argv = build_bwrap_command(policy, workspace_dir, command)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("sandbox: binário bwrap não encontrado no sistema")
        return SandboxResult(
            stdout="",
            stderr="Error: bwrap não está instalado neste sistema — sandbox indisponível.",
            exit_code=127,
        )
    except PermissionError:
        logger.warning("sandbox: sem permissão para executar o binário bwrap")
        return SandboxResult(
            stdout="",
            stderr="Error: sem permissão para executar bwrap — sandbox indisponível.",
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
