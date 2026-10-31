"""Tests para src/services/tool_policy.py — política de tools por usuário (S5).

Allow-all por padrão; admin/self podem desabilitar tools por usuário. Persistido
por arquivo, isolado entre usuários. Diretório base redirecionado para tmp_path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from backend.persistence.native.session_store import SessionStore
from backend.rbac import tool_policy
from backend.services.policy_compiler import PolicySnapshot
from backend.storage.sqlite.pool import AsyncConnectionPool


@pytest.fixture(autouse=True)
def iso_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_policy, "_policy_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(tool_policy, "_versions", {})
    monkeypatch.setattr(tool_policy, "_version_store", None)


@pytest.fixture
async def version_store(tmp_path) -> AsyncIterator[SessionStore]:
    pool = AsyncConnectionPool(str(tmp_path / "sessions.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    tool_policy.bind_version_store(store)
    try:
        yield store
    finally:
        tool_policy.bind_version_store(None)
        await pool.close()


async def test_allow_all_by_default():
    assert tool_policy.is_allowed("u1", "terminal") is True
    assert tool_policy.get_disabled("u1") == []


async def test_disable_tool():
    await tool_policy.set_disabled("u1", ["terminal"])
    assert tool_policy.is_allowed("u1", "terminal") is False
    assert tool_policy.is_allowed("u1", "file_read") is True


async def test_get_disabled_returns_list():
    await tool_policy.set_disabled("u1", ["terminal", "file_write"])
    assert set(tool_policy.get_disabled("u1")) == {"terminal", "file_write"}


async def test_reenable_by_setting_empty():
    await tool_policy.set_disabled("u1", ["terminal"])
    await tool_policy.set_disabled("u1", [])
    assert tool_policy.is_allowed("u1", "terminal") is True


async def test_users_isolated():
    await tool_policy.set_disabled("a", ["terminal"])
    assert tool_policy.is_allowed("a", "terminal") is False
    assert tool_policy.is_allowed("b", "terminal") is True


async def test_persists_across_reads():
    await tool_policy.set_disabled("u1", ["grep"])
    # Releitura vai ao disco
    assert tool_policy.get_disabled("u1") == ["grep"]


# ---------------------------------------------------------------------------
# GLOBAL_SCOPE — kill-switch do admin (aplica a todas as sessões, mesmo local)
# ---------------------------------------------------------------------------


async def test_global_disable_blocks_any_user():
    await tool_policy.set_disabled(tool_policy.GLOBAL_SCOPE, ["terminal"])
    assert tool_policy.is_allowed("u1", "terminal") is False
    assert tool_policy.is_allowed("u2", "terminal") is False
    assert tool_policy.is_allowed("local", "terminal") is False


async def test_global_disable_does_not_block_other_tools():
    await tool_policy.set_disabled(tool_policy.GLOBAL_SCOPE, ["terminal"])
    assert tool_policy.is_allowed("u1", "file_read") is True


async def test_global_reenable_by_setting_empty():
    await tool_policy.set_disabled(tool_policy.GLOBAL_SCOPE, ["terminal"])
    await tool_policy.set_disabled(tool_policy.GLOBAL_SCOPE, [])
    assert tool_policy.is_allowed("u1", "terminal") is True


async def test_effective_disabled_unions_global_and_user():
    await tool_policy.set_disabled(tool_policy.GLOBAL_SCOPE, ["terminal"])
    await tool_policy.set_disabled("u1", ["grep"])
    assert tool_policy.effective_disabled("u1") == {"terminal", "grep"}
    # Outro usuário não herda o disable pessoal de u1, só o global.
    assert tool_policy.effective_disabled("u2") == {"terminal"}


async def test_effective_disabled_without_user_id_is_global_only():
    await tool_policy.set_disabled(tool_policy.GLOBAL_SCOPE, ["terminal"])
    assert tool_policy.effective_disabled(None) == {"terminal"}


async def test_effective_disabled_empty_by_default():
    assert tool_policy.effective_disabled("u1") == set()
    assert tool_policy.effective_disabled(None) == set()


async def test_versao_gravada_no_banco_sobrevive_ao_reinicio(
    version_store: SessionStore,
) -> None:
    await tool_policy.set_disabled("u1", ["terminal"])
    await tool_policy.set_disabled("u1", [])
    assert tool_policy.policy_version("u1") == 2
    assert await version_store.get_tool_policy_versions() == {"u1": 2}

    tool_policy._versions.clear()
    assert tool_policy.policy_version("u1") == 0
    await tool_policy.sync_persisted_versions()
    assert tool_policy.policy_version("u1") == 2


async def test_revogacao_feita_em_outra_replica_invalida_snapshot_existente(
    version_store: SessionStore,
) -> None:
    await tool_policy.set_disabled("u1", ["terminal"])
    snapshot = PolicySnapshot(
        user_id="u1",
        disabled_tools=frozenset({"terminal"}),
        user_policy_version=tool_policy.policy_version("u1"),
    )
    assert snapshot.is_revoked() is False

    # Outra réplica grava no banco sem que esta receba o pubsub.
    await version_store.bump_tool_policy_version("u1")
    assert snapshot.is_revoked() is False

    await tool_policy.sync_persisted_versions()
    assert snapshot.is_revoked() is True


async def test_falha_do_banco_mantem_ultima_versao_lida(
    version_store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await tool_policy.set_disabled("u1", ["terminal"])
    assert tool_policy.policy_version("u1") == 1

    async def _indisponivel(*_args: object) -> dict[str, int]:
        msg = "banco fora do ar"
        raise ConnectionError(msg)

    monkeypatch.setattr(version_store, "get_tool_policy_versions", _indisponivel)
    await tool_policy.sync_persisted_versions()
    assert tool_policy.policy_version("u1") == 1


async def test_set_disabled_com_banco_fora_ainda_grava_arquivo_e_avanca_local(
    version_store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _indisponivel(*_args: object) -> int:
        msg = "banco fora do ar"
        raise ConnectionError(msg)

    monkeypatch.setattr(version_store, "bump_tool_policy_version", _indisponivel)
    await tool_policy.set_disabled("u1", ["terminal"])
    assert tool_policy.is_allowed("u1", "terminal") is False
    assert tool_policy.policy_version("u1") == 1
