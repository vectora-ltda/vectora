"""Memory Buckets — download/publish de buckets RAG compartilhados.

download_memory_bucket: valida embed_model ANTES de tocar em qualquer
arquivo (nunca mistura dimensões de vetor silenciosamente); publish_memory_bucket:
empacota a coleção local e publica via multipart.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile

import httpx
import pytest

from backend.services.memory_buckets import (
    DEFAULT_MEMORY_BUCKETS_URL,
    MemoryBucketsError,
    MemoryBucketsUnavailableError,
    _memory_buckets_url,
    download_memory_bucket,
    list_catalog,
    publish_memory_bucket,
)


@pytest.fixture(autouse=True)
def _bucket_owned_by_ws1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local buckets belong to ``ws1`` unless a test overrides the owner."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "backend.services.rag_buckets.get_bucket",
        lambda runtime_settings, bucket_id: SimpleNamespace(workspace_id="ws1"),
    )


@pytest.mark.parametrize("bucket_id", ["../escape", "nested/id", r"nested\\id", ""])
def test_bucket_id_rejects_path_traversal(bucket_id):
    """IDs externos nunca podem escapar da coleção isolada local."""
    with pytest.raises(MemoryBucketsError, match="Identificador"):
        # A validação ocorre antes de qualquer chamada de rede ou filesystem.
        asyncio.run(download_memory_bucket(bucket_id))


def test_memory_buckets_url_ignores_whitespace_only_override(monkeypatch):
    monkeypatch.setenv("VECTORA_MEMORY_BUCKETS_URL", "   ")
    monkeypatch.setenv("VECTORA_RAG_LIBRARY_URL", " https://legacy.example/buckets ")

    assert _memory_buckets_url() == "https://legacy.example/buckets"

    monkeypatch.setenv("VECTORA_RAG_LIBRARY_URL", "   ")
    assert _memory_buckets_url() == DEFAULT_MEMORY_BUCKETS_URL


def _catalog_response(entries: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json=entries, request=httpx.Request("GET", "https://x/memory-buckets/")
    )


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_download_incompatible_embed_model_raises_before_touching_files(
    tmp_path, monkeypatch
):
    async def _fake_get(self, url, **kwargs):
        assert "download" not in url  # nunca chega a baixar o arquivo
        return _catalog_response(
            [{"id": "b1", "embed_model": "voyage-3", "name": "Bucket 1"}]
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    with pytest.raises(MemoryBucketsError, match="embedder"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_bucket_not_found_raises_clear_error(monkeypatch, tmp_path):
    async def _fake_get(self, url, **kwargs):
        return _catalog_response([])

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    with pytest.raises(MemoryBucketsError, match="não encontrado"):
        await download_memory_bucket("missing", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_distingue_catalogo_indisponivel_de_catalogo_vazio(
    monkeypatch, tmp_path
):
    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    with pytest.raises(MemoryBucketsUnavailableError, match="indisponível"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_compatible_bucket_extracts_into_isolated_collection(
    tmp_path, monkeypatch
):
    archive = _make_tar_gz({"data.lance": b"fake lance content"})
    call_log: list[str] = []

    async def _fake_get(self, url, **kwargs):
        call_log.append(url)
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0", "name": "b1"}]
            )
        return httpx.Response(200, content=archive, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    # Erro/borda: coleção do usuário já existente com outro nome não pode
    # ser tocada — só a nova pasta isolada `shared_b1` é criada.
    (tmp_path / "meu_workspace_local").mkdir()

    collection = await download_memory_bucket("b1", lancedb_dir=tmp_path)

    assert collection == "shared_b1"
    assert (tmp_path / "shared_b1.lance" / "data.lance").is_file()
    assert (tmp_path / "meu_workspace_local").is_dir()
    assert list((tmp_path / "meu_workspace_local").iterdir()) == []


@pytest.mark.asyncio
async def test_download_workspace_scope_avoids_cross_workspace_collision(
    tmp_path, monkeypatch
):
    """O mesmo bucket público recebe coleções distintas por workspace."""
    archive = _make_tar_gz({"data.lance": b"workspace scoped"})

    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0"}]
            )
        return httpx.Response(200, content=archive, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    first = await download_memory_bucket(
        "b1", lancedb_dir=tmp_path, workspace_id="workspace-a"
    )
    second = await download_memory_bucket(
        "b1", lancedb_dir=tmp_path, workspace_id="workspace-b"
    )

    suffix_a = hashlib.sha256(b"workspace-a").hexdigest()[:16]
    suffix_b = hashlib.sha256(b"workspace-b").hexdigest()[:16]
    assert first == f"shared_{suffix_a}_b1"
    assert second == f"shared_{suffix_b}_b1"
    assert first != second
    assert (tmp_path / f"{first}.lance" / "data.lance").is_file()
    assert (tmp_path / f"{second}.lance" / "data.lance").is_file()


@pytest.mark.asyncio
async def test_download_rejects_symlink_archive_entry(tmp_path, monkeypatch):
    """Reject an archive containing a symlink before extraction."""
    archive_buf = io.BytesIO()
    with tarfile.open(fileobj=archive_buf, mode="w:gz") as tar:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        tar.addfile(link)
    archive = archive_buf.getvalue()

    async def _fake_get(self, url, **kwargs):
        """Return the prepared archive or catalog response for this test."""
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0"}]
            )
        return httpx.Response(200, content=archive, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    with pytest.raises(MemoryBucketsError, match="não suportada"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)
    assert not (tmp_path / "shared_b1.lance").exists()


@pytest.mark.asyncio
async def test_download_does_not_overwrite_existing_collection(tmp_path, monkeypatch):
    """Keep an existing collection intact when a download collides with it."""
    archive = _make_tar_gz({"new": b"new"})

    async def _fake_get(self, url, **kwargs):
        """Return the prepared archive or catalog response for this test."""
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0"}]
            )
        return httpx.Response(200, content=archive, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    existing = tmp_path / "shared_b1.lance"
    existing.mkdir()
    (existing / "keep").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(MemoryBucketsError, match="já existe"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)
    assert (existing / "keep").read_text(encoding="utf-8") == "do not overwrite"


@pytest.mark.asyncio
async def test_publish_rejects_symlink_inside_collection(tmp_path):
    """Reject publishing a collection that contains a symlink."""
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    link = bucket_dir / "link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks não disponíveis neste ambiente")
    with pytest.raises(MemoryBucketsError, match="link simbólico"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_publish_rejects_symlinked_collection_root(tmp_path):
    """A raiz da coleção não pode ser symlink: nada pode sair do diretório dela."""
    real_dir = tmp_path / "elsewhere"
    real_dir.mkdir()
    (real_dir / "data.lance").write_text("dados", encoding="utf-8")
    try:
        (tmp_path / "bucket_b1.lance").symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks não disponíveis neste ambiente")
    with pytest.raises(MemoryBucketsError, match="link simbólico"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_publish_missing_local_workspace_raises_clear_error(tmp_path):
    with pytest.raises(MemoryBucketsError, match="não tem tabela"):
        await publish_memory_bucket(
            "nonexistent-bucket",
            "Nome",
            "Descrição",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_publish_success_returns_bucket_id(tmp_path, monkeypatch):
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")

    captured: dict = {}

    async def _fake_post(self, url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        captured["data"] = kwargs.get("data")
        return httpx.Response(
            200,
            json={"ok": True, "id": "new-bucket-id", "verified": False},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    remote_bucket_id = await publish_memory_bucket(
        "b1",
        "Meu bucket",
        "descrição",
        "MIT",
        workspace_id="ws1",
        session_token="tok123",
        lancedb_dir=tmp_path,
    )

    assert remote_bucket_id == "new-bucket-id"
    assert captured["headers"]["Authorization"] == "Bearer tok123"
    assert captured["data"]["embed_model"] == "embed-multilingual-v3.0"


@pytest.mark.asyncio
async def test_publish_network_failure_raises_memory_buckets_error(
    tmp_path, monkeypatch
):
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")

    async def _fake_post(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    with pytest.raises(MemoryBucketsError, match="Falha ao publicar"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_list_catalog_falha_de_rede_preserva_indisponibilidade(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    with pytest.raises(MemoryBucketsUnavailableError, match="indisponível"):
        await list_catalog()


@pytest.mark.asyncio
async def test_list_catalog_consulta_a_rota_memory_buckets(monkeypatch):
    calls: list[str] = []

    async def _fake_get(self, url, **kwargs):
        calls.append(url)
        return _catalog_response([{"id": "legacy", "name": "Legado"}])

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    result = await list_catalog()
    assert calls == ["https://services.vectora.company/memory-buckets"]
    assert result == [{"id": "legacy", "name": "Legado"}]


@pytest.mark.asyncio
async def test_list_catalog_vazio_do_servidor_e_estado_valido(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return _catalog_response([])

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await list_catalog()

    assert result == []


@pytest.mark.asyncio
async def test_list_catalog_repassa_q_como_query_param(monkeypatch):
    """`q` chega como `?q=` na requisição real ao Worker — sem isso, o
    parâmetro fica órfão e a busca sempre devolve o catálogo inteiro."""
    captured: dict = {}

    async def _fake_get(self, url, **kwargs):
        captured["params"] = kwargs.get("params")
        return _catalog_response([])

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    await list_catalog(q="godot")

    assert captured["params"] == {"q": "godot"}


@pytest.mark.asyncio
async def test_list_catalog_sem_q_nao_manda_param_vazio(monkeypatch):
    captured: dict = {}

    async def _fake_get(self, url, **kwargs):
        captured["params"] = kwargs.get("params")
        return _catalog_response([])

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    await list_catalog()

    assert captured["params"] is None


@pytest.mark.asyncio
async def test_download_com_embed_model_ausente_no_bucket_nao_bloqueia(
    tmp_path, monkeypatch
):
    # Borda: bucket legado sem `embed_model` (coluna NULL, dado antigo
    # first-party) — `if bucket_embed_model and ...` é falsy, não bloqueia
    # por uma comparação com None.
    archive = _make_tar_gz({"data.lance": b"conteudo"})

    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response([{"id": "legacy", "name": "Legado"}])
        return httpx.Response(200, content=archive, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    collection = await download_memory_bucket("legacy", lancedb_dir=tmp_path)

    assert collection == "shared_legacy"


@pytest.mark.asyncio
async def test_download_catalogo_com_ids_duplicados_usa_a_primeira_ocorrencia(
    tmp_path, monkeypatch
):
    # Duplicado: dois entries com o mesmo id (dado inconsistente do
    # catálogo remoto) — _fetch_bucket_metadata não deve travar, usa o
    # primeiro que encontrar (comportamento determinístico, documentado
    # pelo teste em vez de implícito).
    async def _fake_get(self, url, **kwargs):
        return _catalog_response(
            [
                {"id": "dup", "embed_model": "modelo-a", "name": "primeiro"},
                {"id": "dup", "embed_model": "modelo-b", "name": "segundo"},
            ]
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr("backend.settings.settings.embedding_model", "modelo-b")

    with pytest.raises(MemoryBucketsError, match="modelo-a"):
        await download_memory_bucket("dup", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_arquivo_corrompido_levanta_erro_tipado_sem_deixar_lixo_parcial(
    tmp_path, monkeypatch
):
    # Payload malformado: bytes que não são um tar.gz válido (download
    # truncado/corrompido na rede) — MemoryBucketsError, nunca uma exceção
    # crua de tarfile propagando pro caller.
    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0"}]
            )
        return httpx.Response(
            200, content=b"nao-e-um-tar-gz-valido", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryBucketsError, match="corrompido"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_http_404_na_rota_de_download_levanta_erro_claro(
    tmp_path, monkeypatch
):
    # Bucket existe no catálogo mas o binário sumiu do storage (R2) —
    # raise_for_status() dispara HTTPStatusError, capturado genericamente.
    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0"}]
            )
        return httpx.Response(
            404, content=b"not found", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryBucketsError, match="Falha ao baixar"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_embed_model_case_sensitive_nao_normaliza(tmp_path, monkeypatch):
    # Erro/borda: comparação de embed_model é exata (==) — variação de
    # caixa não é tolerada silenciosamente (evita falso-positivo de
    # compatibilidade entre modelos com nomes parecidos).
    async def _fake_get(self, url, **kwargs):
        return _catalog_response(
            [{"id": "b1", "embed_model": "Embed-Multilingual-V3.0"}]
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    with pytest.raises(MemoryBucketsError, match="embedder"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_publish_resposta_sem_id_levanta_erro_claro(tmp_path, monkeypatch):
    # Payload malformado do servidor: 200 OK mas sem o campo `id` esperado
    # — não deve devolver None silenciosamente pro caller tratar como sucesso.
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")

    async def _fake_post(self, url, **kwargs):
        return httpx.Response(
            200, json={"ok": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    with pytest.raises(MemoryBucketsError, match="sem id"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_publish_workspace_com_pasta_vazia_ainda_empacota_sem_erro(
    tmp_path, monkeypatch
):
    # Borda: diretório existe mas está vazio (coleção LanceDB sem dados
    # ainda) — is_dir() passa, tarfile.add funciona com pasta vazia.
    bucket_dir = tmp_path / "bucket_b-vazio.lance"
    bucket_dir.mkdir()

    async def _fake_post(self, url, **kwargs):
        return httpx.Response(
            200, json={"id": "bucket-vazio"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    remote_bucket_id = await publish_memory_bucket(
        "b-vazio",
        "n",
        "d",
        "MIT",
        workspace_id="ws1",
        session_token="tok",
        lancedb_dir=tmp_path,
    )

    assert remote_bucket_id == "bucket-vazio"


@pytest.mark.asyncio
async def test_publish_http_401_por_session_token_invalido_levanta_erro_claro(
    tmp_path, monkeypatch
):
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")

    async def _fake_post(self, url, **kwargs):
        return httpx.Response(
            401, content=b"invalid token", request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    with pytest.raises(MemoryBucketsError, match="401"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok-invalido",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_download_truncated_archive_raises_clear_error_not_generic_exception(
    tmp_path, monkeypatch
):
    # Erro/borda: resposta HTTP incompleta (tar.gz truncado) — extractall
    # levanta exceção de tarfile, deve virar MemoryBucketsError tipado.
    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0", "name": "b1"}]
            )
        # tar.gz cortado no meio — bytes inválidos como gzip/tar.
        return httpx.Response(
            200, content=b"\x1f\x8b\x08\x00truncated", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryBucketsError, match="corrompido"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_empty_response_body_raises_clear_error(tmp_path, monkeypatch):
    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0", "name": "b1"}]
            )
        return httpx.Response(200, content=b"", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryBucketsError, match="corrompido"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_http_error_status_raises_memory_buckets_error(
    tmp_path, monkeypatch
):
    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0", "name": "b1"}]
            )
        return httpx.Response(
            404, json={"error": "not found"}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryBucketsError, match="Falha ao baixar"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_bucket_without_embed_model_skips_compatibility_check(
    tmp_path, monkeypatch
):
    # Borda: bucket sem embed_model declarado (metadado antigo/ausente) não
    # deve bloquear o download — só bloqueia quando há valor conflitante.
    archive = _make_tar_gz({"data.lance": b"conteudo"})

    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response([{"id": "b1", "name": "b1"}])
        return httpx.Response(200, content=archive, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    collection = await download_memory_bucket("b1", lancedb_dir=tmp_path)

    assert collection == "shared_b1"


@pytest.mark.asyncio
async def test_download_network_error_during_fetch_raises_memory_buckets_error(
    tmp_path, monkeypatch
):
    async def _fake_get(self, url, **kwargs):
        if url.rstrip("/").endswith("/memory-buckets"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0", "name": "b1"}]
            )
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryBucketsError, match="Falha ao baixar"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_list_catalog_network_error_returns_unavailable_error(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    from backend.services.memory_buckets import list_catalog

    with pytest.raises(MemoryBucketsUnavailableError, match="indisponível"):
        await list_catalog()


@pytest.mark.asyncio
async def test_publish_http_error_status_includes_status_code_in_message(
    tmp_path, monkeypatch
):
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")

    async def _fake_post(self, url, **kwargs):
        return httpx.Response(
            403, json={"error": "forbidden"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    with pytest.raises(MemoryBucketsError, match="403"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_publish_response_without_id_raises_clear_error(tmp_path, monkeypatch):
    # Erro/borda: resposta 200 mas sem "id" — payload inesperado do backend,
    # não deve devolver bucket_id vazio/None silenciosamente.
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")

    async def _fake_post(self, url, **kwargs):
        return httpx.Response(
            200, json={"ok": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    with pytest.raises(MemoryBucketsError, match="sem id"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_publish_empty_workspace_dir_still_packages_empty_archive(
    tmp_path, monkeypatch
):
    # Borda: bucket existe mas está vazio (sem tabelas .lance ainda) —
    # empacota um tar.gz vazio em vez de falhar.
    bucket_dir = tmp_path / "bucket_b-empty.lance"
    bucket_dir.mkdir()

    async def _fake_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"id": "empty-bucket-id"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )

    remote_bucket_id = await publish_memory_bucket(
        "b-empty",
        "n",
        "d",
        "MIT",
        workspace_id="ws1",
        session_token="tok",
        lancedb_dir=tmp_path,
    )

    assert remote_bucket_id == "empty-bucket-id"


@pytest.mark.asyncio
async def test_publish_exige_bucket_do_workspace_antes_de_empacotar(
    tmp_path, monkeypatch
) -> None:
    """The service itself enforces ownership: a bucket from another workspace,
    an unknown bucket or a missing workspace never reaches the upload."""
    from types import SimpleNamespace

    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")
    posts: list[str] = []

    async def _fake_post(self, url, **kwargs):
        posts.append(url)
        return httpx.Response(
            200, json={"ok": True, "id": "remote"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    owners = {"b1": SimpleNamespace(workspace_id="other")}
    monkeypatch.setattr(
        "backend.services.rag_buckets.get_bucket",
        lambda runtime_settings, bucket_id: owners.get(bucket_id),
    )

    for workspace_id in ("ws1", ""):
        with pytest.raises(MemoryBucketsError, match="workspace"):
            await publish_memory_bucket(
                "b1",
                "n",
                "d",
                "MIT",
                workspace_id=workspace_id,
                session_token="tok",
                lancedb_dir=tmp_path,
            )
    owners.clear()
    with pytest.raises(MemoryBucketsError, match="workspace"):
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )
    assert posts == []

    owners["b1"] = SimpleNamespace(workspace_id="ws1")
    assert (
        await publish_memory_bucket(
            "b1",
            "n",
            "d",
            "MIT",
            workspace_id="ws1",
            session_token="tok",
            lancedb_dir=tmp_path,
        )
        == "remote"
    )
    assert len(posts) == 1
