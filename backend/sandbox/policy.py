"""Parser da política de sandbox (`vectora.toml`, seção `[sandbox]`).

Modelo de ameaça: supply-chain attack via `terminal`/`npm install`
malicioso, exfiltração de credenciais (`~/.ssh`, `~/.aws`, vault local do
Vectora), modificação destrutiva fora do workspace confiado. Referência de
design: `akitaonrails/ai-jail` (Rust/bubblewrap) — reimplementação própria,
nunca forkada, com nomes de campo ao estilo Vectora.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

logger = logging.getLogger(__name__)

DEFAULT_MASK = (".env", "**/*.pem", "**/.ssh/**", "**/.aws/**")


NetworkMode = Literal["off", "filtered", "open"]


@dataclass(frozen=True)
class SandboxPolicy:
    """Política resolvida — sempre um valor concreto, nunca `None`; ausência
    de `vectora.toml`/seção `[sandbox]` já vira `enabled=False` aqui."""

    enabled: bool = False
    backend: str = "local"
    rw_paths: tuple[str, ...] = ()
    ro_paths: tuple[str, ...] = ()
    mask: tuple[str, ...] = field(default_factory=lambda: DEFAULT_MASK)
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
    #: Rede do jail: `off` (sem rede), `filtered` (só os destinos permitidos, por
    #: um proxy fora do jail) ou `open` (rede do host, sem filtro).
    network: NetworkMode = "filtered"
    #: Destinos extras (`host` ou `*.dominio`) liberados no modo `filtered`.
    allow_hosts: tuple[str, ...] = ()
    #: Inclui os registries de pacotes (pip, npm, cargo, go, maven, composer e
    #: GitHub) na lista do modo `filtered`.
    registry_hosts: bool = True
    #: Maior arquivo que o jail pode gravar (MB, `RLIMIT_FSIZE`).
    max_file_mb: int = 4096
    #: Teto de memória por processo (MB, `RLIMIT_AS`); `None` não limita.
    memory_limit_mb: int | None = None
    #: Tamanho máximo dos tmpfs do jail (`/tmp` e `$HOME`), em MB.
    tmp_size_mb: int = 1024
    #: Chaves do `[sandbox]` do repositório que tentaram ampliar o acesso e
    #: foram ignoradas (só a configuração do usuário amplia).
    ignored_repo_keys: tuple[str, ...] = ()


#: Destino permitido: `dominio.com` ou `*.dominio.com` (sem esquema, porta nem caminho).
HOST_PATTERN = re.compile(
    r"(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+",
    re.IGNORECASE,
)

NETWORK_MODES: tuple[str, ...] = ("off", "filtered", "open")

#: Registries de dependências liberados por padrão no modo `filtered`, para que
#: `pip install`, `npm install` e `cargo build` funcionem sem configuração.
REGISTRY_HOSTS: tuple[str, ...] = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "proxy.golang.org",
    "sum.golang.org",
    "repo1.maven.org",
    "repo.maven.apache.org",
    "repo.clojars.org",
    "repo.packagist.org",
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
)


def effective_network(policy: SandboxPolicy) -> str:
    """Modo de rede que vale: o lockdown sempre corta a rede."""
    return "off" if policy.lockdown else policy.network


def effective_allow_hosts(policy: SandboxPolicy) -> tuple[str, ...]:
    """Destinos liberados no modo `filtered`: registries (se ligados) e extras."""
    base = REGISTRY_HOSTS if policy.registry_hosts else ()
    return tuple(dict.fromkeys((*base, *policy.allow_hosts)))


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


def _list_field(
    section: dict[str, Any],
    key: str,
    convert: type,
    default: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    """Campo de lista do `[sandbox]`. Uma string no lugar da lista (que o Python
    percorreria letra a letra) é tipo errado: levanta e cai na falha fechada."""
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, list):
        raise TypeError(f"[sandbox].{key} deve ser uma lista")
    return tuple(convert(item) for item in value)


def _positive_int(section: dict[str, Any], key: str, default: int) -> int:
    """Inteiro positivo do `[sandbox]`; booleano, texto ou valor não positivo
    é tipo errado (falha fechada)."""
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TypeError(f"[sandbox].{key} deve ser um inteiro positivo")
    return value


def _network_field(section: dict[str, Any]) -> NetworkMode:
    value = section.get("network", "filtered")
    if value not in NETWORK_MODES:
        raise ValueError(f"[sandbox].network deve ser um de {NETWORK_MODES}")
    return cast("NetworkMode", value)


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
            rw_paths=_list_field(section, "rw_paths", str),
            ro_paths=_list_field(section, "ro_paths", str),
            mask=_list_field(section, "mask", str, DEFAULT_MASK),
            no_gpu=bool(section.get("no_gpu", True)),
            lockdown=bool(section.get("lockdown", False)),
            allow_tcp_ports=_list_field(section, "allow_tcp_ports", int),
            docker_image=(
                str(section["docker_image"]) if "docker_image" in section else None
            ),
            remote_host=(
                str(section["remote_host"]) if "remote_host" in section else None
            ),
            ssh_key_id=(
                str(section["ssh_key_id"]) if "ssh_key_id" in section else None
            ),
            env_pass=_list_field(section, "env_pass", str),
            network=_network_field(section),
            allow_hosts=_list_field(section, "allow_hosts", str),
            registry_hosts=bool(section.get("registry_hosts", True)),
            max_file_mb=_positive_int(section, "max_file_mb", 4096),
            memory_limit_mb=(
                _positive_int(section, "memory_limit_mb", 0) or None
                if "memory_limit_mb" in section
                else None
            ),
            tmp_size_mb=_positive_int(section, "tmp_size_mb", 1024),
            git_writable=bool(section.get("git_writable", False)),
            require_full_isolation=bool(section.get("require_full_isolation", False)),
        )
    except Exception:
        logger.warning(
            "sandbox: campo inválido em [sandbox] de %s — fail-closed",
            vectora_toml_path,
        )
        return LOCKED_DOWN_POLICY


# ── política confiável ──────────────────────────────────────────────────────
#
# O `vectora.toml` mora no repositório, que pode ter sido clonado de qualquer
# lugar. A política que amplia o acesso (caminhos graváveis, variáveis do
# ambiente, `.git` gravável, portas, backend) vem de um arquivo do usuário, fora
# do workspace; o arquivo do repositório só pode restringir.

TRUSTED_POLICY_DIR_ENV = "VECTORA_SANDBOX_POLICY_DIR"


def trusted_policy_dir() -> Path:
    """Pasta das políticas do usuário (fora de qualquer workspace)."""
    override = os.environ.get(TRUSTED_POLICY_DIR_ENV)
    if override:
        return Path(override)
    from backend.settings import settings

    return Path(settings.vectora_home) / "sandbox-policies"


def trusted_policy_path(root: Path) -> Path:
    """Arquivo da política do usuário para o workspace em `root`."""
    digest = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()
    return trusted_policy_dir() / f"{digest[:24]}.toml"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def policy_to_toml(policy: SandboxPolicy) -> str:
    """`[sandbox]` equivalente a `policy`, no formato que `parse_policy` lê."""
    lines = ["[sandbox]"]
    fixed = (
        "enabled",
        "backend",
        "rw_paths",
        "ro_paths",
        "mask",
        "no_gpu",
        "lockdown",
        "allow_tcp_ports",
        "env_pass",
        "git_writable",
        "require_full_isolation",
        "network",
        "allow_hosts",
        "registry_hosts",
        "max_file_mb",
        "tmp_size_mb",
    )
    lines.extend(f"{name} = {_toml_value(getattr(policy, name))}" for name in fixed)
    optional = ("docker_image", "remote_host", "ssh_key_id", "memory_limit_mb")
    lines.extend(
        f"{name} = {_toml_value(getattr(policy, name))}"
        for name in optional
        if getattr(policy, name) is not None
    )
    return "\n".join(lines) + "\n"


def policy_digest(policy: SandboxPolicy) -> str:
    """Impressão digital da política efetiva (ignora as chaves só informativas)."""
    text = policy_to_toml(replace(policy, ignored_repo_keys=()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def write_trusted_policy(root: Path, policy: SandboxPolicy) -> Path:
    """Grava a política do usuário para o workspace, de forma atômica."""
    path = trusted_policy_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(policy_to_toml(policy), encoding="utf-8")
    temporary.replace(path)
    return path


_MALFORMED: Any = object()


def _read_repo_section(path: Path) -> dict[str, Any] | Any | None:
    """`[sandbox]` do repositório: `None` sem arquivo ou seção, `_MALFORMED` se
    o TOML ou a seção são inválidos."""
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _MALFORMED
    if "sandbox" not in raw:
        return None
    section = raw["sandbox"]
    return section if isinstance(section, dict) else None


def _narrowed(base: tuple[Any, ...], requested: tuple[Any, ...]) -> tuple[Any, ...]:
    """Só o que a política do usuário já permite e o repositório manteve."""
    return tuple(item for item in base if item in requested)


def _looser_memory(repo: int | None, base: int | None) -> bool:
    """O limite do repositório deixa passar mais memória que o do usuário."""
    if base is None:
        return False
    return repo is None or repo > base


def _stricter_memory(base: int | None, repo: int | None) -> int | None:
    if base is None:
        return repo
    return base if repo is None else min(base, repo)


_NETWORK_RANK = {"off": 0, "filtered": 1, "open": 2}

_logged_ignored: set[tuple[str, tuple[str, ...]]] = set()


def resolve_policy(root: Path) -> SandboxPolicy:
    """Política efetiva do workspace em `root`: a do usuário (ou o padrão do
    ambiente) com as restrições que o `vectora.toml` do repositório acrescenta.

    O repositório pode ligar o sandbox, ligar o lockdown, acrescentar máscaras,
    desligar GPU, exigir isolamento completo e estreitar caminhos, portas,
    variáveis e `.git`. Qualquer pedido que amplie o acesso, ou desligue o
    sandbox, é ignorado e registrado em `ignored_repo_keys`. Um `vectora.toml`
    malformado, como sempre, vira lockdown (falha fechada)."""
    root = Path(root)
    repo_toml = root / "vectora.toml"
    user_file = trusted_policy_path(root)
    if user_file.is_file():
        base = parse_policy(user_file)
    elif repo_toml.is_file():
        # Sem política do usuário, um `vectora.toml` sem `[sandbox]` continua
        # significando "não habilitado" (só a ausência do arquivo liga o padrão
        # automático do ambiente); o `[sandbox]` dele pode ligar, nunca ampliar.
        base = DISABLED_POLICY
    else:
        base = parse_policy(user_file)
    section = _read_repo_section(repo_toml)
    if section is None:
        return base
    if section is _MALFORMED:
        return replace(base, enabled=True, lockdown=True, rw_paths=())

    repo = parse_policy(root / "vectora.toml")
    if repo == LOCKED_DOWN_POLICY:
        return replace(base, enabled=True, lockdown=True, rw_paths=())

    ignored: list[str] = []
    if "enabled" in section and not repo.enabled and base.enabled:
        ignored.append("enabled")
    ignored.extend(
        key
        for key in ("backend", "docker_image", "remote_host", "ssh_key_id")
        if key in section and getattr(repo, key) != getattr(base, key)
    )
    ignored.extend(
        key
        for key in ("rw_paths", "ro_paths", "env_pass", "allow_tcp_ports")
        if key in section and set(getattr(repo, key)) - set(getattr(base, key))
    )
    if "git_writable" in section and repo.git_writable and not base.git_writable:
        ignored.append("git_writable")
    if (
        "network" in section
        and _NETWORK_RANK[repo.network] > _NETWORK_RANK[base.network]
    ):
        ignored.append("network")
    if "allow_hosts" in section and set(repo.allow_hosts) - set(base.allow_hosts):
        ignored.append("allow_hosts")
    if "registry_hosts" in section and repo.registry_hosts and not base.registry_hosts:
        ignored.append("registry_hosts")
    if "max_file_mb" in section and repo.max_file_mb > base.max_file_mb:
        ignored.append("max_file_mb")
    if "tmp_size_mb" in section and repo.tmp_size_mb > base.tmp_size_mb:
        ignored.append("tmp_size_mb")
    if "memory_limit_mb" in section and _looser_memory(
        repo.memory_limit_mb, base.memory_limit_mb
    ):
        ignored.append("memory_limit_mb")
    if "no_gpu" in section and not repo.no_gpu and base.no_gpu:
        ignored.append("no_gpu")
    if "lockdown" in section and not repo.lockdown and base.lockdown:
        ignored.append("lockdown")

    merged = replace(
        base,
        enabled=base.enabled or repo.enabled,
        lockdown=base.lockdown or repo.lockdown,
        no_gpu=base.no_gpu or repo.no_gpu,
        require_full_isolation=(
            base.require_full_isolation or repo.require_full_isolation
        ),
        mask=tuple(dict.fromkeys((*base.mask, *repo.mask))),
        rw_paths=(
            _narrowed(base.rw_paths, repo.rw_paths)
            if "rw_paths" in section
            else base.rw_paths
        ),
        ro_paths=(
            _narrowed(base.ro_paths, repo.ro_paths)
            if "ro_paths" in section
            else base.ro_paths
        ),
        env_pass=(
            _narrowed(base.env_pass, repo.env_pass)
            if "env_pass" in section
            else base.env_pass
        ),
        allow_tcp_ports=(
            _narrowed(base.allow_tcp_ports, repo.allow_tcp_ports)
            if "allow_tcp_ports" in section
            else base.allow_tcp_ports
        ),
        git_writable=(
            base.git_writable and repo.git_writable
            if "git_writable" in section
            else base.git_writable
        ),
        network=min(base.network, repo.network, key=_NETWORK_RANK.__getitem__)
        if "network" in section
        else base.network,
        allow_hosts=(
            _narrowed(base.allow_hosts, repo.allow_hosts)
            if "allow_hosts" in section
            else base.allow_hosts
        ),
        registry_hosts=(
            base.registry_hosts and repo.registry_hosts
            if "registry_hosts" in section
            else base.registry_hosts
        ),
        max_file_mb=min(base.max_file_mb, repo.max_file_mb)
        if "max_file_mb" in section
        else base.max_file_mb,
        tmp_size_mb=min(base.tmp_size_mb, repo.tmp_size_mb)
        if "tmp_size_mb" in section
        else base.tmp_size_mb,
        memory_limit_mb=(
            _stricter_memory(base.memory_limit_mb, repo.memory_limit_mb)
            if "memory_limit_mb" in section
            else base.memory_limit_mb
        ),
        ignored_repo_keys=tuple(ignored),
    )
    if ignored:
        marker = (str(root), tuple(ignored))
        if marker not in _logged_ignored:
            _logged_ignored.add(marker)
            logger.warning(
                "sandbox: %s tenta ampliar o acesso (%s) — ignorado; só a "
                "configuração do usuário amplia",
                root / "vectora.toml",
                ", ".join(ignored),
            )
    return merged


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
