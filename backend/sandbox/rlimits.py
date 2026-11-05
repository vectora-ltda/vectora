"""Resource limits (POSIX `rlimit`) aplicados ao worker jailado antes do
loop RPC começar — contém fork bombs e exaustão de descritores/arquivos
que os namespaces do bwrap sozinhos não limitam. Mesmo espírito do
`ai-jail` original (2 perfis, normal e lockdown), via `resource` (stdlib,
sem dependência nova). `resource` só existe em POSIX — no-op silencioso
em qualquer outro SO (o worker em si só roda em Linux/WSL2 hoje)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RlimitProfile:
    nproc: int
    nofile: int


NORMAL_PROFILE = RlimitProfile(nproc=4096, nofile=65536)
LOCKDOWN_PROFILE = RlimitProfile(nproc=1024, nofile=4096)


def apply_rlimits(lockdown: bool) -> None:
    """Aplica `RLIMIT_NPROC`/`RLIMIT_NOFILE`/`RLIMIT_CORE=0` no processo
    atual (herdado por qualquer filho exec'd depois) — nunca levanta:
    plataforma sem `resource` (Windows) ou sem permissão pra baixar um
    limite já reduzido degrada com aviso, não quebra o worker."""
    try:
        import resource
    except ImportError:
        logger.debug(
            "sandbox: módulo 'resource' indisponível (não-POSIX) — sem rlimits"
        )
        return

    profile = LOCKDOWN_PROFILE if lockdown else NORMAL_PROFILE
    for limit_name, value in (
        ("RLIMIT_NPROC", profile.nproc),
        ("RLIMIT_NOFILE", profile.nofile),
        ("RLIMIT_CORE", 0),
    ):
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
