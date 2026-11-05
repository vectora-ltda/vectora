"""``vectora doctor`` — encontra e limpa sidecars órfãos (nats-server) e
diagnostica o Sandbox no Windows.

Varre TODOS os processos ``nats-server`` do sistema por nome de imagem,
não só os PIDs conhecidos pelo pid file de
``backend/scheduling/nats_sidecar.py`` — assim também encontra órfãos
que ficaram fora desse rastreamento.

Também reporta o status do backend de sandbox nativo da plataforma: no
Windows, o caminho real é WSL2 (bwrap não roda nativo nesse SO, e Docker
não é o caminho certo pra isso, ver ``backend/sandbox/workspace_jail.py``)
— WSL2 instalado ou não, distro WSL2 elegível encontrada, e se ``bwrap``
está instalado dentro dela. No macOS, o backend ``"macos"`` (Seatbelt via
``sandbox-exec``) — disponível ou não.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess  # nosec B404 — só consulta/mata processos já identificados por nome, sem shell
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


def _find_nats_server_pids_win32() -> list[int]:
    tasklist = shutil.which("tasklist")
    if tasklist is None:
        return []
    try:
        out = subprocess.run(  # noqa: S603  # nosec B603
            [tasklist, "/NH", "/FI", "IMAGENAME eq nats-server.exe"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in (out.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def _find_nats_server_pids_posix() -> list[int]:
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return []
    try:
        out = subprocess.run(  # noqa: S603  # nosec B603
            [pgrep, "-f", "nats-server"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    return [int(p) for p in (out.stdout or "").split() if p.isdigit()]


def find_nats_server_pids() -> list[int]:
    """Varre o sistema por nome de imagem — não depende de nenhum PID
    file conhecido, então acha órfãos que o rastreamento normal (pid
    file de ``nats_sidecar.py``) não cobre."""
    if sys.platform == "win32":
        return _find_nats_server_pids_win32()
    return _find_nats_server_pids_posix()


async def _report_sandbox_status(console: Console) -> None:
    """Diagnóstico do sandbox por plataforma.

    Linux: `bwrap` é nativo, sempre disponível — reporta só se
    `singularity`/`apptainer` também está instalado, como alternativa em
    ambientes HPC/cluster sem daemon Docker. macOS: `backend = "macos"`
    (Seatbelt via `sandbox-exec`) é o caminho nativo no host — reporta se o
    binário está disponível (interface legada da Apple, pode sumir numa
    versão futura sem aviso). Windows: depende de WSL2, checado abaixo.
    """
    if sys.platform == "darwin":
        console.print("\n[bold]Sandbox:[/bold]")
        if shutil.which("sandbox-exec") is not None:
            console.print(
                "[green]✔ sandbox-exec disponível[/green] — use "
                '`backend = "macos"` no [sandbox] do vectora.toml para '
                "isolamento nativo no host (Seatbelt/SBPL)."
            )
        else:
            console.print(
                "[yellow]✖ sandbox-exec não encontrado neste macOS — "
                "backend 'macos' indisponível. Use `backend = \"docker\"` "
                "(ou 'ssh'/'modal') no [sandbox] do vectora.toml.[/yellow]"
            )
        return
    if sys.platform == "linux":
        singularity_bin = shutil.which("singularity") or shutil.which("apptainer")
        if singularity_bin is not None:
            console.print("\n[bold]Sandbox:[/bold]")
            console.print(
                f"[green]✔ {singularity_bin} disponível[/green] — use "
                '`backend = "singularity"` no [sandbox] do vectora.toml como '
                "alternativa ao bwrap em ambientes HPC/cluster sem daemon "
                "Docker."
            )
        return
    if sys.platform != "win32":
        return

    from backend.sandbox.policy import detect_wsl2
    from backend.sandbox.workspace_jail import _bwrap_available_in_distro

    console.print("\n[bold]Sandbox:[/bold]")
    distro = await detect_wsl2()
    if distro is None:
        console.print(
            "[yellow]✖ WSL2 não encontrado — o sandbox não roda nativo no "
            "Windows (namespaces/mount API não existem aqui). Instale com "
            "`wsl --install`, reinicie e crie uma distro Linux.[/yellow]"
        )
        return
    console.print(f"[green]✔ WSL2 disponível[/green] (distro: {distro})")

    if await _bwrap_available_in_distro(distro):
        console.print(f"[green]✔ bwrap instalado dentro de '{distro}'[/green]")
    else:
        console.print(
            f"[yellow]✖ bwrap não está instalado dentro de '{distro}' — "
            f"rode `wsl -d {distro} -- sudo apt install bubblewrap` (ou o "
            "gerenciador de pacotes da sua distro).[/yellow]"
        )


def run_doctor(args: argparse.Namespace) -> None:
    from rich.console import Console

    from backend.scheduling.nats_sidecar import kill_orphan_pid

    console = Console()
    asyncio.run(_report_sandbox_status(console))

    pids = find_nats_server_pids()

    if not pids:
        console.print("[green]✔ Nenhum sidecar nats-server órfão encontrado.[/green]")
        return

    console.print(
        f"[yellow]Encontrado(s) {len(pids)} processo(s) nats-server: {pids}[/yellow]"
    )

    yes = getattr(args, "yes", False)
    if not yes:
        answer = input("Finalizar todos? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "s", "sim"):
            console.print("Cancelado — nenhum processo finalizado.")
            return

    for pid in pids:
        kill_orphan_pid(pid)

    console.print(f"[green]✔ {len(pids)} processo(s) finalizado(s).[/green]")
