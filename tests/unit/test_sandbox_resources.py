"""Limites de recursos do jail real: arquivo, tmpfs e memória."""

from __future__ import annotations

import sys

import pytest

from backend.sandbox import dry_run, workspace_jail
from backend.sandbox.egress_proxy import egress_registry
from backend.sandbox.policy import SandboxPolicy
from tests.unit.test_sandbox_hardening import _bwrap_works, _exec

real_bwrap = pytest.mark.skipif(
    not _bwrap_works(), reason="bwrap sem user namespaces neste ambiente"
)


async def _com_worker(tmp_path, policy, *comandos: str) -> list[dict]:
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn("ws-rec", str(tmp_path), policy)
        return [await _exec(worker, comando) for comando in comandos]
    finally:
        await manager.close("ws-rec")
        await egress_registry.close_all()


@real_bwrap
@pytest.mark.asyncio
async def test_arquivo_maior_que_o_limite_nao_e_gravado(tmp_path):
    policy = SandboxPolicy(enabled=True, max_file_mb=1)

    pequeno, grande = await _com_worker(
        tmp_path,
        policy,
        "head -c 500000 /dev/zero > pequeno.bin",
        "head -c 5000000 /dev/zero > grande.bin",
    )

    assert pequeno["exit_code"] == 0
    assert (tmp_path / "pequeno.bin").stat().st_size == 500000
    assert grande["exit_code"] != 0
    assert (tmp_path / "grande.bin").stat().st_size <= 1024 * 1024


@real_bwrap
@pytest.mark.skipif(
    not dry_run.bwrap_supports_size(), reason="bwrap sem --size neste computador"
)
@pytest.mark.asyncio
async def test_tmpfs_do_jail_tem_teto_de_tamanho(tmp_path):
    policy = SandboxPolicy(enabled=True, tmp_size_mb=4)

    cabe, nao_cabe = await _com_worker(
        tmp_path,
        policy,
        "head -c 2000000 /dev/zero > /tmp/a.bin",
        "head -c 8000000 /dev/zero > /tmp/b.bin",
    )

    assert cabe["exit_code"] == 0
    assert nao_cabe["exit_code"] != 0


@real_bwrap
@pytest.mark.asyncio
async def test_limite_de_memoria_impede_alocar_alem_do_teto(tmp_path):
    policy = SandboxPolicy(enabled=True, memory_limit_mb=512)
    alocar = f'{sys.executable} -c "b = bytearray(%d * 1024 * 1024); print(len(b))"'

    cabe, nao_cabe = await _com_worker(tmp_path, policy, alocar % 8, alocar % 2000)

    assert cabe["exit_code"] == 0
    assert nao_cabe["exit_code"] != 0
    assert "MemoryError" in nao_cabe["stderr"]


@real_bwrap
@pytest.mark.asyncio
async def test_sem_limite_de_memoria_a_alocacao_grande_passa(tmp_path):
    policy = SandboxPolicy(enabled=True)

    (resultado,) = await _com_worker(
        tmp_path,
        policy,
        f'{sys.executable} -c "b = bytearray(300 * 1024 * 1024); print(len(b))"',
    )

    assert resultado["exit_code"] == 0
