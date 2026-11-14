"""Serializa o comando `bwrap` a partir de uma `SandboxPolicy`, sem executar.

Usado tanto por uma futura flag de auditoria quanto pelos testes — permite
validar a lógica de montagem de mounts/flags sem precisar do binário
`bwrap` instalado (CI-friendly).
"""

from __future__ import annotations

import json
import os
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

from backend.sandbox.policy import SandboxPolicy

# `$HOME` dedicado do worker — tmpfs vazio, nunca o `$HOME` real do host
# (que não existe de qualquer forma dentro do mount namespace isolado, mas
# documenta a intenção: nenhum dotfile do usuário real vaza pro worker).
WORKER_HOME = "/home/vectora-worker"

# Denylist de syscalls perigosas (ptrace/module loading/bpf/namespace
# escape) — mesmo espírito do ai-jail original. Aplicada via seccomp-bpf
# real em `linux.py`; aqui só documentamos/expomos a lista pros testes.
DENIED_SYSCALLS: tuple[str, ...] = (
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "bpf",
    "init_module",
    "finit_module",
    "delete_module",
    "kexec_load",
    "kexec_file_load",
    "mount",
    "umount2",
    "pivot_root",
    "unshare",
    "setns",
    "personality",
    "reboot",
    # Superfícies de ataque ao kernel que nenhuma ferramenta de desenvolvimento
    # precisa: io_uring contorna filtros de syscall, userfaultfd e keyctl são
    # peças clássicas de exploração, perf_event_open expõe contadores do host.
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "userfaultfd",
    "keyctl",
    "add_key",
    "request_key",
    "perf_event_open",
    "open_by_handle_at",
    "swapon",
    "swapoff",
    "iopl",
    "ioperm",
    "acct",
    "settimeofday",
    "clock_settime",
    "adjtimex",
)

#: ``ioctl`` com ``TIOCSTI`` injeta teclas no terminal de controle; a sessão nova
#: do bwrap já o isola, e o filtro nega a chamada mesmo assim (defesa em camadas).
TIOCSTI = 0x5412

#: Flags de ``clone`` que criam namespaces: o jail já está num, e criar outro
#: dentro dele é o caminho usual de fuga. ``clone3`` não entra aqui: as flags
#: ficam numa estrutura que o seccomp não inspeciona, e o glibc o usa para criar
#: threads comuns.
CLONE_NAMESPACE_FLAGS: tuple[int, ...] = (
    0x00020000,  # CLONE_NEWNS
    0x02000000,  # CLONE_NEWCGROUP
    0x04000000,  # CLONE_NEWUTS
    0x08000000,  # CLONE_NEWIPC
    0x10000000,  # CLONE_NEWUSER
    0x20000000,  # CLONE_NEWPID
    0x40000000,  # CLONE_NEWNET
)

#: Pastas do sistema que o jail enxerga somente leitura. Em distros com ``/usr``
#: unificado, ``/bin``, ``/lib`` e ``/lib64`` são links para ``/usr``; sem
#: recriar esses links o carregador dinâmico (``/lib64/ld-linux-*``) não existe
#: e nenhum programa executa.
SYSTEM_PATHS: tuple[str, ...] = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/etc",
)

#: Variáveis que o jail herda do host além de ``HOME`` e ``PATH``. Chaves de
#: provider, tokens e o restante do ambiente do backend nunca entram.
SAFE_ENV_NAMES: tuple[str, ...] = ("LANG", "LC_ALL", "LC_CTYPE", "TZ")
JAIL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

#: Pastas graváveis dentro do jail além do workspace: o ``/tmp`` próprio, o
#: ``$HOME`` dedicado e os dispositivos (``/dev/null``, ``/dev/tty``).
BASE_RW_PATHS: tuple[str, ...] = ("/tmp", WORKER_HOME, "/dev")  # noqa: S108  # nosec B108


def _expand_mask_glob(pattern: str, workspace_dir: str) -> list[str]:
    """Expande um padrão de `mask` (glob, ex. `**/*.pem`) contra o
    workspace — devolve paths absolutos reais. Padrão sem `*`/`?`/`[`
    (ex. `.env`) é tratado como path literal relativo ao workspace, sem
    exigir que já exista (mascarar preventivamente é válido). Padrão com
    glob sem nenhum match devolve lista vazia — nunca é fatal, o caller
    só ignora."""
    has_glob = any(ch in pattern for ch in "*?[")
    base = Path(workspace_dir)
    if not has_glob:
        return [str(base / pattern)]
    return [str(m.resolve()) for m in base.glob(pattern)]


