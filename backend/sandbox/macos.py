"""Backend de sandbox nativo no host macOS via `sandbox-exec` (Seatbelt) —
paralelo a `linux.py` (bwrap), mesma filosofia de política (`SandboxPolicy`
compartilhada), mecanismo de isolamento completamente diferente por
natureza da plataforma, não por lacuna de implementação do Vectora.

Reimplementação própria (não fork), inspirada no mesmo princípio de design
documentado em `policy.py` (referência: `akitaonrails/ai-jail`, que também
usa Seatbelt no macOS via SBPL — Sandbox Profile Language).

`sandbox-exec` é uma interface **legada/não-documentada publicamente** da
Apple — funciona hoje, mas pode ser removida em versão futura do macOS sem
aviso (mesmo risco que o `ai-jail` original assume e documenta). Diferente
do Linux, o macOS não tem equivalente nativo a seccomp-bpf nem Landlock: o
Seatbelt cobre só filesystem/rede/processo via SBPL — o rigor é
estruturalmente menor que o do backend `local` no Linux, não uma escolha
de implementação mais fraca.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from backend.sandbox.linux import SandboxResult
from backend.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)

# SBPL usa `\` para escapar aspas/barras dentro de string literal.
_SBPL_ESCAPE_CHARS = ("\\", '"')


def _sbpl_escape(path: str) -> str:
    escaped = path
    for ch in _SBPL_ESCAPE_CHARS:
        escaped = escaped.replace(ch, "\\" + ch)
    return escaped


def _path_rule(verb: str, action: str, path: str) -> str:
    """`subpath` para diretório (ou caminho ainda inexistente — mask
    preventivo antes do arquivo existir é válido), `literal` para arquivo
    real. Mesma heurística de `dry_run._expand_mask_glob`."""
    p = Path(path)
    pattern = "subpath" if (p.is_dir() or not p.exists()) else "literal"
    return f'({verb} {action} ({pattern} "{_sbpl_escape(str(p))}"))\n'


def _expand_mask_glob(pattern: str, workspace_dir: str) -> list[str]:
    """Mesma lógica de `dry_run._expand_mask_glob` — duplicada aqui (não
    importada) porque `dry_run.py` é especificamente o dry-run do `bwrap`;
    manter os dois desacoplados evita que uma mudança no formato do argv
    do bwrap acople acidentalmente o gerador de SBPL."""
    has_glob = any(ch in pattern for ch in "*?[")
    base = Path(workspace_dir)
    if not has_glob:
        return [str(base / pattern)]
    return [str(m.resolve()) for m in base.glob(pattern)]


def build_seatbelt_profile(policy: SandboxPolicy, workspace_dir: str) -> str:
    """Gera o profile SBPL completo. Separado da execução pra ser testável
    sem `sandbox-exec` instalado nem estar rodando em macOS (mesmo espírito
    de `dry_run.build_bwrap_command`).

    Ordem importa em SBPL: é "last-match-wins" — os `allow` amplos (leitura/
    escrita do workspace) vêm primeiro, os `deny` de `mask` vêm por último
    pra sobrescrever qualquer allow anterior que os cubra.
    """
    lines: list[str] = ["(version 1)\n", "(deny default)\n\n"]

    # Processo — mesmo conjunto liberado por padrão em qualquer sandbox de
    # execução de comando de dev (compilar, rodar testes, git).
    lines.append("; Process operations\n")
    lines.append("(allow process-exec)\n")
    lines.append("(allow process-fork)\n")
    lines.append("(allow signal)\n")
    lines.append("(allow sysctl-read)\n\n")

    # Rede — Seatbelt não tem granularidade de porta (diferente do Landlock
    # V4 no Linux via --allow-tcp-port); é allow/deny binário, limitação da
    # própria API da Apple, não do Vectora.
    lines.append("; Network\n")
    if policy.lockdown:
        lines.append("(deny network-outbound)\n")
        lines.append("(deny network-inbound)\n")
        lines.append("(deny network-bind)\n\n")
    else:
        lines.append("(allow network-outbound)\n")
        lines.append("(allow network-inbound)\n")
        lines.append("(allow network-bind)\n\n")

    # Leitura — sistema base + workspace + rw_paths/ro_paths da política.
    lines.append("; File read\n")
    lines.append('(allow file-read* (subpath "/usr"))\n')
    lines.append('(allow file-read* (subpath "/bin"))\n')
    lines.append('(allow file-read* (subpath "/System"))\n')
    lines.append('(allow file-read* (subpath "/Library"))\n')
    lines.append(_path_rule("allow", "file-read*", workspace_dir))
    lines.extend(
        _path_rule("allow", "file-read*", path)
        for path in (*policy.rw_paths, *policy.ro_paths)
    )
    lines.append("\n")

    # Escrita — só workspace + rw_paths (nunca ro_paths).
    lines.append("; File write\n")
    lines.append(_path_rule("allow", "file-write*", workspace_dir))
    lines.extend(_path_rule("allow", "file-write*", path) for path in policy.rw_paths)
    lines.append("\n")

    # Mask — por último, sobrescreve os allows acima pros paths mascarados
    # (segredos: .env, chaves SSH/AWS, e sempre vectora.toml, mesmo padrão
    # de dry_run.build_bwrap_command).
    mask_patterns = (*policy.mask, "vectora.toml")
    masked: list[str] = []
    lines.append("; Masked paths (deny overrides the allows above)\n")
    for pattern in mask_patterns:
        for resolved in _expand_mask_glob(pattern, workspace_dir):
            if resolved in masked:
                continue
            masked.append(resolved)
            lines.append(_path_rule("deny", "file-read*", resolved))
            lines.append(_path_rule("deny", "file-write*", resolved))

    return "".join(lines)


async def run_macos_sandboxed(
    command: list[str],
    workspace_dir: str,
    policy: SandboxPolicy,
    *,
    timeout_s: float = 60.0,
) -> SandboxResult:
    """Roda `command` sob `sandbox-exec -p <profile>`. Binário ausente
    (macOS pré-Seatbelt ou removido numa versão futura) devolve
    `exit_code=127` com mensagem clara; sem permissão devolve `exit_code=126`
    — nunca levanta exceção (tools defensivas, CLAUDE.md regra 11)."""
    if shutil.which("sandbox-exec") is None:
        logger.warning("sandbox.macos: binário sandbox-exec não encontrado no sistema")
        return SandboxResult(
            stdout="",
            stderr=(
                "Error: sandbox-exec não está disponível neste sistema — "
                "sandbox indisponível (interface legada da Apple, pode ter "
                "sido removida nesta versão do macOS)."
            ),
            exit_code=127,
        )

    profile = build_seatbelt_profile(policy, workspace_dir)
    argv = ["sandbox-exec", "-p", profile, *command]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("sandbox.macos: sandbox-exec sumiu entre o which() e o exec()")
        return SandboxResult(
            stdout="",
            stderr="Error: sandbox-exec não está disponível — sandbox indisponível.",
            exit_code=127,
        )
    except PermissionError:
        logger.warning("sandbox.macos: sem permissão para executar sandbox-exec")
        return SandboxResult(
            stdout="",
            stderr="Error: sem permissão para executar sandbox-exec — sandbox indisponível.",
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
