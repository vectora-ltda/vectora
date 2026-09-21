"""Memory Buckets (handler HTTP).

GET  /rag-library/catalog — lista buckets publicados
POST /rag-library/install — baixa e instala um bucket como coleção LanceDB
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.api.handlers.memory_buckets import (
    InstallRequest,
    PublishRequest,
    get_catalog,
    post_install,
    post_publish,
)
from backend.services.memory_buckets import MemoryBucketsError


@pytest.mark.asyncio
async def test_get_catalog_returns_entries_from_service(monkeypatch):
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        memory_buckets,
        "_list_catalog",
        AsyncMock(return_value=[{"id": "b1", "name": "Bucket 1", "verified": True}]),
    )

    result = await get_catalog()

    assert result == [{"id": "b1", "name": "Bucket 1", "verified": True}]


@pytest.mark.asyncio
async def test_get_catalog_degrades_to_empty_list_on_service_failure(monkeypatch):
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(memory_buckets, "_list_catalog", AsyncMock(return_value=[]))

    result = await get_catalog()

    assert result == []


@pytest.mark.asyncio
async def test_get_catalog_repassa_q_pro_service(monkeypatch):
    """`?q=` chega no handler e é repassado pro service — não fica órfão."""
    from backend.api.handlers import memory_buckets

    list_catalog_mock = AsyncMock(return_value=[{"id": "b1"}])
    monkeypatch.setattr(memory_buckets, "_list_catalog", list_catalog_mock)

    result = await get_catalog(q="godot")

    list_catalog_mock.assert_awaited_once_with("godot")
    assert result == [{"id": "b1"}]


@pytest.mark.asyncio
async def test_post_install_returns_collection_on_success(monkeypatch):
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        memory_buckets,
        "download_memory_bucket",
        AsyncMock(return_value="shared_b1"),
    )

    result = await post_install(InstallRequest(bucket_id="b1"))

    assert result == {"status": "installed", "collection": "shared_b1"}


@pytest.mark.asyncio
async def test_post_install_incompatible_embed_model_returns_error_not_exception(
    monkeypatch,
):
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        memory_buckets,
        "download_memory_bucket",
        AsyncMock(side_effect=MemoryBucketsError("embedder incompatível")),
    )

    result = await post_install(InstallRequest(bucket_id="b1"))

    assert result["status"] == "error"
    assert "embedder" in result["error"]


def _publish_req() -> PublishRequest:
    return PublishRequest(
        bucket_id="b1",
        name="Docs internos",
        description="Documentação vetorizada",
        license="MIT",
    )


@pytest.mark.asyncio
async def test_post_publish_returns_bucket_id_on_success(monkeypatch):
    from backend.services import license

    monkeypatch.setattr(license, "_get_token", lambda: "tok-123")
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        memory_buckets,
        "publish_memory_bucket",
        AsyncMock(return_value="b-new"),
    )

    result = await post_publish(_publish_req())

    assert result == {"status": "published", "bucket_id": "b-new"}


@pytest.mark.asyncio
async def test_post_publish_sem_vectora_token_retorna_erro_sem_tentar_publicar(
    monkeypatch,
):
    """Erro/borda: sem VECTORA_TOKEN (nenhuma conta company conectada), o
    endpoint devolve erro claro e nem chama publish_memory_bucket."""
    from backend.services import license

    monkeypatch.setattr(license, "_get_token", lambda: None)
    from backend.api.handlers import memory_buckets

    publish_mock = AsyncMock()
    monkeypatch.setattr(memory_buckets, "publish_memory_bucket", publish_mock)

    result = await post_publish(_publish_req())

    assert result["status"] == "error"
    assert "VECTORA_TOKEN" in result["error"]
    publish_mock.assert_not_called()


@pytest.mark.asyncio
async def test_post_publish_falha_do_service_vira_status_error(monkeypatch):
    from backend.services import license

    monkeypatch.setattr(license, "_get_token", lambda: "tok-123")
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        memory_buckets,
        "publish_memory_bucket",
        AsyncMock(side_effect=MemoryBucketsError("workspace sem coleção local")),
    )

    result = await post_publish(_publish_req())

    assert result["status"] == "error"
    assert "coleção local" in result["error"]
