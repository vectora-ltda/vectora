"""O que o isolamento do workspace realmente garante, dimensão por dimensão.

A política diz o que se quer; o relatório diz o que está em vigor. Com um worker
vivo, o estado vem dele (seccomp carregado pelo bwrap; Landlock, `no_new_privs`
e rlimits aplicados pelo próprio worker). Sem worker, o relatório mostra o que
este host consegue aplicar. Nunca declara "efetivo" o que não foi confirmado.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import Literal

from backend.sandbox.policy import SandboxPolicy, effective_network

State = Literal["effective", "partial", "off", "unknown"]
Level = Literal["effective", "partial", "soft", "off"]


@dataclass(frozen=True)
class Dimension:
    name: str
    state: State
    detail: str = ""


@dataclass(frozen=True)
class IsolationReport:
    level: Level
    #: `True` quando o estado vem de um worker em execução.
    live: bool
    dimensions: tuple[Dimension, ...]


def _host_can_isolate() -> bool:
    return sys.platform == "linux" and shutil.which("bwrap") is not None


def _seccomp_available() -> bool:
    from backend.sandbox.linux import _load_seccomp

    return _load_seccomp() is not None


def isolation_report(
    policy: SandboxPolicy,
    *,
    worker_status: dict[str, bool] | None = None,
    worker_enforced: bool | None = None,
) -> IsolationReport:
    """`worker_status` é o `isolation_status()` de um worker vivo (ou `None`);
    `worker_enforced` diz se esse worker roda sob isolamento nativo."""
    if not policy.enabled:
        return IsolationReport("off", False, ())

    live = worker_status is not None
    network = effective_network(policy)
    if live and worker_enforced is False:
        # Worker "soft" (Windows sem WSL2): processo comum com ambiente limpo.
        return IsolationReport(
            "soft",
            True,
            (
                Dimension("filesystem", "off", "sem namespaces de arquivos"),
                Dimension("network", "off", "sem filtro de rede"),
                Dimension("environment", "effective", "ambiente reduzido"),
            ),
        )

    can_isolate = _host_can_isolate()
    if live:
        status = worker_status or {}
        seccomp, landlock = status.get("seccomp"), status.get("landlock")
        no_new_privs, rlimits = status.get("no_new_privs"), status.get("rlimits")
    else:
        from backend.sandbox.landlock import landlock_abi_version

        seccomp = can_isolate and _seccomp_available()
        landlock = can_isolate and landlock_abi_version() > 0
        no_new_privs = rlimits = can_isolate

    def flag(ok: bool | None, missing: str) -> tuple[State, str]:
        if ok:
            return "effective", ""
        return ("partial" if live else "unknown", missing)

    seccomp_state, seccomp_detail = flag(
        seccomp, "libseccomp ausente: sem filtro de syscalls"
    )
    landlock_state, landlock_detail = flag(landlock, "kernel sem Landlock")
    nnp_state, nnp_detail = flag(no_new_privs, "no_new_privs não confirmado")
    rl_state, rl_detail = flag(rlimits, "limites de recursos não confirmados")

    if network == "open":
        network_dim = Dimension("network", "partial", "rede do host aberta, sem filtro")
    elif network == "off":
        network_dim = Dimension("network", "effective", "sem rede")
    else:
        network_dim = Dimension("network", "effective", "só os destinos permitidos")

    dimensions = (
        Dimension(
            "filesystem",
            "effective" if can_isolate or live else "off",
            "namespaces do bwrap" if can_isolate or live else "bwrap indisponível",
        ),
        network_dim,
        Dimension("environment", "effective", "ambiente limpo (allowlist)"),
        Dimension("seccomp", seccomp_state, seccomp_detail),
        Dimension("landlock", landlock_state, landlock_detail),
        Dimension("no_new_privs", nnp_state, nnp_detail),
        Dimension("resources", rl_state, rl_detail),
        Dimension(
            "git",
            "effective" if not policy.git_writable else "partial",
            "hooks e config somente leitura"
            if not policy.git_writable
            else ".git/hooks e .git/config graváveis",
        ),
    )
    if not (can_isolate or live):
        return IsolationReport("off", live, dimensions)
    states = {d.state for d in dimensions}
    level: Level = "effective" if states == {"effective"} else "partial"
    return IsolationReport(level, live, dimensions)
