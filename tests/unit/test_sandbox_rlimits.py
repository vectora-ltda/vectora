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
