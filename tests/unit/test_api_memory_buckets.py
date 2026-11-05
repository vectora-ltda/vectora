"""Memory Buckets (handler HTTP).

GET  /memory-buckets/catalog — lista buckets publicados
POST /memory-buckets/install — baixa e instala um bucket como coleção LanceDB
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api.handlers.memory_buckets import (
    InstallRequest,
    PublishRequest,
    get_catalog,
    post_install,
    post_publish,
)
from backend.services.memory_buckets import (
    MemoryBucketsError,
    MemoryBucketsUnavailableError,
)


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
async def test_get_catalog_returns_503_on_service_unavailable(monkeypatch):
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        memory_buckets,
        "_list_catalog",
        AsyncMock(side_effect=MemoryBucketsUnavailableError("catálogo indisponível")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_catalog()
    assert exc_info.value.status_code == 503


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

    result = await post_install(InstallRequest(bucket_id="b1"), _request())

    assert result == {"status": "installed", "collection": "shared_b1"}


@pytest.mark.asyncio
async def test_post_install_preserva_escopo_do_workspace_no_download(monkeypatch):
    """O escopo autenticado não pode ser descartado antes de instalar."""
    from backend.api.handlers import memory_buckets

    download = AsyncMock(return_value="shared_workspace_b1")
    monkeypatch.setattr(memory_buckets, "download_memory_bucket", download)
    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: object(),
    )

    result = await post_install(
        InstallRequest(bucket_id="b1", workspace_id="workspace-a"), _request()
    )

    assert result == {"status": "installed", "collection": "shared_workspace_b1"}
    download.assert_awaited_once_with("b1", workspace_id="workspace-a")


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

    result = await post_install(InstallRequest(bucket_id="b1"), _request())

    assert result["status"] == "error"
    assert "embedder" in result["error"]


def _publish_req() -> PublishRequest:
    return PublishRequest(
        bucket_id="b1",
        name="Docs internos",
        description="Documentação vetorizada",
        license="MIT",
        workspace_id="ws1",
    )


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/memory-buckets"})
    request.state.user = SimpleNamespace(id="local")
    return request


@pytest.mark.asyncio
async def test_post_publish_returns_bucket_id_on_success(monkeypatch):
    from backend.services import license

    monkeypatch.setattr(license, "_get_token", lambda: "tok-123")
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: object(),
    )
    monkeypatch.setattr(
        memory_buckets, "_authorize_bucket_workspace", lambda *args: None
    )

    monkeypatch.setattr(
        memory_buckets,
        "publish_memory_bucket",
        AsyncMock(return_value="b-new"),
    )

    result = await post_publish(_publish_req(), _request())

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

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: object(),
    )
    monkeypatch.setattr(
        memory_buckets, "_authorize_bucket_workspace", lambda *args: None
    )

    publish_mock = AsyncMock()
    monkeypatch.setattr(memory_buckets, "publish_memory_bucket", publish_mock)

    result = await post_publish(_publish_req(), _request())

    assert result["status"] == "error"
    assert "VECTORA_TOKEN" in result["error"]
    publish_mock.assert_not_called()


@pytest.mark.asyncio
async def test_post_publish_falha_do_service_vira_status_error(monkeypatch):
    from backend.services import license

    monkeypatch.setattr(license, "_get_token", lambda: "tok-123")
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: object(),
    )
    monkeypatch.setattr(
        memory_buckets, "_authorize_bucket_workspace", lambda *args: None
    )

    monkeypatch.setattr(
        memory_buckets,
        "publish_memory_bucket",
        AsyncMock(side_effect=MemoryBucketsError("workspace sem coleção local")),
    )

    result = await post_publish(_publish_req(), _request())

    assert result["status"] == "error"
    assert "coleção local" in result["error"]


@pytest.mark.asyncio
async def test_install_workspace_inexistente_e_bloqueado_antes_do_download(monkeypatch):
    """A scope inválida não pode disparar download nem escrever no disco."""
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: None,
    )
    download = AsyncMock()
    monkeypatch.setattr(memory_buckets, "download_memory_bucket", download)

    with pytest.raises(HTTPException) as exc_info:
        await post_install(
            InstallRequest(bucket_id="b1", workspace_id="missing"), _request()
        )

    assert exc_info.value.status_code == 404
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_workspace_sem_acesso_nao_envia_upload(monkeypatch):
    """Falha de ownership deve ocorrer antes de ler a coleção ou o token."""
    from backend.api.handlers import memory_buckets
    from backend.services import license

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="sem acesso")
        ),
    )
    monkeypatch.setattr(license, "_get_token", lambda: "should-not-be-read")
    publish = AsyncMock()
    monkeypatch.setattr(memory_buckets, "publish_memory_bucket", publish)

    payload = _publish_req().model_dump()
    payload["workspace_id"] = "foreign"
    request = _request()
    with pytest.raises(HTTPException) as exc_info:
        await post_publish(
            PublishRequest(**payload),
            request,
        )

    assert exc_info.value.status_code == 403
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_sem_workspace_rejeita_antes_de_ler_token(monkeypatch) -> None:
    """A publicação sem escopo não pode usar um bucket global."""
    from backend.api.handlers import memory_buckets
    from backend.services import license

    monkeypatch.setattr(license, "_get_token", lambda: "must-not-read")
    publish = AsyncMock()
    monkeypatch.setattr(memory_buckets, "publish_memory_bucket", publish)

    payload = _publish_req().model_dump()
    payload["workspace_id"] = None
    with pytest.raises(HTTPException) as exc_info:
        await post_publish(PublishRequest(**payload), _request())

    assert exc_info.value.status_code == 400
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_bucket_de_outro_workspace_rejeita_upload(monkeypatch) -> None:
    """O bucket precisa pertencer ao workspace autorizado, não apenas o token."""
    from backend.api.handlers import memory_buckets

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: object(),
    )
    monkeypatch.setattr(
        "backend.services.rag_buckets.get_bucket",
        lambda runtime_settings, bucket_id: SimpleNamespace(workspace_id="other"),
    )
    publish = AsyncMock()
    monkeypatch.setattr(memory_buckets, "publish_memory_bucket", publish)

    with pytest.raises(HTTPException) as exc_info:
        await post_publish(_publish_req(), _request())

    assert exc_info.value.status_code == 404
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_e_publish_sem_usuario_seguem_a_politica_de_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With auth disabled, a request without ``state.user`` reaches the service
    (local mode); with auth enabled, the same request is rejected with 401."""
    from backend.api.handlers import memory_buckets
    from backend.services import license

    monkeypatch.setattr(license, "_get_token", lambda: "tok-123")
    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        lambda workspace_id, request: object(),
    )
    monkeypatch.setattr(
        memory_buckets, "_authorize_bucket_workspace", lambda *args: None
    )
    download = AsyncMock(return_value="shared_b1")
    publish = AsyncMock(return_value="b-new")
    monkeypatch.setattr(memory_buckets, "download_memory_bucket", download)
    monkeypatch.setattr(memory_buckets, "publish_memory_bucket", publish)

    def _anonymous() -> Request:
        request = Request({"type": "http", "method": "POST", "path": "/memory-buckets"})
        request.state.user = None
        return request

    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    installed = await post_install(InstallRequest(bucket_id="b1"), _anonymous())
    assert installed == {"status": "installed", "collection": "shared_b1"}
    published = await post_publish(_publish_req(), _anonymous())
    assert published == {"status": "published", "bucket_id": "b-new"}
    assert publish.await_args is not None
    assert publish.await_args.kwargs["workspace_id"] == "ws1"

    download.reset_mock()
    publish.reset_mock()
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "true")
    for call in (
        post_install(InstallRequest(bucket_id="b1"), _anonymous()),
        post_publish(_publish_req(), _anonymous()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 401
    download.assert_not_awaited()
    publish.assert_not_awaited()
