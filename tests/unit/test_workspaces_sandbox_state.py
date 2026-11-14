"""Rotas de estado, auditoria e prévia do sandbox."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.handlers.workspaces import (
    workspace_sandbox_audit,
    workspace_sandbox_dry_run,
    workspace_sandbox_status,
)
from backend.sandbox import isolation
from backend.sandbox import policy as policy_module
from backend.sandbox.audit import audit_log
from backend.sandbox.workspace_jail import jail_manager


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_module, "_native_sandbox_available_sync", lambda: False)
    monkeypatch.setattr(isolation, "_host_can_isolate", lambda: True)
    monkeypatch.setattr(isolation, "_seccomp_available", lambda: True)


def _register(monkeypatch: pytest.MonkeyPatch, cwd: Path) -> None:
    from backend.vtypes import Workspace
    from backend.workspace import workspace as ws_mod

    ws = Workspace(
        id="ws-1",
        name="ws-1",
        cwd=str(cwd),
        created_at="2024-01-01T00:00:00+00:00",
        trusted=True,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry, "get", lambda wid: ws if wid == "ws-1" else None
    )


@pytest.mark.asyncio
async def test_status_com_sandbox_ligado_traz_o_relatorio_por_dimensao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    (tmp_path / "vectora.toml").write_text("[sandbox]\nenabled = true\n")

    resp = await workspace_sandbox_status("ws-1")

    assert resp.enabled is True
    assert resp.isolation is not None
    assert resp.isolation.live is False  # nenhum worker ainda
    nomes = {d.name for d in resp.isolation.dimensions}
    assert {"filesystem", "network", "environment", "seccomp", "landlock"} <= nomes


@pytest.mark.asyncio
async def test_status_usa_o_estado_do_worker_vivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    (tmp_path / "vectora.toml").write_text("[sandbox]\nenabled = true\n")

    class _Worker:
        isolation_enforced = True

        async def isolation_status(self) -> dict[str, bool]:
            return {
                "seccomp": False,
                "landlock": True,
                "no_new_privs": True,
                "rlimits": True,
            }

    monkeypatch.setattr(jail_manager, "worker_for", lambda wid: _Worker())

    resp = await workspace_sandbox_status("ws-1")

    assert resp.isolation is not None
    assert resp.isolation.live is True
    assert resp.isolation.level == "partial"
    seccomp = next(d for d in resp.isolation.dimensions if d.name == "seccomp")
    assert seccomp.state == "partial"


@pytest.mark.asyncio
async def test_status_desligado_nao_traz_relatorio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)

    resp = await workspace_sandbox_status("ws-1")

    assert resp.enabled is False
    assert resp.isolation is None


@pytest.mark.asyncio
async def test_auditoria_devolve_so_os_eventos_do_workspace_com_a_cadeia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    audit_log.append("ws-1", "egress", {"host": "pypi.org", "allowed": True})
    audit_log.append("outro", "egress", {"host": "nao-aparece.com"})
    audit_log.append("ws-1", "worker_started", {"network": "filtered"})

    resp = await workspace_sandbox_audit("ws-1")

    assert [e.event for e in resp.events] == ["worker_started", "egress"]
    assert resp.chain_ok is True
    assert resp.records == 3
    assert "nao-aparece.com" not in str(resp.events)


@pytest.mark.asyncio
async def test_auditoria_acusa_cadeia_adulterada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    audit_log.append("ws-1", "egress", {"host": "a.com", "allowed": False})
    audit_log.append("ws-1", "egress", {"host": "b.com", "allowed": False})
    linhas = audit_log.path.read_text().splitlines()
    linhas[0] = linhas[0].replace('"allowed": false', '"allowed": true')
    audit_log.path.write_text("\n".join(linhas) + "\n")

    resp = await workspace_sandbox_audit("ws-1")

    assert resp.chain_ok is False
    assert resp.broken_at == 1


@pytest.mark.asyncio
async def test_auditoria_limita_a_quantidade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    for i in range(7):
        audit_log.append("ws-1", "egress", {"host": f"h{i}.com"})

    resp = await workspace_sandbox_audit("ws-1", limit=3)

    assert len(resp.events) == 3
    assert resp.events[0].data["host"] == "h6.com"


@pytest.mark.asyncio
async def test_auditoria_e_previa_de_workspace_inexistente_respondem_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _register(monkeypatch, tmp_path)

    for rota in (workspace_sandbox_audit, workspace_sandbox_dry_run):
        with pytest.raises(HTTPException) as erro:
            await rota("nao-existe")
        assert erro.value.status_code == 404


@pytest.mark.asyncio
async def test_previa_devolve_o_comando_sem_executar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    (tmp_path / "vectora.toml").write_text("[sandbox]\nenabled = true\n")

    resp = await workspace_sandbox_dry_run("ws-1")

    assert resp.argv[0] == "bwrap"
    assert "--clearenv" in resp.argv
    assert "<comando>" in resp.argv
