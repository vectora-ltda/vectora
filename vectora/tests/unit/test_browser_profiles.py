from __future__ import annotations

import pytest

from backend.services.browser_profiles import BrowserProfileStore


@pytest.mark.asyncio
async def test_perfis_isolam_proprietarios_e_validam_escopo(tmp_path) -> None:
    store = BrowserProfileStore(tmp_path)
    profile = await store.create("alice", "Trabalho", scope="workspace", locale="pt-BR")
    assert [item.profile_id for item in await store.list_profiles("alice")] == [
        profile.profile_id
    ]
    assert await store.list_profiles("bob") == []
    with pytest.raises(ValueError):
        await store.create("alice", "Inválido", scope="other")
    with pytest.raises(KeyError):
        await store.delete("bob", profile.profile_id)
