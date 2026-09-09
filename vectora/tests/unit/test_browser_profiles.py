from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from backend.services.browser_profiles import BrowserProfileStore, BrowserScope


@pytest.mark.asyncio
async def test_perfis_isolam_proprietarios_e_validam_escopo(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path)
    profile = await store.create(
        "alice", "Trabalho", scope="workspace", scope_target="ws-1", locale="pt-BR"
    )
    assert [item.profile_id for item in await store.list_profiles("alice")] == [
        profile.profile_id
    ]
    assert await store.list_profiles("bob") == []
    with pytest.raises(ValueError):
        await store.create("alice", "Inválido", scope=cast("BrowserScope", "other"))
    with pytest.raises(KeyError):
        await store.delete("bob", profile.profile_id)


@pytest.mark.asyncio
async def test_perfil_scoped_nao_vaza_para_outro_workspace(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path)
    profile = await store.create("alice", "Trabalho", scope_target="ws-1")
    assert [item.profile_id for item in await store.list_profiles("alice", "ws-1")] == [
        profile.profile_id
    ]
    assert await store.list_profiles("alice", "ws-2") == []
    with pytest.raises(KeyError):
        await store.resolve("alice", profile.profile_id, "ws-2", "workspace")


@pytest.mark.asyncio
async def test_resolve_exige_o_tipo_de_escopo(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path)
    profile = await store.create(
        "alice", "Sessão", scope="session", scope_target="thread-1"
    )
    with pytest.raises(KeyError):
        await store.resolve("alice", profile.profile_id, "thread-1", "workspace")
    assert (
        await store.resolve("alice", profile.profile_id, "thread-1", "session")
    ) == profile
    with pytest.raises(ValueError, match="scope inválido"):
        await store.resolve(
            "alice", profile.profile_id, "thread-1", cast("BrowserScope", None)
        )


@pytest.mark.asyncio
async def test_perfil_legado_sem_expiracao_respeita_retencao(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path)
    profile = await store.create("alice", "Legado", scope_target="ws-1")
    profile.expires_at = None
    profile.created_at = "2000-01-01T00:00:00+00:00"
    store._write_sync([profile])
    assert await store.list_profiles("alice", "ws-1") == []


@pytest.mark.asyncio
async def test_store_rejeita_escopo_persistido_invalido(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path)
    store._root.mkdir(parents=True, exist_ok=True)
    store._index.write_text(
        '[{"profile_id":"p1","owner_id":"alice","scope":"other","name":"Inválido"}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="escopo persistido inválido"):
        await store.list_profiles("alice")


@pytest.mark.asyncio
async def test_store_instances_preservam_mutacoes_concorrentes(tmp_path: Path) -> None:
    first = BrowserProfileStore(tmp_path)
    second = BrowserProfileStore(tmp_path)
    created = await asyncio.gather(
        first.create("alice", "Um", scope_target="ws-1"),
        second.create("alice", "Dois", scope_target="ws-1"),
    )
    assert {profile.name for profile in created} == {"Um", "Dois"}
    assert len(await first.list_profiles("alice", "ws-1")) == 2


@pytest.mark.asyncio
async def test_perfil_expirado_e_removido_ao_listar(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path)
    profile = await store.create("alice", "Temporário", scope_target="ws-1")
    profile.expires_at = "2000-01-01T00:00:00+00:00"
    store._write_sync([profile])
    assert await store.list_profiles("alice", "ws-1") == []