def _resolver_run_dir() -> str | None:
    """Pasta em ``/run`` para onde ``/etc/resolv.conf`` aponta (systemd-resolved):
    sem ela o jail com rede não resolve nomes."""
    try:
        real = Path("/etc/resolv.conf").resolve(strict=True)
    except OSError:
        return None
    if not str(real).startswith("/run/"):
        return None
    return str(real.parent)


def system_mounts() -> list[str]:
    """Argumentos do bwrap que expõem o sistema somente leitura, reproduzindo os
    links simbólicos do host (``/bin -> usr/bin``) em vez de montá-los."""
    argv: list[str] = []
    for path in SYSTEM_PATHS:
        entry = Path(path)
        if entry.is_symlink():
            argv += ["--symlink", str(entry.readlink()), path]
        elif entry.exists():
            argv += ["--ro-bind", path, path]
    resolver = _resolver_run_dir()
    if resolver:
        argv += ["--ro-bind", resolver, resolver]
    return argv


def system_read_paths() -> list[str]:
    """Pastas reais do sistema para o Landlock do worker (links já resolvidos)."""
    resolved: list[str] = []
    for path in SYSTEM_PATHS:
        entry = Path(path)
        if entry.exists():
            real = str(entry.resolve())
            if real not in resolved:
                resolved.append(real)
    resolver = _resolver_run_dir()
    if resolver:
        resolved.append(resolver)
    return resolved


def _interpreter_aliases() -> list[tuple[str, str]]:
    """Pares (pasta real, pasta como o link a cita) do interpretador: o ``python``
    do ambiente virtual costuma apontar para um nome estável
    (``.../python/cpython-3.13-linux/bin/python``) que é um link para a pasta real
    (``.../cpython-3.13.18-linux``); o jail precisa enxergar o nome citado."""
    pairs: list[tuple[str, str]] = []
    current = Path(sys.executable)
    for _ in range(8):
        if not current.is_symlink():
            break
        target = Path(os.path.normpath(current.parent / current.readlink()))
        cited = str(target.parent.parent)
        real = str(Path(cited).resolve())
        if cited != real and Path(real).is_dir():
            pairs.append((real, cited))
        current = target
    return pairs


