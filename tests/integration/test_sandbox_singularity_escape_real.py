"""Sandbox Singularity/Apptainer — escape real contra um binário instalado.

`tests/unit/test_sandbox_singularity.py` mocka o subprocess inteiro: prova
que o argv está certo, não que o container de fato contém o processo. Aqui
`singularity`/`apptainer` roda de verdade e o teste confirma que a leitura
fora do bind mount e a rede (quando `lockdown=True`) falham de verdade.

Skip-guard hermético (não conta com a ausência do binário pra "passar"): só
roda em Linux com `singularity` ou `apptainer` instalado. Fora disso é skip
explícito, nunca um verde silencioso.
"""

from __future__ import annotations

import shutil
import sys

import pytest

from backend.sandbox.policy import SandboxPolicy
from backend.sandbox.singularity import run_singularity_sandboxed

pytestmark = [
    pytest.mark.singularity,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="Singularity/Apptainer é Linux-only (namespaces unprivilegiados)",
    ),
    pytest.mark.skipif(
        shutil.which("singularity") is None and shutil.which("apptainer") is None,
        reason="nem singularity nem apptainer instalados",
    ),
]


@pytest.mark.asyncio
async def test_leitura_dentro_do_workspace_funciona_fora_falha(tmp_path):
    """Happy: o que está no bind mount é legível. Erro: `/etc/shadow` do host
    está fora do bind e a leitura precisa falhar — se passar, o `--containall`
    não está isolando o filesystem do host."""
    (tmp_path / "dentro.txt").write_text("conteudo\n", encoding="utf-8")
    policy = SandboxPolicy(enabled=True, rw_paths=(str(tmp_path),))

    dentro = await run_singularity_sandboxed(
        ["cat", f"{tmp_path}/dentro.txt"], str(tmp_path), policy, timeout_s=30.0
    )
    assert dentro.exit_code == 0
    assert "conteudo" in dentro.stdout

    fora = await run_singularity_sandboxed(
        ["cat", "/etc/shadow"], str(tmp_path), policy, timeout_s=30.0
    )
    assert fora.exit_code != 0, (
        f"/etc/shadow foi lido de dentro do container: {fora.stdout[:200]!r}"
    )


@pytest.mark.asyncio
async def test_lockdown_nega_rede_de_verdade(tmp_path):
    """Com `lockdown=True`, `--net` isola pra uma rede privada sem rota
    externa — uma conexão de verdade contra um IP público precisa falhar."""
    policy = SandboxPolicy(enabled=True, lockdown=True, rw_paths=(str(tmp_path),))

    result = await run_singularity_sandboxed(
        ["sh", "-c", "wget -T 5 -O- http://1.1.1.1 2>&1"],
        str(tmp_path),
        policy,
        timeout_s=20.0,
    )
    assert result.exit_code != 0
