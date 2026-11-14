"""Sandbox — backend.sandbox.rlimits: RLIMIT_NPROC/NOFILE/CORE aplicados
ao worker antes do loop RPC, contendo fork bombs mesmo sem Landlock."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.sandbox import rlimits


def _fake_resource_module():
    fake = MagicMock()
    fake.RLIMIT_NPROC = 1
    fake.RLIMIT_NOFILE = 2
    fake.RLIMIT_CORE = 3
    fake.setrlimit = MagicMock()
    return fake


def test_perfil_normal_aplica_valores_esperados(monkeypatch):
    fake = _fake_resource_module()
    monkeypatch.setitem(__import__("sys").modules, "resource", fake)

    rlimits.apply_rlimits(lockdown=False)

    calls = {c.args[0]: c.args[1] for c in fake.setrlimit.call_args_list}
    assert calls[fake.RLIMIT_NPROC] == (
        rlimits.NORMAL_PROFILE.nproc,
        rlimits.NORMAL_PROFILE.nproc,
    )
    assert calls[fake.RLIMIT_NOFILE] == (
        rlimits.NORMAL_PROFILE.nofile,
        rlimits.NORMAL_PROFILE.nofile,
    )
    assert calls[fake.RLIMIT_CORE] == (0, 0)


def test_perfil_lockdown_usa_valores_mais_restritos(monkeypatch):
    fake = _fake_resource_module()
    monkeypatch.setitem(__import__("sys").modules, "resource", fake)

    rlimits.apply_rlimits(lockdown=True)

    calls = {c.args[0]: c.args[1] for c in fake.setrlimit.call_args_list}
    assert calls[fake.RLIMIT_NPROC] == (
        rlimits.LOCKDOWN_PROFILE.nproc,
        rlimits.LOCKDOWN_PROFILE.nproc,
    )
    assert rlimits.LOCKDOWN_PROFILE.nproc < rlimits.NORMAL_PROFILE.nproc
    assert rlimits.LOCKDOWN_PROFILE.nofile < rlimits.NORMAL_PROFILE.nofile


def test_modulo_resource_ausente_nao_lanca(monkeypatch):
    # Erro/borda: plataforma sem `resource` (ex. Windows) — nunca deve
    # levantar, o worker segue sem os limites.
    import sys

    monkeypatch.setitem(sys.modules, "resource", None)

    rlimits.apply_rlimits(lockdown=False)


def test_setrlimit_levantando_oserror_nao_propaga(monkeypatch):
    # Erro/borda: baixar um limite já reduzido pelo SO (permissão
    # insuficiente) levanta OSError/ValueError — degrada com aviso.
    fake = _fake_resource_module()
    fake.setrlimit.side_effect = OSError("not permitted")
    monkeypatch.setitem(__import__("sys").modules, "resource", fake)

    rlimits.apply_rlimits(lockdown=False)


@pytest.mark.parametrize("lockdown", [True, False])
def test_todos_os_tres_limites_sao_configurados(monkeypatch, lockdown):
    fake = _fake_resource_module()
    monkeypatch.setitem(__import__("sys").modules, "resource", fake)

    rlimits.apply_rlimits(lockdown=lockdown)

    assert fake.setrlimit.call_count == 3


# ── tamanho de arquivo e memória ────────────────────────────────────────────


def _aplicar_em_subprocesso(**limites):
    """Aplica os limites num processo filho e devolve o que ele enxerga depois."""
    import json
    import subprocess
    import sys
    import textwrap

    codigo = textwrap.dedent(
        f"""
        import json, resource
        from backend.sandbox.rlimits import apply_rlimits
        apply_rlimits(False, **{limites!r})
        print(json.dumps({{
            "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
            "as": resource.getrlimit(resource.RLIMIT_AS)[0],
        }}))
        """
    )
    saida = subprocess.run(  # noqa: S603
        [sys.executable, "-c", codigo], capture_output=True, text=True, check=True
    )
    return json.loads(saida.stdout)


def test_max_file_mb_limita_o_tamanho_de_cada_arquivo():
    limites = _aplicar_em_subprocesso(max_file_mb=64)

    assert limites["fsize"] == 64 * 1024 * 1024


def test_memory_limit_mb_limita_o_espaco_de_enderecamento():
    limites = _aplicar_em_subprocesso(memory_limit_mb=2048)

    assert limites["as"] == 2048 * 1024 * 1024


def test_sem_declarar_nao_limita_arquivo_nem_memoria():
    import resource

    limites = _aplicar_em_subprocesso()

    assert limites["fsize"] == resource.RLIM_INFINITY
    assert limites["as"] == resource.RLIM_INFINITY


def test_limits_from_environment_le_so_inteiros_positivos(monkeypatch):
    from backend.sandbox.rlimits import limits_from_environment

    monkeypatch.setenv("VECTORA_SANDBOX_MAX_FILE_MB", "512")
    monkeypatch.setenv("VECTORA_SANDBOX_MEMORY_MB", "0")
    assert limits_from_environment() == (512, None)

    monkeypatch.setenv("VECTORA_SANDBOX_MAX_FILE_MB", "-3")
    monkeypatch.setenv("VECTORA_SANDBOX_MEMORY_MB", "abc")
    assert limits_from_environment() == (None, None)

    monkeypatch.delenv("VECTORA_SANDBOX_MAX_FILE_MB")
    monkeypatch.delenv("VECTORA_SANDBOX_MEMORY_MB")
    assert limits_from_environment() == (None, None)