def python_runtime_paths() -> tuple[str | tuple[str, str], ...]:
    """Pastas somente leitura que o worker precisa para iniciar o Python: o
    ambiente virtual, o Python base (com o nome que os links citam) e o pacote
    ``backend``. O restante do repositório (``.env``, ``tests``) não é exposto.
    Um item é a pasta (montada no mesmo caminho) ou o par (origem, destino)."""
    import backend

    candidates = [sys.prefix, sys.base_prefix, str(Path(backend.__file__).parent)]
    candidates.extend(sysconfig.get_paths()[key] for key in ("purelib", "platlib"))
    paths: list[str | tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        real = str(Path(candidate).resolve())
        if not Path(real).is_dir() or real in seen:
            continue
        if any(
            real == system or real.startswith(system + "/")
            for system in ("/usr", "/etc")
        ):
            continue
        seen.add(real)
        paths.append(real)
    for real, cited in _interpreter_aliases():
        paths.append((real, cited))
    return tuple(paths)


def _runtime_real(path: str | tuple[str, str]) -> str:
    return path if isinstance(path, str) else path[0]


def python_path_for(runtime_paths: Sequence[str | tuple[str, str]]) -> str:
    """``PYTHONPATH`` do worker: a pasta que contém o pacote ``backend``."""
    for entry in runtime_paths:
        path = _runtime_real(entry)
        if Path(path).name == "backend" and (Path(path) / "sandbox").is_dir():
            return str(Path(path).parent)
    return ""


def jail_environment(policy: SandboxPolicy, pythonpath: str = "") -> dict[str, str]:
    """Ambiente mínimo do jail: o que o shell e as ferramentas precisam para
    funcionar mais as variáveis que o usuário declarou em ``env_pass``."""
    env = {
        "PATH": JAIL_PATH,
        "HOME": WORKER_HOME,
        "LANG": "C.UTF-8",
        "TERM": "xterm-256color",
    }
    for name in (*SAFE_ENV_NAMES, *policy.env_pass):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return env


def git_protection_mounts(workspace_dir: str) -> list[str]:
    """Deixa ``.git/hooks`` e ``.git/config`` somente leitura: um hook ou um
    ``core.fsmonitor`` gravados pelo agente rodariam fora do jail, no próximo
    ``git`` do usuário. Commits, branches e o restante de ``.git`` seguem
    graváveis."""
    git_dir = Path(workspace_dir) / ".git"
    if not git_dir.is_dir():
        return []
    argv: list[str] = []
    for name in ("hooks", "config"):
        target = git_dir / name
        if target.exists():
            argv += ["--ro-bind", str(target), str(target)]
    return argv


def build_bwrap_command(
    policy: SandboxPolicy,
    workspace_dir: str,
    command: list[str],
    *,
    runtime_ro_paths: Sequence[str | tuple[str, str]] = (),
) -> list[str]:
    """Monta o argv completo de `bwrap`, na ordem: sessão e ambiente limpos →
    mounts base (proc/dev/sistema somente leitura, `/tmp` próprio) → `$HOME`
    dedicado do worker (tmpfs vazio, nunca o `$HOME` real) → pastas de runtime
    do Python (`runtime_ro_paths`, só quando o comando é o worker) → workspace
    em rw → `rw_paths`/`ro_paths` da política (depois do bind do workspace, pra
    path dentro do workspace não ser sombreado por ele) → proteção de
    `.git/hooks` e `.git/config` → `mask` por último (glob expandido contra o
    workspace; arquivo vira `--ro-bind /dev/null`, diretório vira `--tmpfs`, sem
    match é ignorado) — `vectora.toml` do workspace entra no mask sempre, mesmo
    sem o usuário declarar (o worker nunca vê sua própria política).

    O processo nasce com ambiente limpo (`--clearenv` mais `jail_environment`):
    chaves de provider e tokens do backend nunca chegam ao jail. `--new-session`
    tira o jail do terminal de controle do backend. `lockdown` também nega rede
    (`--unshare-net`) e seta `VECTORA_SANDBOX_LOCKDOWN=1` (perfil restrito de
    rlimits, ver `backend/sandbox/rlimits.py`)."""
    argv: list[str] = [
        "bwrap",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        *system_mounts(),
        "--tmpfs",
        "/tmp",  # noqa: S108  # nosec B108 - /tmp do próprio jail, nunca o do host
        "--tmpfs",
        WORKER_HOME,
    ]
    runtime = tuple(runtime_ro_paths)
    for entry in runtime:
        source, target = (entry, entry) if isinstance(entry, str) else entry
        argv += ["--ro-bind", source, target]
    for name, value in jail_environment(policy, python_path_for(runtime)).items():
        argv += ["--setenv", name, value]
    argv += ["--bind", workspace_dir, workspace_dir]
    for path in policy.rw_paths:
        argv += ["--bind", path, path]
    for path in policy.ro_paths:
        argv += ["--ro-bind", path, path]
    if not policy.git_writable:
        argv += git_protection_mounts(workspace_dir)

    mask_patterns = (*policy.mask, "vectora.toml")
    masked_paths: list[str] = []
    for pattern in mask_patterns:
        for resolved in _expand_mask_glob(pattern, workspace_dir):
            if resolved in masked_paths:
                continue
            masked_paths.append(resolved)
            if Path(resolved).is_dir():
                argv += ["--tmpfs", resolved]
            else:
                argv += ["--ro-bind", "/dev/null", resolved]

    if policy.lockdown:
        argv += ["--unshare-net"]
    argv += ["--setenv", "VECTORA_SANDBOX_LOCKDOWN", "1" if policy.lockdown else "0"]
    # Propaga rw_paths/ro_paths/workspace pro worker aplicar Landlock (4.1)
    # de dentro do próprio processo — landlock_restrict_self só afeta quem
    # o chama, não pode ser feito de fora via bwrap. As pastas base (sistema,
    # runtime do Python, /tmp, /dev) vão em variáveis próprias: o Landlock nega
    # tudo o que não é listado, inclusive o carregador e o interpretador.
    argv += ["--setenv", "VECTORA_SANDBOX_WORKSPACE_DIR", workspace_dir]
    argv += ["--setenv", "VECTORA_SANDBOX_RW_PATHS", json.dumps(list(policy.rw_paths))]
    argv += ["--setenv", "VECTORA_SANDBOX_RO_PATHS", json.dumps(list(policy.ro_paths))]
    argv += [
        "--setenv",
        "VECTORA_SANDBOX_BASE_RO_PATHS",
        json.dumps([*system_read_paths(), *map(_runtime_real, runtime), "/proc"]),
    ]
    argv += [
        "--setenv",
        "VECTORA_SANDBOX_BASE_RW_PATHS",
        json.dumps(list(BASE_RW_PATHS)),
    ]
    argv += [
        "--setenv",
        "VECTORA_SANDBOX_ALLOW_TCP_PORTS",
        json.dumps(list(policy.allow_tcp_ports)),
    ]
    argv += ["--chdir", workspace_dir]
    argv += ["--", *command]
    return argv
