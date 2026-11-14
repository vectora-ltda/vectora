"""Relatório de isolamento, prévia do jail e eventos de auditoria do jail real."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.sandbox import isolation, workspace_jail
from backend.sandbox.audit import audit_log
from backend.sandbox.dry_run import preview_bwrap_command
from backend.sandbox.isolation import isolation_report
from backend.sandbox.policy import SandboxPolicy
from tests.unit.test_sandbox_hardening import _bwrap_works, _exec

real_bwrap = pytest.mark.skipif(
    not _bwrap_works(), reason="bwrap sem user namespaces neste ambiente"
)

_TUDO = {"seccomp": True, "landlock": True, "no_new_privs": True, "rlimits": True}


def _estado(report, nome):
    return next(d.state for d in report.dimensions if d.name == nome)


@pytest.fixture
def host_com_bwrap(monkeypatch):
    monkeypatch.setattr(isolation, "_host_can_isolate", lambda: True)


def test_sandbox_desligado_e_off():
    report = isolation_report(SandboxPolicy(enabled=False))

    assert report.level == "off"
    assert report.dimensions == ()


def test_worker_com_tudo_aplicado_e_efetivo(host_com_bwrap):
    report = isolation_report(
        SandboxPolicy(enabled=True), worker_status=_TUDO, worker_enforced=True
    )

    assert report.level == "effective"
    assert report.live is True
    assert {d.state for d in report.dimensions} == {"effective"}


def test_sem_seccomp_o_isolamento_e_parcial_nunca_efetivo(host_com_bwrap):
    status = {**_TUDO, "seccomp": False}

    report = isolation_report(
        SandboxPolicy(enabled=True), worker_status=status, worker_enforced=True
    )

    assert report.level == "partial"
    assert _estado(report, "seccomp") == "partial"
    assert "libseccomp" in next(
        d.detail for d in report.dimensions if d.name == "seccomp"
    )


def test_sem_landlock_o_isolamento_e_parcial(host_com_bwrap):
    report = isolation_report(
        SandboxPolicy(enabled=True),
        worker_status={**_TUDO, "landlock": False},
        worker_enforced=True,
    )

    assert report.level == "partial"
    assert _estado(report, "landlock") == "partial"


def test_rede_aberta_deixa_o_isolamento_parcial(host_com_bwrap):
    report = isolation_report(
        SandboxPolicy(enabled=True, network="open"),
        worker_status=_TUDO,
        worker_enforced=True,
    )

    assert report.level == "partial"
    assert _estado(report, "network") == "partial"


def test_rede_filtrada_e_sem_rede_sao_efetivas(host_com_bwrap):
    for modo in ("filtered", "off"):
        report = isolation_report(
            SandboxPolicy(enabled=True, network=modo),  # type: ignore[arg-type]
            worker_status=_TUDO,
            worker_enforced=True,
        )
        assert _estado(report, "network") == "effective"


def test_git_gravavel_deixa_parcial(host_com_bwrap):
    report = isolation_report(
        SandboxPolicy(enabled=True, git_writable=True),
        worker_status=_TUDO,
        worker_enforced=True,
    )

    assert report.level == "partial"
    assert _estado(report, "git") == "partial"


def test_worker_soft_do_windows_e_declarado_soft():
    report = isolation_report(
        SandboxPolicy(enabled=True), worker_status={}, worker_enforced=False
    )

    assert report.level == "soft"
    assert _estado(report, "filesystem") == "off"
    assert _estado(report, "network") == "off"


def test_sem_bwrap_no_host_o_nivel_e_off(monkeypatch):
    monkeypatch.setattr(isolation, "_host_can_isolate", lambda: False)

    report = isolation_report(SandboxPolicy(enabled=True))

    assert report.level == "off"
    assert report.live is False
    assert _estado(report, "filesystem") == "off"


def test_sem_worker_o_relatorio_diz_que_nao_e_ao_vivo(host_com_bwrap, monkeypatch):
    monkeypatch.setattr(isolation, "_seccomp_available", lambda: True)

    report = isolation_report(SandboxPolicy(enabled=True))

    assert report.live is False


# ── prévia ──────────────────────────────────────────────────────────────────


def test_previa_mostra_o_comando_sem_executar_e_oculta_o_que_veio_do_host(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NPM_TOKEN", "npm_segredo")
    policy = SandboxPolicy(enabled=True, env_pass=("NPM_TOKEN",))

    argv = preview_bwrap_command(policy, str(tmp_path))
    texto = " ".join(argv)

    assert argv[0] == "bwrap"
    assert "npm_segredo" not in texto
    assert "--setenv NPM_TOKEN <oculto>" in texto
    assert "--clearenv" in argv
    assert "--unshare-net" in argv
    assert "<comando>" in argv


def test_previa_mantem_visiveis_as_variaveis_seguras(tmp_path):
    argv = preview_bwrap_command(SandboxPolicy(enabled=True), str(tmp_path))
    texto = " ".join(argv)

    assert "--setenv HOME /home/vectora-worker" in texto
    assert "<oculto>" not in texto


# ── auditoria do jail real ──────────────────────────────────────────────────


@real_bwrap
@pytest.mark.asyncio
async def test_jail_real_registra_inicio_e_decisoes_de_rede(tmp_path):
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws-aud", str(tmp_path), SandboxPolicy(enabled=True)
        )
        await _exec(
            worker,
            "python3 -c \"import socket;s=socket.create_connection(('127.0.0.1',3128),timeout=5);"
            "s.sendall(b'CONNECT evil.example.org:443 HTTP/1.1\\r\\n\\r\\n');s.recv(100)\"",
        )
    finally:
        await manager.close("ws-aud")
        from backend.sandbox.egress_proxy import egress_registry

        await egress_registry.close_all()

    eventos = audit_log.read(workspace_id="ws-aud")
    nomes = [e["event"] for e in eventos]
    assert "worker_started" in nomes
    iniciado = next(e for e in eventos if e["event"] == "worker_started")
    assert iniciado["data"]["network"] == "filtered"
    assert iniciado["data"]["enforced"] is True
    assert iniciado["data"]["policy_digest"]
    saidas = [e for e in eventos if e["event"] == "egress"]
    assert any(
        e["data"]["host"] == "evil.example.org" and e["data"]["allowed"] is False
        for e in saidas
    )
    assert audit_log.verify().ok is True


@real_bwrap
@pytest.mark.asyncio
async def test_isolamento_exigido_sem_seccomp_e_recusado_e_auditado(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(workspace_jail, "build_seccomp_filter", lambda: None)
    manager = workspace_jail.WorkspaceJailManager()

    with pytest.raises(workspace_jail.WorkerSpawnError):
        await manager.get_or_spawn(
            "ws-rec",
            str(tmp_path),
            SandboxPolicy(enabled=True, require_full_isolation=True),
        )

    eventos = audit_log.read(workspace_id="ws-rec")
    assert [e["event"] for e in eventos] == ["worker_refused"]
    assert eventos[0]["data"]["reason"] == "seccomp_unavailable"


@real_bwrap
@pytest.mark.asyncio
async def test_worker_for_so_devolve_worker_vivo(tmp_path):
    manager = workspace_jail.WorkspaceJailManager()
    assert manager.worker_for("ws-x") is None
    try:
        worker = await manager.get_or_spawn(
            "ws-x", str(Path(tmp_path)), SandboxPolicy(enabled=True)
        )
        assert manager.worker_for("ws-x") is worker
    finally:
        await manager.close("ws-x")
        from backend.sandbox.egress_proxy import egress_registry

        await egress_registry.close_all()
    assert manager.worker_for("ws-x") is None
