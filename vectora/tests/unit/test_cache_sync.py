"""Testes da sincronização de caches entre réplicas (cache_sync)."""

from __future__ import annotations

import json
from typing import cast

import pytest

from backend.embedding import cache_sync
from backend.persistence.kv import MemoryKV, get_kv, kv_initialized, reset_kv
from backend.rbac import tool_policy
from backend.tools.registry import ToolSpec
from backend.workspace import plugins


@pytest.fixture(autouse=True)
def _isolado(monkeypatch: pytest.MonkeyPatch, _no_nats_sidecar):
    from backend.settings import settings

    monkeypatch.setattr(settings, "redis_url", None)
    reset_kv()
    # Estado limpo dos caches de versão.
    monkeypatch.setattr(plugins, "_versions", {})
    monkeypatch.setattr(plugins, "_mcp_tools_cache", {})
    monkeypatch.setattr(tool_policy, "_versions", {})
    yield
    reset_kv()


@pytest.mark.asyncio
async def test_start_cache_sync_registra_subscribers() -> None:
    await cache_sync.start_cache_sync()
    kv = await get_kv()
    assert isinstance(kv, MemoryKV)
    assert cache_sync.CHANNEL_TOOLS in kv._subs
    assert cache_sync.CHANNEL_POLICY in kv._subs
    assert cache_sync.CHANNEL_WS_ACTIVE in kv._subs


@pytest.mark.asyncio
async def test_start_cache_sync_registra_bridge_sse() -> None:
    """O bridge de SSE cross-réplica (webhooks.CHANNEL_SSE) precisa se
    inscrever ANTES do primeiro kv.start() — start_cache_sync é o único
    ponto que chama isso, então é onde essa inscrição precisa acontecer
    (registrar depois não teria efeito: o reader já capturou os canais)."""
    from backend.api.handlers.webhooks import CHANNEL_SSE

    await cache_sync.start_cache_sync()
    kv = await get_kv()
    assert CHANNEL_SSE in kv._subs


@pytest.mark.asyncio
async def test_tools_changed_avanca_versao_e_dropa_cache() -> None:
    await cache_sync.start_cache_sync()
    plugins._mcp_tools_cache[("u1", None)] = (
        0,
        0,
        cast("list[ToolSpec]", ["tool_antiga"]),
    )

    await (await get_kv()).publish(
        cache_sync.CHANNEL_TOOLS, json.dumps({"user_id": "u1", "version": 5})
    )
    assert plugins.tools_version("u1") == 5
    assert ("u1", None) not in plugins._mcp_tools_cache


@pytest.mark.asyncio
async def test_tools_changed_versao_antiga_e_noop() -> None:
    await cache_sync.start_cache_sync()
    plugins._versions["u1"] = 10
    plugins._mcp_tools_cache[("u1", None)] = (
        10,
        0,
        cast("list[ToolSpec]", ["tool"]),
    )

    await (await get_kv()).publish(
        cache_sync.CHANNEL_TOOLS, json.dumps({"user_id": "u1", "version": 3})
    )
    # Versão menor não regride nem dropa o cache (evita eco do próprio bump).
    assert plugins.tools_version("u1") == 10
    assert ("u1", None) in plugins._mcp_tools_cache


@pytest.mark.asyncio
async def test_tools_changed_scoped_invalida_cache_mesmo_com_versao_antiga() -> None:
    await cache_sync.start_cache_sync()
    plugins._versions["u1"] = 10
    scoped_key = ("u2", None, "workspace-1", None, None)
    plugins._mcp_tools_cache[scoped_key] = (
        10,
        0,
        cast("list[ToolSpec]", ["tool"]),
    )

    await (await get_kv()).publish(
        cache_sync.CHANNEL_TOOLS,
        json.dumps(
            {
                "user_id": "u1",
                "version": 3,
                "scope": "workspace",
                "target": "workspace-1",
            }
        ),
    )

    assert plugins.tools_version("u1") == 10
    assert scoped_key not in plugins._mcp_tools_cache


@pytest.mark.asyncio
async def test_policy_changed_avanca_versao() -> None:
    await cache_sync.start_cache_sync()
    await (await get_kv()).publish(
        cache_sync.CHANNEL_POLICY, json.dumps({"user_id": "u2", "version": 2})
    )
    assert tool_policy.policy_version("u2") == 2


@pytest.mark.asyncio
async def test_ws_active_changed_aplica_no_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.workspace.workspace import workspace_registry

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        workspace_registry,
        "apply_remote_active",
        lambda wid, uid: calls.append((wid, uid)),
    )
    await cache_sync.start_cache_sync()
    await (await get_kv()).publish(
        cache_sync.CHANNEL_WS_ACTIVE,
        json.dumps({"user_id": "u3", "workspace_id": "ws-9"}),
    )
    assert calls == [("ws-9", "u3")]


@pytest.mark.asyncio
async def test_payload_invalido_e_ignorado() -> None:
    await cache_sync.start_cache_sync()
    # Nenhuma exceção deve escapar para o publisher.
    await (await get_kv()).publish(cache_sync.CHANNEL_TOOLS, "não-é-json")
    await (await get_kv()).publish(cache_sync.CHANNEL_TOOLS, json.dumps({"version": 1}))


class TestStopCacheSyncSemInicializar:
    """`stop_cache_sync` roda no shutdown — sem `start_cache_sync` (ou outro
    caller) ter chamado `get_kv()` antes, não pode inicializar o KV do zero
    só pra fechá-lo em seguida (subiria o sidecar NATS sem necessidade)."""

    @pytest.mark.asyncio
    async def test_nao_inicializa_kv_quando_nunca_foi_usado(self) -> None:
        assert kv_initialized() is False

        await cache_sync.stop_cache_sync()

        assert kv_initialized() is False

    @pytest.mark.asyncio
    async def test_fecha_o_kv_quando_ja_estava_inicializado(self) -> None:
        await cache_sync.start_cache_sync()
        assert kv_initialized() is True

        await cache_sync.stop_cache_sync()

        # MemoryKV.close() não reseta o singleton — só confirma que rodou
        # sem levantar, sobre o KV já existente (não um novo).
        assert kv_initialized() is True
