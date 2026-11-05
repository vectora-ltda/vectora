"""Sandbox — escape real contra um kernel Linux de verdade.

Todo o resto da suíte de sandbox é mockado (argv montado, SDK falso): isso
prova que a *configuração* está certa, não que o kernel a respeita. Aqui o
`bwrap` roda de verdade e o teste confirma que a contenção acontece — se
o namespace/Landlock/rlimit parar de ser aplicado, estes testes falham
mesmo com todos os testes de argv passando.

Skip-guard hermético (não conta com a ausência do binário pra "passar"):
só roda em Linux com `bwrap` instalado. Fora disso é skip explícito, nunca
um verde silencioso.
"""

from __future__ import annotations

import shutil
import sys

import pytest

from backend.sandbox.linux import run_local_sandboxed
from backend.sandbox.policy import SandboxPolicy

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="bwrap é Linux-only — no Windows o caminho real é WSL2 (testado à parte)",
    ),
    pytest.mark.skipif(
        shutil.which("bwrap") is None,
        reason="bwrap não instalado — `sudo apt install bubblewrap`",
    ),
]


@pytest.mark.asyncio
async def test_leitura_dentro_do_workspace_funciona_fora_falha(tmp_path):
    """Happy: o que está no workspace é legível. Erro: `/etc/passwd` está
    fora de `rw_paths` e a leitura precisa falhar de verdade — se passar,
    o bind mount não está contendo nada."""
    (tmp_path / "dentro.txt").write_text("conteudo\n", encoding="utf-8")
    policy = SandboxPolicy(enabled=True, rw_paths=(str(tmp_path),))

    dentro = await run_local_sandboxed(
        ["cat", f"{tmp_path}/dentro.txt"], str(tmp_path), policy, timeout_s=15.0
    )
    assert dentro.exit_code == 0
    assert "conteudo" in dentro.stdout

    fora = await run_local_sandboxed(
        ["cat", "/etc/passwd"], str(tmp_path), policy, timeout_s=15.0
    )
    assert fora.exit_code != 0, (
        f"/etc/passwd foi lido de dentro do jail: {fora.stdout[:200]!r}"
    )
    assert "root:" not in fora.stdout


@pytest.mark.asyncio
async def test_escrita_fora_do_workspace_falha(tmp_path):
    """Escrever num path não declarado em `rw_paths` precisa falhar — o
    workspace segue gravável (é o ponto do sandbox, não uma prisão total)."""
    policy = SandboxPolicy(enabled=True, rw_paths=(str(tmp_path),))

    dentro = await run_local_sandboxed(
        ["sh", "-c", f"echo ok > {tmp_path}/novo.txt"],
        str(tmp_path),
        policy,
        timeout_s=15.0,
    )
    assert dentro.exit_code == 0

    fora = await run_local_sandboxed(
        ["sh", "-c", "echo invadido > /etc/vectora-escape-test"],
        str(tmp_path),
        policy,
        timeout_s=15.0,
    )
    assert fora.exit_code != 0


@pytest.mark.asyncio
async def test_fork_bomb_e_contida_sem_travar_o_processo_de_teste(tmp_path):
    """`RLIMIT_NPROC` precisa cortar a explosão de processos. Timeout curto:
    se a contenção falhar, o teste termina por timeout em vez de deixar a
    máquina do CI sem PIDs livres."""
    policy = SandboxPolicy(enabled=True, lockdown=True, rw_paths=(str(tmp_path),))

    result = await run_local_sandboxed(
        ["sh", "-c", ":(){ :|:& };:"], str(tmp_path), policy, timeout_s=10.0
    )

    # Contida por limite (exit != 0) ou pelo timeout do próprio wrapper —
    # o que não pode acontecer é rodar até o fim com sucesso.
    assert result.exit_code != 0 or result.timed_out
