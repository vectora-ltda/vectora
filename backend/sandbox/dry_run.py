"""Serializa o comando `bwrap` a partir de uma `SandboxPolicy`, sem executar.

Usado tanto por uma futura flag de auditoria quanto pelos testes — permite
validar a lógica de montagem de mounts/flags sem precisar do binário
`bwrap` instalado (CI-friendly).
"""

from __future__ import annotations

import json
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
    "mount",
    "umount2",
    "pivot_root",
    "unshare",
    "personality",
    "reboot",
)


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


def build_bwrap_command(
    policy: SandboxPolicy, workspace_dir: str, command: list[str]
) -> list[str]:
    """Monta o argv completo de `bwrap`, na ordem: mounts base (proc/dev/
    usr/bin/lib) → `$HOME` dedicado do worker (tmpfs vazio, nunca o
    `$HOME` real) → workspace em rw → `rw_paths`/`ro_paths` da política
    (depois do bind do workspace, pra path dentro do workspace não ser
    sombreado por ele) → `mask` por último (glob expandido contra o
    workspace; arquivo vira `--ro-bind /dev/null`, diretório vira
    `--tmpfs`, sem match é ignorado) — `vectora.toml` do workspace entra
    no mask sempre, mesmo sem o usuário declarar (o worker nunca vê sua
    própria política). `lockdown` também nega rede (`--unshare-net`) e
    seta `VECTORA_SANDBOX_LOCKDOWN=1` (perfil restrito de rlimits, ver
    `backend/sandbox/rlimits.py`)."""
    argv: list[str] = [
        "bwrap",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--die-with-parent",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--tmpfs",
        WORKER_HOME,
        "--setenv",
        "HOME",
        WORKER_HOME,
    ]
    argv += ["--bind", workspace_dir, workspace_dir]
    for path in policy.rw_paths:
        argv += ["--bind", path, path]
    for path in policy.ro_paths:
        argv += ["--ro-bind", path, path]

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
    # o chama, não pode ser feito de fora via bwrap.
    argv += ["--setenv", "VECTORA_SANDBOX_WORKSPACE_DIR", workspace_dir]
    argv += ["--setenv", "VECTORA_SANDBOX_RW_PATHS", json.dumps(list(policy.rw_paths))]
    argv += ["--setenv", "VECTORA_SANDBOX_RO_PATHS", json.dumps(list(policy.ro_paths))]
    argv += [
        "--setenv",
        "VECTORA_SANDBOX_ALLOW_TCP_PORTS",
        json.dumps(list(policy.allow_tcp_ports)),
    ]
    argv += ["--chdir", workspace_dir]
    argv += ["--", *command]
    return argv
