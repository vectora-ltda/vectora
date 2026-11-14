"""Parser da política de sandbox (`vectora.toml`, seção `[sandbox]`).

Modelo de ameaça: supply-chain attack via `terminal`/`npm install`
malicioso, exfiltração de credenciais (`~/.ssh`, `~/.aws`, vault local do
Vectora), modificação destrutiva fora do workspace confiado. Referência de
design: `akitaonrails/ai-jail` (Rust/bubblewrap) — reimplementação própria,
nunca forkada, com nomes de campo ao estilo Vectora.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MASK = (".env", "**/*.pem", "**/.ssh/**", "**/.aws/**")


@dataclass(frozen=True)
class SandboxPolicy:
    """Política resolvida — sempre um valor concreto, nunca `None`; ausência
    de `vectora.toml`/seção `[sandbox]` já vira `enabled=False` aqui."""

    enabled: bool = False
    backend: str = "local"
    rw_paths: tuple[str, ...] = ()
    ro_paths: tuple[str, ...] = ()
    mask: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_MASK)
    no_gpu: bool = True
    lockdown: bool = False
    allow_tcp_ports: tuple[int, ...] = ()
    docker_image: str | None = None
    remote_host: str | None = None
    ssh_key_id: str | None = None
    #: Nomes de variáveis do host que o jail pode herdar (além do mínimo).
    env_pass: tuple[str, ...] = ()
    #: Libera a escrita em `.git/hooks` e `.git/config` dentro do jail.
    git_writable: bool = False
    #: Recusa iniciar o jail quando seccomp ou Landlock não puderem ser aplicados.
    require_full_isolation: bool = False


DISABLED_POLICY = SandboxPolicy(enabled=False)

# Habilitada por padrão quando o ambiente já suporta isolamento nativo e o
# workspace não tem `vectora.toml` nenhum — o worker jailado ainda tem RW
# completo dentro do próprio workspace (rw_paths é só pra paths extras fora
# dele), então isso não quebra operação normal, só adiciona a defesa em
# profundidade (Landlock/seccomp/rlimits) sem exigir opt-in manual por
# workspace quando o ambiente já suporta. Duas checagens: WSL2
# (`detect_wsl2()`, cache populado por `warm_wsl2_cache()` no startup — exige
# spawnar `wsl.exe`, por isso é async/cacheado) no Windows; `bwrap` (Linux
# nativo) via `shutil.which`, seguro de chamar direto no hot path síncrono.
# Windows sem WSL2 elegível mantém o teto atual (não é seguro auto-instalar
# WSL2 silenciosamente). macOS nunca se auto-habilita: o worker persistente
# (`WorkspaceJailManager`) usa `bwrap` em todo host não Windows, então uma
# política automática ali falharia na primeira ação jailada.
AUTO_ENABLED_POLICY = SandboxPolicy(enabled=True, backend="local")

# Política mais restritiva possível — usada quando o parse falha (fail-closed:
# preferimos negar execução a rodar sem proteção por um TOML corrompido).
LOCKED_DOWN_POLICY = SandboxPolicy(
    enabled=True, backend="local", lockdown=True, rw_paths=()
)


def parse_policy(vectora_toml_path: Path) -> SandboxPolicy:
    """Lê `[sandbox]` de `vectora.toml`.

    Ausência de arquivo ou de seção `[sandbox]`: sandbox desabilitado
    (comportamento atual do produto é preservado, sem regressão — isto não
    é "fail closed", é "não opt-in"). TOML malformado ou campo com tipo
    errado, uma vez que `[sandbox]` EXISTE: fail-closed — devolve a
    política mais restritiva em vez de propagar exceção ou (pior) ignorar
    o erro e desabilitar a proteção silenciosamente.
    """
    if not vectora_toml_path.is_file():
        auto_enable = _wsl2_eligible_sync() or _native_sandbox_available_sync()
        return AUTO_ENABLED_POLICY if auto_enable else DISABLED_POLICY
    try:
        raw = tomllib.loads(vectora_toml_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(
            "sandbox: %s malformado — fail-closed (negando execução)",
            vectora_toml_path,
        )
        return LOCKED_DOWN_POLICY

    section = raw.get("sandbox")
    if not isinstance(section, dict):
        return DISABLED_POLICY

    try:
        return SandboxPolicy(
            enabled=bool(section.get("enabled", True)),
            backend=str(section.get("backend", "local")),
            rw_paths=tuple(str(p) for p in section.get("rw_paths", [])),
            ro_paths=tuple(str(p) for p in section.get("ro_paths", [])),
            mask=tuple(str(p) for p in section.get("mask", _DEFAULT_MASK)),
            no_gpu=bool(section.get("no_gpu", True)),
            lockdown=bool(section.get("lockdown", False)),
            allow_tcp_ports=tuple(int(p) for p in section.get("allow_tcp_ports", [])),
            docker_image=(
                str(section["docker_image"]) if "docker_image" in section else None
            ),
            remote_host=(
                str(section["remote_host"]) if "remote_host" in section else None
            ),
            ssh_key_id=(
                str(section["ssh_key_id"]) if "ssh_key_id" in section else None
            ),
            env_pass=tuple(str(n) for n in section.get("env_pass", [])),
            git_writable=bool(section.get("git_writable", False)),
            require_full_isolation=bool(section.get("require_full_isolation", False)),
        )
    except Exception:
        logger.warning(
            "sandbox: campo inválido em [sandbox] de %s — fail-closed",
            vectora_toml_path,
        )
        return LOCKED_DOWN_POLICY


class _Unset:
    """Sentinel — distingue "ainda não checado" de "checado, sem distro" (None)."""


_UNSET = _Unset()
_wsl2_distro_cache: str | _Unset | None = _UNSET


def _wsl2_eligible_sync() -> bool:
    """Leitura síncrona do cache de `detect_wsl2()` — populado no startup do
    backend via `warm_wsl2_cache()`. Antes da primeira detecção rodar (cache
    ainda `_UNSET`), retorna `False`: mesmo comportamento de hoje (sandbox
    não se auto-habilita até a detecção real terminar), nunca um falso
    positivo por checar cedo demais."""
    return not isinstance(_wsl2_distro_cache, _Unset) and _wsl2_distro_cache is not None


def _native_sandbox_available_sync() -> bool:
    """Linux (`bwrap`) — diferente do WSL2, não exige spawnar processo
    externo pra decidir (`detect_wsl2()` roda `wsl.exe`), então não precisa
    de cache nem de warm-up assíncrono no startup: `shutil.which` é uma
    checagem de filesystem, segura de chamar direto no hot path síncrono de
    `parse_policy()`. Fora do Linux devolve `False`: o worker persistente só
    sabe isolar via `bwrap` (ou WSL2 no Windows, checado à parte)."""
    if sys.platform == "linux":
        return shutil.which("bwrap") is not None
    return False


async def warm_wsl2_cache() -> None:
    """Roda `detect_wsl2()` uma vez no startup do backend pra popular o cache
    antes de qualquer tool call — sem isso, `parse_policy()` (síncrono, chamado
    no hot path de file_edit/file_write/terminal) sempre veria o cache
    `_UNSET` e nunca auto-habilitaria o sandbox na primeira leitura real."""
    await detect_wsl2()


# VMs internas do Docker Desktop — nomes reservados e conhecidos, sem rootfs
# de uso geral (sem `/bin/sh` utilizável por um worker jailado). Nunca são
# uma distro WSL2 elegível pro sandbox, mesmo aparecendo em `wsl.exe -l -v`.
_DOCKER_RESERVED_DISTROS = {"docker-desktop", "docker-desktop-data"}


async def detect_wsl2() -> str | None:
    """Detecta uma distro WSL2 elegível (kernel Linux real, WSL versão 2,
    de uso geral — nunca a VM interna do Docker Desktop) via `wsl.exe -l -v`.
    É o único caminho real de sandbox no Windows: bwrap não roda nativo (sem
    namespace/mount API equivalente), e WSL2 é exatamente o caminho que o
    `ai-jail` original usa nesse SO — Docker não entra nessa equação.

    Cacheado por processo — o ambiente (WSL instalado ou não) não muda em
    runtime. Retorna `None` (não levanta) se `wsl.exe` não existir, falhar,
    nenhuma distro estiver em WSL2, ou a(s) única(s) candidata(s) forem
    reservadas do Docker Desktop / sem `/bin/sh` utilizável.
    """
    global _wsl2_distro_cache
    if not isinstance(_wsl2_distro_cache, _Unset):
        return _wsl2_distro_cache

    distro = await _detect_wsl2_uncached()
    _wsl2_distro_cache = distro
    return distro


async def _list_wsl2_candidates() -> list[str]:
    """Lista os nomes de distro WSL2 (versão 2) elegíveis, na ordem: a
    distro default primeiro (se ela for WSL2 e de uso geral), depois as
    demais na ordem em que `wsl.exe -l -v` as lista. Exclui as VMs
    reservadas do Docker Desktop. Lista vazia (nunca levanta) se `wsl.exe`
    não existir/falhar ou nenhuma distro WSL2 de uso geral existir.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe",
            "-l",
            "-v",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except (FileNotFoundError, TimeoutError, OSError):
        return []
    if proc.returncode != 0:
        return []

    # `wsl.exe -l -v` imprime UTF-16LE no Windows — decodificar como UTF-8
    # produz lixo intercalado com bytes nulos.
    try:
        text = stdout_b.decode("utf-16-le")
    except UnicodeDecodeError:
        text = stdout_b.decode("utf-8", errors="replace")

    default_distro: str | None = None
    others: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("*").strip()
        if not stripped or stripped.upper().startswith("NAME"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        name, version = parts[0], parts[-1]
        if version != "2" or name.lower() in _DOCKER_RESERVED_DISTROS:
            continue
        if line.strip().startswith("*"):
            default_distro = name
        else:
            others.append(name)
    return ([default_distro] if default_distro else []) + others


async def _distro_has_usable_shell(name: str) -> bool:
    """Testa `/bin/sh -c true` dentro da distro — confirma que ela tem um
    rootfs de uso geral utilizável pelo worker jailado, não só que aparece
    listada. Nunca levanta; qualquer falha (timeout, distro corrompida,
    `wsl.exe` sumiu no meio do caminho) vira `False`."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe",
            "-d",
            name,
            "--",
            "test",
            "-x",
            "/bin/sh",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except (FileNotFoundError, TimeoutError, OSError):
        return False
    return proc.returncode == 0


async def _detect_wsl2_uncached() -> str | None:
    for name in await _list_wsl2_candidates():
        if await _distro_has_usable_shell(name):
            return name
    return None


async def _wsl_available() -> bool:
    """`wsl.exe --status` só pra confirmar que o binário existe e responde —
    não usa a saída (formato muda entre versões do Windows)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe",
            "--status",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except (FileNotFoundError, TimeoutError, OSError):
        return False
    return True


async def wsl2_diagnostic() -> str:
    """Explica por que `detect_wsl2()` não achou uma distro elegível —
    devolve um código curto (não texto pronto: tradução vive no frontend
    via `m()`, mesmo padrão de erro tipado do resto do produto). Só faz
    sentido chamar depois de `detect_wsl2()` já ter retornado `None`.
    """
    if not await _wsl_available():
        return "wsl_not_installed"
    candidates = await _list_wsl2_candidates()
    if not candidates:
        return "no_general_purpose_distro"
    return "distro_missing_shell"
