"""Tools de auto-instalação da Library: MCP/Skills/Memory Buckets.

Cobre `install_mcp_from_registry`, `install_skill_from_catalog` e
`install_memory_bucket` — happy path e erro/borda de cada uma, reaproveitando
a mesma lógica `_impl` que os handlers HTTP já expõem, sem duplicar.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.tools.context import ToolContext
from backend.tools.library import (
    delete_skill,
    install_mcp_from_registry,
    install_memory_bucket,
    install_skill_from_catalog,
    publish_memory_bucket_tool,
    publish_skill_tool,
    save_mcp_env_var,
    uninstall_mcp,
    verify_skill,
)


def _ctx(user_id: str = "local") -> ToolContext:
    return ToolContext(user_id=user_id)


# ---------------------------------------------------------------------------
# install_mcp_from_registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_mcp_from_registry_installs_connector_without_env_vars(
    monkeypatch,
):
    from backend.api.handlers import mcp_marketplace

    connector = mcp_marketplace.MCPConnector(
        id="filesystem",
        name="Filesystem",
        description="d",
        install_cmd="npx -y @modelcontextprotocol/server-filesystem",
        env_vars=[],
        vectora_verified=True,
    )
    monkeypatch.setattr(
        mcp_marketplace,
        "list_registry",
        AsyncMock(return_value=[connector]),
    )
    monkeypatch.setattr(
        mcp_marketplace,
        "install_mcp",
        AsyncMock(return_value={"status": "installed", "mcp_id": "filesystem"}),
    )

    result = json.loads(
        await install_mcp_from_registry(connector_id="filesystem", ctx=_ctx())
    )

    assert result == {"status": "installed", "mcp_id": "filesystem"}


@pytest.mark.asyncio
async def test_install_mcp_from_registry_missing_env_vars_does_not_install(
    monkeypatch,
):
    from backend.api.handlers import mcp_marketplace

    connector = mcp_marketplace.MCPConnector(
        id="brave-search",
        name="Brave Search",
        description="d",
        install_cmd="npx -y @modelcontextprotocol/server-brave-search",
        env_vars=["BRAVE_API_KEY_DOES_NOT_EXIST_XYZ"],
    )
    monkeypatch.setattr(
        mcp_marketplace, "list_registry", AsyncMock(return_value=[connector])
    )
    install_spy = AsyncMock()
    monkeypatch.setattr(mcp_marketplace, "install_mcp", install_spy)
    monkeypatch.delenv("BRAVE_API_KEY_DOES_NOT_EXIST_XYZ", raising=False)

    result = json.loads(
        await install_mcp_from_registry(connector_id="brave-search", ctx=_ctx())
    )

    assert result["status"] == "error"
    assert result["missing_env_vars"] == ["BRAVE_API_KEY_DOES_NOT_EXIST_XYZ"]
    install_spy.assert_not_called()


@pytest.mark.asyncio
async def test_install_mcp_from_registry_unknown_connector_returns_error(monkeypatch):
    from backend.api.handlers import mcp_marketplace

    monkeypatch.setattr(mcp_marketplace, "list_registry", AsyncMock(return_value=[]))

    result = json.loads(
        await install_mcp_from_registry(connector_id="does-not-exist", ctx=_ctx())
    )

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# install_skill_from_catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_skill_from_catalog_installs_skill(monkeypatch):
    from backend.services import registry_client
    from backend.workspace import skills as skills_mod

    monkeypatch.setattr(
        registry_client,
        "fetch_catalog",
        AsyncMock(
            return_value=[
                {
                    "id": "pdf-extract",
                    "name": "PDF Extract",
                    "source": "https://github.com/example/pdf-extract-skill",
                }
            ]
        ),
    )

    class _FakeSkill:
        id = "pdf-extract"

    monkeypatch.setattr(
        skills_mod, "install_skill", lambda user_id, source: _FakeSkill()
    )

    result = json.loads(
        await install_skill_from_catalog(skill_id="pdf-extract", ctx=_ctx())
    )

    assert result == {"status": "installed", "skill_id": "pdf-extract"}


@pytest.mark.asyncio
async def test_install_skill_from_catalog_unknown_skill_returns_error(monkeypatch):
    from backend.services import registry_client

    monkeypatch.setattr(registry_client, "fetch_catalog", AsyncMock(return_value=[]))

    result = json.loads(
        await install_skill_from_catalog(skill_id="does-not-exist", ctx=_ctx())
    )

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# install_memory_bucket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_memory_bucket_installs_collection(monkeypatch):
    from backend.services import memory_buckets

    monkeypatch.setattr(
        memory_buckets,
        "download_memory_bucket",
        AsyncMock(return_value="shared_docs-2024"),
    )

    result = json.loads(await install_memory_bucket(bucket_id="docs-2024"))

    assert result == {"status": "installed", "collection": "shared_docs-2024"}


@pytest.mark.asyncio
async def test_install_memory_bucket_error_returns_status_error_not_raised(
    monkeypatch,
):
    from backend.services import memory_buckets

    async def _boom(bucket_id: str) -> str:
        raise memory_buckets.MemoryBucketsError("embed_model incompatível")

    monkeypatch.setattr(memory_buckets, "download_memory_bucket", _boom)

    result = json.loads(await install_memory_bucket(bucket_id="docs-2024"))

    assert result["status"] == "error"
    assert "embed_model incompatível" in result["error"]


# ---------------------------------------------------------------------------
# uninstall_mcp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_mcp_removes_connector(monkeypatch):
    from backend.api.handlers import mcp_marketplace

    monkeypatch.setattr(
        mcp_marketplace,
        "uninstall_mcp",
        AsyncMock(return_value={"status": "removed", "mcp_id": "filesystem"}),
    )

    result = json.loads(await uninstall_mcp(connector_id="filesystem", ctx=_ctx()))

    assert result == {"status": "removed", "mcp_id": "filesystem"}


@pytest.mark.asyncio
async def test_uninstall_mcp_not_installed_returns_not_found(monkeypatch):
    from backend.api.handlers import mcp_marketplace

    monkeypatch.setattr(
        mcp_marketplace,
        "uninstall_mcp",
        AsyncMock(return_value={"status": "not_found", "mcp_id": "nope"}),
    )

    result = json.loads(await uninstall_mcp(connector_id="nope", ctx=_ctx()))

    assert result["status"] == "not_found"


# ---------------------------------------------------------------------------
# delete_skill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_skill_removes_installed_skill(monkeypatch):
    from backend.workspace import skills as skills_mod

    monkeypatch.setattr(skills_mod, "remove_skill", lambda user_id, skill_id: True)

    result = json.loads(await delete_skill(skill_id="pdf-extract", ctx=_ctx()))

    assert result == {"status": "removed", "skill_id": "pdf-extract"}


@pytest.mark.asyncio
async def test_delete_skill_unknown_skill_returns_error(monkeypatch):
    from backend.workspace import skills as skills_mod

    monkeypatch.setattr(skills_mod, "remove_skill", lambda user_id, skill_id: False)

    result = json.loads(await delete_skill(skill_id="does-not-exist", ctx=_ctx()))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# verify_skill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_skill_revalidates_and_returns_status(monkeypatch):
    from backend.workspace import skills as skills_mod

    monkeypatch.setattr(
        skills_mod,
        "verify_skill",
        lambda user_id, skill_id: {"status": "valid", "skill_id": skill_id},
    )

    result = json.loads(await verify_skill(skill_id="pdf-extract", ctx=_ctx()))

    assert result == {"status": "valid", "skill_id": "pdf-extract"}


@pytest.mark.asyncio
async def test_verify_skill_propagates_internal_error_as_typed_error(monkeypatch):
    from backend.workspace import skills as skills_mod

    def _boom(user_id, skill_id):
        raise FileNotFoundError("SKILL.md ausente")

    monkeypatch.setattr(skills_mod, "verify_skill", _boom)

    result = json.loads(await verify_skill(skill_id="pdf-extract", ctx=_ctx()))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# publish_memory_bucket_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_memory_bucket_tool_publishes_with_token(monkeypatch):
    from backend.services import license as license_service
    from backend.services import memory_buckets

    monkeypatch.setattr(license_service, "_get_token", lambda: "tok-123")
    monkeypatch.setattr(
        memory_buckets,
        "publish_memory_bucket",
        AsyncMock(return_value="remote-bucket-1"),
    )

    result = json.loads(
        await publish_memory_bucket_tool(
            bucket_id="docs-2024",
            name="Docs 2024",
            description="# Docs",
            license="MIT",
        )
    )

    assert result == {"status": "published", "bucket_id": "remote-bucket-1"}


@pytest.mark.asyncio
async def test_publish_memory_bucket_tool_no_token_returns_error_without_publishing(
    monkeypatch,
):
    from backend.services import license as license_service
    from backend.services import memory_buckets

    monkeypatch.setattr(license_service, "_get_token", lambda: None)
    publish_spy = AsyncMock()
    monkeypatch.setattr(memory_buckets, "publish_memory_bucket", publish_spy)

    result = json.loads(
        await publish_memory_bucket_tool(
            bucket_id="docs-2024",
            name="Docs 2024",
            description="# Docs",
        )
    )

    assert result["status"] == "error"
    publish_spy.assert_not_called()


# ---------------------------------------------------------------------------
# publish_skill_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_skill_tool_publishes_with_token(monkeypatch):
    from backend.services import license as license_service
    from backend.services import registry_client

    monkeypatch.setattr(license_service, "_get_token", lambda: "tok-123")
    publish_spy = AsyncMock(return_value="remote-skill-1")
    monkeypatch.setattr(registry_client, "publish_skill", publish_spy)

    result = json.loads(
        await publish_skill_tool(
            source="https://github.com/user/skill",
            name="Minha Skill",
            description="faz coisas",
            category="devtools",
            tags=["cli"],
        )
    )

    assert result == {"status": "published", "skill_id": "remote-skill-1"}
    publish_spy.assert_awaited_once_with(
        "Minha Skill",
        "faz coisas",
        "https://github.com/user/skill",
        category="devtools",
        tags=["cli"],
        session_token="tok-123",
    )


@pytest.mark.asyncio
async def test_publish_skill_tool_no_token_returns_error_without_publishing(
    monkeypatch,
):
    from backend.services import license as license_service
    from backend.services import registry_client

    monkeypatch.setattr(license_service, "_get_token", lambda: None)
    publish_spy = AsyncMock()
    monkeypatch.setattr(registry_client, "publish_skill", publish_spy)

    result = json.loads(
        await publish_skill_tool(
            source="https://github.com/user/skill",
            name="Minha Skill",
            description="faz coisas",
        )
    )

    assert result["status"] == "error"
    publish_spy.assert_not_called()


@pytest.mark.asyncio
async def test_publish_skill_tool_registry_error_returns_typed_error_not_exception(
    monkeypatch,
):
    from backend.services import license as license_service
    from backend.services import registry_client

    monkeypatch.setattr(license_service, "_get_token", lambda: "tok-123")
    monkeypatch.setattr(
        registry_client,
        "publish_skill",
        AsyncMock(side_effect=registry_client.RegistryClientError("source inválido")),
    )

    result = json.loads(
        await publish_skill_tool(
            source="não é url",
            name="x",
            description="y",
        )
    )

    assert result["status"] == "error"
    assert "inválido" in result["error"]


# ---------------------------------------------------------------------------
# save_mcp_env_var
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_mcp_env_var_persists_override(monkeypatch):
    from backend.rbac import auth as auth_svc

    set_spy = AsyncMock()
    monkeypatch.setattr(auth_svc, "set_env_override", set_spy)

    result = json.loads(
        await save_mcp_env_var(
            connector_id="brave-search",
            key="BRAVE_API_KEY",
            value="sk-abc",
            ctx=_ctx(),
        )
    )

    assert result == {
        "status": "saved",
        "connector_id": "brave-search",
        "key": "BRAVE_API_KEY",
    }
    set_spy.assert_awaited_once_with("local", "BRAVE_API_KEY", "sk-abc")


@pytest.mark.asyncio
async def test_save_mcp_env_var_internal_error_returns_typed_error(monkeypatch):
    from backend.rbac import auth as auth_svc

    async def _boom(user_id, key, value):
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(auth_svc, "set_env_override", _boom)

    result = json.loads(
        await save_mcp_env_var(
            connector_id="brave-search",
            key="BRAVE_API_KEY",
            value="sk-abc",
            ctx=_ctx(),
        )
    )

    assert result["status"] == "error"
