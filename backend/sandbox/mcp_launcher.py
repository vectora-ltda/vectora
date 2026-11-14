"""Servidor MCP `stdio` dentro do jail.

O protocolo MCP é bidirecional por stdin/stdout, então o servidor roda sob o
`bwrap` como processo filho do cliente (`stdio_client`), e não pelo worker. O
jail é o mesmo do workspace: sistema somente leitura, `$HOME` vazio, sem as
chaves do backend, seccomp, Landlock, `no_new_privs`, limites de recursos e rede
filtrada pelo proxy de saída (só os registries e os destinos que o servidor
declarou). A pasta de trabalho é própria do servidor, em `~/.vectora`.
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.sandbox.dry_run import build_bwrap_command
from backend.sandbox.egress_proxy import egress_registry
from backend.sandbox.policy import SandboxPolicy, effective_network

logger = logging.getLogger(__name__)

#: Programas que moram nestas pastas já estão visíveis no jail.
_SYSTEM_PREFIXES = ("/usr/", "/bin/", "/sbin/", "/lib", "/opt/")


class McpSandboxUnavailableError(RuntimeError):
    """O jail para servidores MCP não pode ser iniciado neste computador."""


@dataclass(frozen=True)
class JailedLaunch:
    """O comando que o cliente MCP executa no lugar do servidor."""

    command: str
    args: list[str]
    #: Chave do proxy de saída deste servidor, para encerrá-lo junto.
    egress_key: str | None


def jail_available() -> bool:
    """O jail nativo (bwrap em Linux) existe aqui."""
    return sys.platform == "linux" and shutil.which("bwrap") is not None


def mcp_policy(allow_hosts: tuple[str, ...] = ()) -> SandboxPolicy:
    """Política dos servidores MCP: rede filtrada (registries e destinos do
    servidor), nada gravável além da pasta própria, sem `.git`."""
    return SandboxPolicy(
        enabled=True,
        network="filtered",
        allow_hosts=allow_hosts,
        registry_hosts=True,
        mask=(),
        require_full_isolation=False,
    )


def scratch_dir(server_name: str) -> Path:
    """Pasta de trabalho (gravável) do servidor, fora de qualquer workspace."""
    from backend.settings import settings

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", server_name) or "server"
    path = Path(settings.vectora_home) / "mcp-sandbox" / safe
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _program_prefix(program: str) -> str | None:
    """Pasta de instalação do programa quando ela não é do sistema (nvm, pyenv,
    uv…): o jail precisa enxergá-la, somente leitura, para executá-lo."""
    resolved = shutil.which(program)
    if resolved is None:
        raise McpSandboxUnavailableError(
            f"programa do servidor MCP não encontrado: {program}"
        )
    real = Path(resolved).resolve()
    if str(real).startswith(_SYSTEM_PREFIXES):
        return None
    home = Path.home().resolve()
    # `.../versions/node/v20/bin/node` -> `.../versions/node/v20`; um programa
    # solto numa pasta do usuário expõe só a pasta dele.
    prefix = real.parent.parent if real.parent.name == "bin" else real.parent
    if prefix in (home, Path("/"), home.parent):
        prefix = real.parent
    return str(prefix)


async def build_jailed_launch(
    server_name: str,
    command: str,
    args: list[str],
    *,
    allow_hosts: tuple[str, ...] = (),
) -> JailedLaunch:
    """Comando que inicia `command args` dentro do jail do servidor MCP."""
    if not jail_available():
        raise McpSandboxUnavailableError(
            "o jail para servidores MCP precisa de bwrap (Linux); instale-o ou "
            "configure um launcher de sandbox"
        )
    policy = mcp_policy(allow_hosts)
    workdir = scratch_dir(server_name)
    prefix = _program_prefix(command)

    egress_key: str | None = None
    egress_dir: str | None = None
    if effective_network(policy) == "filtered":
        egress_key = f"mcp:{server_name}"
        egress_dir = (await egress_registry.ensure(egress_key, policy)).directory

    bwrap = build_bwrap_command(
        policy,
        str(workdir),
        [command, *args],
        egress_dir=egress_dir,
        inherit_env=True,
        harden=True,
        extra_ro_paths=(prefix,) if prefix else (),
    )
    logger.info("mcp: servidor %s em jail (rede %s)", server_name, policy.network)
    return JailedLaunch(
        command=sys.executable,
        args=["-m", "backend.sandbox.jail_exec", "--", *bwrap],
        egress_key=egress_key,
    )


async def release(launch: JailedLaunch) -> None:
    """Encerra o proxy de saída do servidor."""
    if launch.egress_key is not None:
        await egress_registry.close(launch.egress_key)
