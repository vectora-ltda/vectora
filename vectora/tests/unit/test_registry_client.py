"""registry_client: cliente HTTP do registry remoto de MCP/Skills, com cache
local TTL e fallback offline gracioso — mesmo padrão de `license.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.services import registry_client


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_client, "CACHE_DIR", tmp_path / "registry_cache")


@pytest.mark.asyncio
async def test_fetch_catalog_success_writes_cache_and_returns_entries(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"entries": [{"id": "filesystem"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("mcp")

    assert entries == [{"id": "filesystem"}]
    cache_path = registry_client._cache_path("mcp")
    assert cache_path.is_file()
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["entries"] == [{"id": "filesystem"}]


@pytest.mark.asyncio
async def test_fetch_catalog_network_error_falls_back_to_existing_cache(monkeypatch):
    registry_client._write_cache("mcp", [{"id": "cached-entry"}])

    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("mcp")

    assert entries == [{"id": "cached-entry"}]


@pytest.mark.asyncio
async def test_fetch_catalog_no_network_no_cache_returns_empty_list_not_exception(
    monkeypatch,
):
    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("skills")

    assert entries == []


@pytest.mark.asyncio
async def test_fetch_catalog_cache_within_offline_ttl_but_past_online_ttl_is_served(
    monkeypatch,
):
    # Dentro do TTL de 48h (offline) mas fora do TTL de 6h (online) — ainda
    # é servido: "stale mas utilizável" é o contrato de fallback gracioso.
    stale_but_usable = {
        "entries": [{"id": "old-but-usable"}],
        "fetched_at": (datetime.now(UTC) - timedelta(hours=20)).isoformat(),
    }
    registry_client._cache_path("mcp").parent.mkdir(parents=True, exist_ok=True)
    registry_client._cache_path("mcp").write_text(
        json.dumps(stale_but_usable), encoding="utf-8"
    )

    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("mcp")

    assert entries == [{"id": "old-but-usable"}]


@pytest.mark.asyncio
async def test_fetch_catalog_cache_expired_beyond_offline_ttl_is_not_used(
    monkeypatch,
):
    # Erro/borda: cache além de 48h não é mais confiável — deve degradar pra
    # lista vazia em vez de servir dado potencialmente muito desatualizado.
    expired = {
        "entries": [{"id": "too-old"}],
        "fetched_at": (datetime.now(UTC) - timedelta(hours=100)).isoformat(),
    }
    registry_client._cache_path("mcp").parent.mkdir(parents=True, exist_ok=True)
    registry_client._cache_path("mcp").write_text(json.dumps(expired), encoding="utf-8")

    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("mcp")

    assert entries == []


def test_clear_registry_cache_removes_all_kinds():
    registry_client._write_cache("mcp", [{"id": "a"}])
    registry_client._write_cache("skills", [{"id": "b"}])
    registry_client._write_cache("mcp_official", [{"id": "c"}])

    registry_client.clear_registry_cache()

    assert not registry_client._cache_path("mcp").exists()
    assert not registry_client._cache_path("skills").exists()
    assert not registry_client._cache_path("mcp_official").exists()


# ---------------------------------------------------------------------------
# fetch_official_mcp_registry: catálogo oficial (registry.modelcontextprotocol.io)
# — só servers com pacote npm/stdio são instaláveis pelo fluxo atual.
# ---------------------------------------------------------------------------


def _official_page(servers: list[dict], next_cursor: str | None = None) -> dict:
    return {
        "servers": servers,
        "metadata": {"nextCursor": next_cursor, "count": len(servers)},
    }


def _npm_stdio_server(
    name: str, identifier: str, required_env: list[str] | None = None
) -> dict:
    return {
        "server": {
            "name": name,
            "title": name.rsplit("/", 1)[-1],
            "description": f"desc de {name}",
            "repository": {"url": f"https://github.com/example/{identifier}"},
            "packages": [
                {
                    "registryType": "npm",
                    "identifier": identifier,
                    "transport": {"type": "stdio"},
                    "environmentVariables": [
                        {"name": ev, "isRequired": True} for ev in (required_env or [])
                    ],
                }
            ],
        },
    }


def _remote_only_server(name: str) -> dict:
    return {
        "server": {
            "name": name,
            "description": "server remoto, sem pacote instalável",
            "remotes": [{"type": "streamable-http", "url": "https://example.com/mcp"}],
        },
    }


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_maps_npm_stdio_servers(monkeypatch):
    page = _official_page(
        [_npm_stdio_server("com.example/foo", "foo-mcp-server", ["FOO_API_KEY"])]
    )

    async def _fake_get(self, url, params=None, **kwargs):
        return httpx.Response(200, json=page, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert len(entries) == 1
    entry = entries[0]
    assert entry["id"] == "com.example/foo"
    assert entry["install_cmd"] == "npx -y foo-mcp-server"
    assert entry["env_vars"] == ["FOO_API_KEY"]
    assert entry["homepage"] == "https://github.com/example/foo-mcp-server"


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_skips_remote_only_servers(monkeypatch):
    # Erro/borda: servers sem pacote npm/stdio (só `remotes`) não são
    # instaláveis pelo fluxo atual — devem ser filtrados, não quebrar.
    page = _official_page(
        [
            _remote_only_server("com.example/remote-only"),
            _npm_stdio_server("com.example/bar", "bar-mcp-server"),
        ]
    )

    async def _fake_get(self, url, params=None, **kwargs):
        return httpx.Response(200, json=page, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert [e["id"] for e in entries] == ["com.example/bar"]


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_paginates_via_cursor(monkeypatch):
    page1 = _official_page(
        [_npm_stdio_server("com.example/a", "a-mcp")], next_cursor="com.example/a"
    )
    page2 = _official_page(
        [_npm_stdio_server("com.example/b", "b-mcp")], next_cursor=None
    )
    calls: list[dict] = []

    async def _fake_get(self, url, params=None, **kwargs):
        calls.append(dict(params or {}))
        page = page1 if len(calls) == 1 else page2
        return httpx.Response(200, json=page, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert [e["id"] for e in entries] == ["com.example/a", "com.example/b"]
    assert len(calls) == 2
    assert calls[1]["cursor"] == "com.example/a"


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_network_error_falls_back_to_cache(
    monkeypatch,
):
    registry_client._write_cache("mcp_official", [{"id": "cached"}])

    async def _fake_get(self, url, params=None, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert entries == [{"id": "cached"}]


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_expired_cache_returns_empty_list(
    monkeypatch,
):
    expired = {
        "entries": [{"id": "cached-too-old"}],
        "fetched_at": (datetime.now(UTC) - timedelta(hours=200)).isoformat(),
    }
    registry_client._cache_path("mcp_official").parent.mkdir(
        parents=True, exist_ok=True
    )
    registry_client._cache_path("mcp_official").write_text(
        json.dumps(expired), encoding="utf-8"
    )

    async def _fake_get(self, url, params=None, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert entries == []


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_duplicate_id_across_pages_not_duplicated(
    monkeypatch,
):
    # Duplicado: o mesmo id aparecendo em duas páginas (ou pacotes distintos
    # do mesmo server) não deve virar entrada repetida — dict por id dedupe.
    page1 = _official_page(
        [_npm_stdio_server("com.example/dup", "dup-mcp-v1")],
        next_cursor="cursor-1",
    )
    page2 = _official_page(
        [_npm_stdio_server("com.example/dup", "dup-mcp-v2")], next_cursor=None
    )
    calls: list[dict] = []

    async def _fake_get(self, url, params=None, **kwargs):
        calls.append(dict(params or {}))
        page = page1 if len(calls) == 1 else page2
        return httpx.Response(200, json=page, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert len(entries) == 1
    # Última ocorrência vence (mesmo comportamento de dict[id] = connector).
    assert entries[0]["install_cmd"] == "npx -y dup-mcp-v2"


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_no_network_no_cache_returns_empty(
    monkeypatch,
):
    async def _fake_get(self, url, params=None, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert entries == []


@pytest.mark.asyncio
async def test_fetch_catalog_corrupted_cache_file_is_ignored_not_raised(monkeypatch):
    # Erro/borda: cache local corrompido (JSON inválido) não deve quebrar o
    # fluxo — é tratado como "sem cache" e a falha de rede vira lista vazia.
    registry_client._cache_path("mcp").parent.mkdir(parents=True, exist_ok=True)
    registry_client._cache_path("mcp").write_text("{not valid json", encoding="utf-8")

    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("mcp")

    assert entries == []


@pytest.mark.asyncio
async def test_fetch_catalog_empty_entries_from_remote_overwrites_cache(monkeypatch):
    # Cache expirado (fora do TTL online) — precisa tocar rede, não é servido
    # pelo fast path cache-first.
    stale = {
        "entries": [{"id": "old"}],
        "fetched_at": (datetime.now(UTC) - timedelta(hours=20)).isoformat(),
    }
    registry_client._cache_path("skills").parent.mkdir(parents=True, exist_ok=True)
    registry_client._cache_path("skills").write_text(
        json.dumps(stale), encoding="utf-8"
    )

    async def _fake_get(self, url, **kwargs):
        return httpx.Response(
            200, json={"entries": []}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("skills")

    assert entries == []
    cache_path = registry_client._cache_path("skills")
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["entries"] == []


@pytest.mark.asyncio
async def test_fetch_catalog_fresh_cache_is_served_without_touching_network(
    monkeypatch,
):
    registry_client._write_cache("mcp", [{"id": "fresh-cached"}])
    called = False

    async def _fake_get(self, url, **kwargs):
        nonlocal called
        called = True
        return httpx.Response(
            200,
            json={"entries": [{"id": "should-not-be-used"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("mcp")

    assert entries == [{"id": "fresh-cached"}]
    assert called is False


@pytest.mark.asyncio
async def test_fetch_official_mcp_registry_fresh_cache_is_served_without_network(
    monkeypatch,
):
    registry_client._write_cache("mcp_official", [{"id": "fresh-cached"}])
    called = False

    async def _fake_get(self, url, params=None, **kwargs):
        nonlocal called
        called = True
        return httpx.Response(
            200, json={"servers": []}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_official_mcp_registry()

    assert entries == [{"id": "fresh-cached"}]
    assert called is False


@pytest.mark.asyncio
async def test_fetch_catalog_http_error_status_falls_back_to_cache(monkeypatch):
    registry_client._write_cache("mcp", [{"id": "cached-entry"}])

    async def _fake_get(self, url, **kwargs):
        return httpx.Response(
            500, json={"error": "internal"}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    entries = await registry_client.fetch_catalog("mcp")

    assert entries == [{"id": "cached-entry"}]


def test_clear_registry_cache_when_nothing_cached_does_not_raise():
    # Erro/borda: limpar cache sem nenhum arquivo existente é no-op seguro.
    registry_client.clear_registry_cache()

    assert not registry_client._cache_path("mcp").exists()
