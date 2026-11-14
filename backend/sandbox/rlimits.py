"""Resource limits (POSIX `rlimit`) aplicados ao worker jailado antes do
loop RPC começar — contém fork bombs e exaustão de descritores/arquivos
que os namespaces do bwrap sozinhos não limitam. Mesmo espírito do
`ai-jail` original (2 perfis, normal e lockdown), via `resource` (stdlib,
sem dependência nova). `resource` só existe em POSIX — no-op silencioso
em qualquer outro SO (o worker em si só roda em Linux/WSL2 hoje)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RlimitProfile:
    nproc: int
    nofile: int


NORMAL_PROFILE = RlimitProfile(nproc=4096, nofile=65536)
LOCKDOWN_PROFILE = RlimitProfile(nproc=1024, nofile=4096)


MB = 1024 * 1024


def limits_from_environment() -> tuple[int | None, int | None]:
    """`(max_file_mb, memory_limit_mb)` que o `bwrap` passou ao jail pelas
    variáveis `VECTORA_SANDBOX_MAX_FILE_MB` e `VECTORA_SANDBOX_MEMORY_MB`."""

    def read(name: str) -> int | None:
        value = os.environ.get(name, "")
        return int(value) if value.isdigit() and int(value) > 0 else None

    return read("VECTORA_SANDBOX_MAX_FILE_MB"), read("VECTORA_SANDBOX_MEMORY_MB")


def apply_rlimits(
    lockdown: bool,
    *,
    max_file_mb: int | None = None,
    memory_limit_mb: int | None = None,
) -> None:
    """Aplica `RLIMIT_NPROC`/`RLIMIT_NOFILE`/`RLIMIT_CORE=0` no processo
    atual (herdado por qualquer filho exec'd depois) — nunca levanta:
    plataforma sem `resource` (Windows) ou sem permissão pra baixar um
    limite já reduzido degrada com aviso, não quebra o worker.

    `max_file_mb` limita o tamanho de cada arquivo gravado (`RLIMIT_FSIZE`) e
    `memory_limit_mb` o espaço de endereçamento (`RLIMIT_AS`, só quando
    declarado: runtimes como o V8 reservam muito endereço virtual)."""
    try:
        import resource
    except ImportError:
        logger.debug(
            "sandbox: módulo 'resource' indisponível (não-POSIX) — sem rlimits"
        )
        return

    profile = LOCKDOWN_PROFILE if lockdown else NORMAL_PROFILE
    limits: list[tuple[str, int]] = [
        ("RLIMIT_NPROC", profile.nproc),
        ("RLIMIT_NOFILE", profile.nofile),
        ("RLIMIT_CORE", 0),
    ]
    if max_file_mb:
        limits.append(("RLIMIT_FSIZE", max_file_mb * MB))
    if memory_limit_mb:
        limits.append(("RLIMIT_AS", memory_limit_mb * MB))
    for limit_name, value in limits:
        limit = getattr(resource, limit_name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            logger.warning(
                "sandbox: falha ao aplicar %s=%s — worker segue sem esse limite",
                limit_name,
                value,
            )
