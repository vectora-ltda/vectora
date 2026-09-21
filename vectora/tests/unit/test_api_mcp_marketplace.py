"""Contract tests for the MCP marketplace's canonical catalog flow."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Request

from backend.api.handlers.mcp_marketplace import (
    InstallRequest,
    MCPConnector,
    UninstallRequest,
    _connector_to_server,
    _filter_registry,
    _req_user_id,
    get_registry,
    install_mcp,
    list_registry,
    uninstall_mcp,
)


def _entry(
    identifier: str, name: str, *, stars: int = 0, downloads: int = 0
) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "description": f"{name} description",
        "install_cmd": f"npx -y {identifier}",
        "env_vars": [],
        "homepage": f"https://github.com/example/{identifier}",
        "category": "community",
        "publisher": "example",
        "publisher_url": "https://github.com/example",
        "icon_url": "https://example.com/icon.png",
        "stars_count": stars,
        "downloads_count": downloads,
        "runtime_hint": "npx",
        "package_identifier": identifier,
        "transport": "stdio",
    }


@pytest.fixture
def _catalog(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    from backend.api.handlers import mcp_marketplace

    entries = [_entry("alpha", "Alpha", stars=10), _entry("beta", "Beta", stars=20)]
    monkeypatch.setattr(
        mcp_marketplace.registry_client,
        "fetch_catalog",
        AsyncMock(return_value=entries),
    )
    return entries


@pytest.fixture
def _functional_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _catalog: list[dict[str, object]],
) -> ModuleType:
    from backend.workspace import plugins

    monkeypatch.setattr(plugins, "_plugins_dir", lambda: tmp_path / "mcp")
    monkeypatch.setattr(plugins, "_versions", {})
    monkeypatch.setattr(plugins, "_mcp_tools_cache", {})
    monkeypatch.setattr(plugins, "publish_soon", lambda *a, **k: None, raising=False)
    return plugins


@pytest.mark.asyncio
async def test_list_registry_uses_only_canonical_catalog(
    _catalog: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api.handlers import mcp_marketplace

    official = AsyncMock(side_effect=AssertionError("raw registry must not be used"))
    monkeypatch.setattr(
        mcp_marketplace.registry_client, "fetch_official_mcp_registry", official
    )
    result = await list_registry()
    assert [entry.id for entry in result] == ["beta", "alpha"]
    official.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_registry_preserves_metadata_and_filters_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import mcp_marketplace

    monkeypatch.setattr(
        mcp_marketplace.registry_client,
        "fetch_catalog",
        AsyncMock(
            return_value=[_entry("valid", "Valid", stars=5), {"name": "missing id"}]
        ),
    )
    result = await list_registry()
    assert len(result) == 1
    assert result[0].icon_url == "https://example.com/icon.png"
    assert result[0].publisher == "example"
    assert result[0].stars_count == 5


@pytest.mark.asyncio
async def test_list_registry_keeps_catalog_curator_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import mcp_marketplace

    entry = _entry("curated", "Curated")
    entry["vectora_verified"] = True
    monkeypatch.setattr(
        mcp_marketplace.registry_client,
        "fetch_catalog",
        AsyncMock(return_value=[entry]),
    )
    assert (await list_registry())[0].vectora_verified is True


class TestFilterRegistry:
    def test_sem_filtro_devolve_tudo(self):
        connectors = [
            MCPConnector(id="a", name="Alpha", description="d1", category="web")
        ]
        assert _filter_registry(connectors, q=None, category=None) == connectors

    def test_filtra_nome_descricao_e_categoria(self):
        connectors = [
            MCPConnector(
                id="a", name="Brave Search", description="web", category="web"
            ),
            MCPConnector(id="b", name="Postgres", description="banco", category="db"),
        ]
        assert [
            c.id for c in _filter_registry(connectors, q="BRAVE", category=None)
        ] == ["a"]
        assert [c.id for c in _filter_registry(connectors, q=None, category="db")] == [
            "b"
        ]


@pytest.mark.asyncio
async def test_get_registry_aplica_filtro_de_query(_catalog):
    result = await get_registry(q="brave")
    assert result == []


@pytest.mark.asyncio
async def test_install_resolves_only_catalog_entry(_functional_store):
    result = await install_mcp(InstallRequest(mcp_id="alpha", confirm_unverified=True))
    assert result["status"] == "installed"
    assert any(
        server.name == "alpha" for server in _functional_store.list_servers("local")
    )


@pytest.mark.asyncio
async def test_install_unknown_connector_returns_error(_functional_store):
    result = await install_mcp(InstallRequest(mcp_id="missing"))
    assert result["status"] == "error"
    assert _functional_store.list_servers("local") == []


@pytest.mark.asyncio
async def test_uninstall_removes_from_functional_store(_functional_store):
    await install_mcp(InstallRequest(mcp_id="alpha", confirm_unverified=True))
    removed = await uninstall_mcp(UninstallRequest(mcp_id="alpha"))
    assert removed["status"] == "removed"
    assert await uninstall_mcp(UninstallRequest(mcp_id="alpha")) == {
        "status": "not_found",
        "mcp_id": "alpha",
    }


def test_connector_to_server_supports_http_transport():
    server = _connector_to_server(
        MCPConnector(
            id="remote",
            name="Remote",
            description="x",
            transport="http",
            server_url="https://example.com/mcp",
        )
    )
    assert server.command == ""
    assert server.url == "https://example.com/mcp"


def test_connector_to_server_com_install_cmd_vazio():
    server = _connector_to_server(
        MCPConnector(id="custom", name="Custom", description="x")
    )
    assert server.command == "npx"
    assert server.args == []


def test_req_user_id_extrai_do_request_state_ou_usa_fallback_local():
    authenticated = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="uuid-123"))
    )
    anonymous = SimpleNamespace(state=SimpleNamespace(user=None))
    assert _req_user_id(cast("Request", authenticated)) == "uuid-123"
    assert _req_user_id(cast("Request", anonymous)) == "local"


@pytest.mark.asyncio
async def test_install_uninstall_tratam_excecao_do_store(
    _functional_store, monkeypatch
):
    def boom_add(*args, **kwargs):
        raise RuntimeError("disco cheio")

    def boom_remove(*args, **kwargs):
        raise RuntimeError("arquivo corrompido")

    monkeypatch.setattr(_functional_store, "add_server", boom_add)
    out_install = await install_mcp(
        InstallRequest(mcp_id="alpha", confirm_unverified=True)
    )
    assert out_install["status"] == "error"
    assert "disco cheio" in out_install["error"]

    monkeypatch.setattr(_functional_store, "remove_server", boom_remove)
    out_uninstall = await uninstall_mcp(UninstallRequest(mcp_id="alpha"))
    assert out_uninstall["status"] == "error"
    assert "arquivo corrompido" in out_uninstall["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["blocked", "policy_unavailable"])
async def test_install_audita_negacao_da_allowlist(
    _functional_store, monkeypatch, code
):
    from backend.api.handlers import mcp_marketplace
    from backend.services.mcp_policy import McpPolicyDecision

    audit = AsyncMock()
    monkeypatch.setattr(mcp_marketplace, "_audit_mcp_decision", audit)
    monkeypatch.setattr(
        mcp_marketplace.mcp_policy,
        "evaluate",
        lambda *_args: McpPolicyDecision(
            allowed=False, code=code, scope="instance", version=3
        ),
    )
    request = SimpleNamespace(state=SimpleNamespace(user=None), query_params={})
    result = await install_mcp(
        InstallRequest(mcp_id="alpha"), request=cast("Request", request)
    )
    assert result["status"] == "error"
    assert result["code"] == (
        "policy_unavailable" if code == "policy_unavailable" else "policy_blocked"
    )
    audit.assert_awaited_once()
