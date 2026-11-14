"""Rotas da política do sandbox: a do usuário, fora do repositório."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.handlers.workspaces import (
    SandboxPolicyBody,
    update_workspace_sandbox_policy,
    workspace_sandbox_policy,
    workspace_sandbox_status,
)
from backend.sandbox import policy as policy_module
from backend.sandbox.policy import trusted_policy_path


@pytest.fixture(autouse=True)
def _sem_deteccao_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_module, "_native_sandbox_available_sync", lambda: False)


def _register(
    monkeypatch: pytest.MonkeyPatch, cwd: Path, *, trusted: bool = True
) -> None:
    from backend.vtypes import Workspace
    from backend.workspace import workspace as ws_mod

    ws = Workspace(
        id="ws-1",
        name="ws-1",
        cwd=str(cwd),
        created_at="2024-01-01T00:00:00+00:00",
        trusted=trusted,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry, "get", lambda wid: ws if wid == "ws-1" else None
    )


@pytest.mark.asyncio
async def test_get_sem_politica_devolve_o_padrao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)

    resp = await workspace_sandbox_policy("ws-1")

    assert resp.enabled is False
    assert resp.user_policy is False
    assert resp.ignored_repo_keys == []


@pytest.mark.asyncio
async def test_workspace_inexistente_responde_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(monkeypatch, Path("/tmp"))

    with pytest.raises(HTTPException) as erro:
        await workspace_sandbox_policy("nao-existe")

    assert erro.value.status_code == 404


@pytest.mark.asyncio
async def test_put_grava_fora_do_workspace_e_devolve_a_efetiva(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)

    resp = await update_workspace_sandbox_policy(
        "ws-1",
        SandboxPolicyBody(
            rw_paths=["/dados"],
            env_pass=["NPM_TOKEN"],
            allow_tcp_ports=[8080],
            git_writable=True,
        ),
    )

    assert resp.enabled is True
    assert resp.rw_paths == ["/dados"]
    assert resp.env_pass == ["NPM_TOKEN"]
    assert resp.allow_tcp_ports == [8080]
    assert resp.git_writable is True
    assert resp.user_policy is True
    assert trusted_policy_path(tmp_path).is_file()
    assert not (tmp_path / "vectora.toml").exists()


@pytest.mark.asyncio
async def test_put_em_workspace_nao_confiado_responde_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path, trusted=False)

    with pytest.raises(HTTPException) as erro:
        await update_workspace_sandbox_policy("ws-1", SandboxPolicyBody())

    assert erro.value.status_code == 403
    assert not trusted_policy_path(tmp_path).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corpo",
    [
        {"rw_paths": ["relativo/pasta"]},
        {"ro_paths": ["../fora"]},
        {"allow_tcp_ports": [0]},
        {"allow_tcp_ports": [70000]},
        {"env_pass": ["COM-HIFEN"]},
    ],
)
async def test_put_recusa_valores_invalidos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpo: dict
) -> None:
    _register(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as erro:
        await update_workspace_sandbox_policy("ws-1", SandboxPolicyBody(**corpo))

    assert erro.value.status_code == 400
    assert not trusted_policy_path(tmp_path).exists()


@pytest.mark.asyncio
async def test_get_lista_o_que_o_repositorio_tentou_ampliar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    (tmp_path / "vectora.toml").write_text('[sandbox]\nrw_paths = ["/etc"]\n')

    resp = await workspace_sandbox_policy("ws-1")

    assert resp.rw_paths == []
    assert resp.ignored_repo_keys == ["rw_paths"]


@pytest.mark.asyncio
async def test_status_tambem_expoe_as_chaves_ignoradas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    (tmp_path / "vectora.toml").write_text('[sandbox]\nenv_pass = ["AWS_SECRET"]\n')

    resp = await workspace_sandbox_status("ws-1")

    assert resp.enabled is True
    assert resp.ignored_repo_keys == ["env_pass"]


@pytest.mark.asyncio
async def test_put_e_get_concordam_e_a_politica_vale_na_proxima_resolucao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)

    await update_workspace_sandbox_policy(
        "ws-1", SandboxPolicyBody(lockdown=True, mask=["segredo.txt"])
    )
    resp = await workspace_sandbox_policy("ws-1")

    assert resp.lockdown is True
    assert resp.mask == ["segredo.txt"]


@pytest.mark.asyncio
async def test_put_grava_rede_e_destinos_e_get_devolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)

    resp = await update_workspace_sandbox_policy(
        "ws-1",
        SandboxPolicyBody(
            network="filtered",
            allow_hosts=["api.exemplo.com", "*.cdn.exemplo.com"],
            registry_hosts=False,
        ),
    )
    lida = await workspace_sandbox_policy("ws-1")

    assert resp.network == lida.network == "filtered"
    assert lida.allow_hosts == ["api.exemplo.com", "*.cdn.exemplo.com"]
    assert lida.registry_hosts is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host", ["sem-ponto", "http://a.com", "a.com/caminho", "*", "a..com", "-a.com"]
)
async def test_put_recusa_destino_invalido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    _register(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as erro:
        await update_workspace_sandbox_policy(
            "ws-1", SandboxPolicyBody(allow_hosts=[host])
        )

    assert erro.value.status_code == 400


@pytest.mark.asyncio
async def test_rede_do_repositorio_que_amplia_aparece_como_ignorada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)
    (tmp_path / "vectora.toml").write_text('[sandbox]\nnetwork = "open"\n')

    resp = await workspace_sandbox_policy("ws-1")

    assert resp.network == "filtered"
    assert "network" in resp.ignored_repo_keys


@pytest.mark.asyncio
async def test_put_grava_os_limites_de_recursos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, tmp_path)

    resp = await update_workspace_sandbox_policy(
        "ws-1",
        SandboxPolicyBody(max_file_mb=100, memory_limit_mb=2048, tmp_size_mb=256),
    )
    lida = await workspace_sandbox_policy("ws-1")

    assert (resp.max_file_mb, resp.memory_limit_mb, resp.tmp_size_mb) == (
        100,
        2048,
        256,
    )
    assert (lida.max_file_mb, lida.memory_limit_mb, lida.tmp_size_mb) == (
        100,
        2048,
        256,
    )


def test_limites_fora_da_faixa_sao_recusados_pelo_contrato() -> None:
    from pydantic import ValidationError

    for corpo in (
        {"max_file_mb": 0},
        {"memory_limit_mb": 10},
        {"tmp_size_mb": 1},
        {"tmp_size_mb": 1_000_000},
    ):
        with pytest.raises(ValidationError):
            SandboxPolicyBody.model_validate(corpo)
