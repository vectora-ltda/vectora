"""Memory Library — download/publish de buckets RAG compartilhados.

download_memory_bucket: valida embed_model ANTES de tocar em qualquer
arquivo (nunca mistura dimensões de vetor silenciosamente); publish_memory_bucket:
empacota a coleção local e publica via multipart.
"""

from __future__ import annotations

import io
import tarfile

import httpx
import pytest

from backend.services.memory_library import (
    MemoryLibraryError,
    download_memory_bucket,
    list_catalog,
    publish_memory_bucket,
)


def _catalog_response(entries: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json=entries, request=httpx.Request("GET", "https://x/rag-library/")
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

    with pytest.raises(MemoryLibraryError, match="embedder"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_bucket_not_found_raises_clear_error(monkeypatch, tmp_path):
    async def _fake_get(self, url, **kwargs):
        return _catalog_response([])

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    with pytest.raises(MemoryLibraryError, match="não encontrado"):
        await download_memory_bucket("missing", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_compatible_bucket_extracts_into_isolated_collection(
    tmp_path, monkeypatch
):
    archive = _make_tar_gz({"data.lance": b"fake lance content"})
    call_log: list[str] = []

    async def _fake_get(self, url, **kwargs):
        call_log.append(url)
        if url.endswith("/rag-library/"):
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
async def test_publish_missing_local_workspace_raises_clear_error(tmp_path):
    with pytest.raises(MemoryLibraryError, match="não tem tabela"):
        await publish_memory_bucket(
            "nonexistent-bucket",
            "Nome",
            "Descrição",
            "MIT",
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
        session_token="tok123",
        lancedb_dir=tmp_path,
    )

    assert remote_bucket_id == "new-bucket-id"
    assert captured["headers"]["Authorization"] == "Bearer tok123"
    assert captured["data"]["embed_model"] == "embed-multilingual-v3.0"


@pytest.mark.asyncio
async def test_publish_network_failure_raises_memory_library_error(
    tmp_path, monkeypatch
):
    bucket_dir = tmp_path / "bucket_b1.lance"
    bucket_dir.mkdir()
    (bucket_dir / "table.lance").write_bytes(b"data")

    async def _fake_post(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    with pytest.raises(MemoryLibraryError, match="Falha ao publicar"):
        await publish_memory_bucket(
            "b1", "n", "d", "MIT", session_token="tok", lancedb_dir=tmp_path
        )


@pytest.mark.asyncio
async def test_list_catalog_falha_de_rede_devolve_lista_vazia_sem_propagar(
    monkeypatch,
):
    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await list_catalog()

    assert result == []


@pytest.mark.asyncio
async def test_list_catalog_faz_fallback_da_rota_com_barra(monkeypatch):
    calls: list[str] = []

    async def _fake_get(self, url, **kwargs):
        calls.append(url)
        if url.endswith("/"):
            return httpx.Response(404, request=httpx.Request("GET", url))
        return _catalog_response([{"id": "legacy", "name": "Legado"}])

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    result = await list_catalog()
    assert calls == [
        "https://services.vectora.company/rag-library/",
        "https://services.vectora.company/rag-library",
    ]
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
        if url.endswith("/rag-library/"):
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

    with pytest.raises(MemoryLibraryError, match="modelo-a"):
        await download_memory_bucket("dup", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_arquivo_corrompido_levanta_erro_tipado_sem_deixar_lixo_parcial(
    tmp_path, monkeypatch
):
    # Payload malformado: bytes que não são um tar.gz válido (download
    # truncado/corrompido na rede) — MemoryLibraryError, nunca uma exceção
    # crua de tarfile propagando pro caller.
    async def _fake_get(self, url, **kwargs):
        if url.endswith("/rag-library/"):
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

    with pytest.raises(MemoryLibraryError, match="corrompido"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_http_404_na_rota_de_download_levanta_erro_claro(
    tmp_path, monkeypatch
):
    # Bucket existe no catálogo mas o binário sumiu do storage (R2) —
    # raise_for_status() dispara HTTPStatusError, capturado genericamente.
    async def _fake_get(self, url, **kwargs):
        if url.endswith("/rag-library/"):
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

    with pytest.raises(MemoryLibraryError, match="Falha ao baixar"):
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

    with pytest.raises(MemoryLibraryError, match="embedder"):
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

    with pytest.raises(MemoryLibraryError, match="sem id"):
        await publish_memory_bucket(
            "b1", "n", "d", "MIT", session_token="tok", lancedb_dir=tmp_path
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
        "b-vazio", "n", "d", "MIT", session_token="tok", lancedb_dir=tmp_path
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

    with pytest.raises(MemoryLibraryError, match="401"):
        await publish_memory_bucket(
            "b1", "n", "d", "MIT", session_token="tok-invalido", lancedb_dir=tmp_path
        )


@pytest.mark.asyncio
async def test_download_truncated_archive_raises_clear_error_not_generic_exception(
    tmp_path, monkeypatch
):
    # Erro/borda: resposta HTTP incompleta (tar.gz truncado) — extractall
    # levanta exceção de tarfile, deve virar MemoryLibraryError tipado.
    async def _fake_get(self, url, **kwargs):
        if url.endswith("/rag-library/"):
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

    with pytest.raises(MemoryLibraryError, match="corrompido"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_empty_response_body_raises_clear_error(tmp_path, monkeypatch):
    async def _fake_get(self, url, **kwargs):
        if url.endswith("/rag-library/"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0", "name": "b1"}]
            )
        return httpx.Response(200, content=b"", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryLibraryError, match="corrompido"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_http_error_status_raises_memory_library_error(
    tmp_path, monkeypatch
):
    async def _fake_get(self, url, **kwargs):
        if url.endswith("/rag-library/"):
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

    with pytest.raises(MemoryLibraryError, match="Falha ao baixar"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_download_bucket_without_embed_model_skips_compatibility_check(
    tmp_path, monkeypatch
):
    # Borda: bucket sem embed_model declarado (metadado antigo/ausente) não
    # deve bloquear o download — só bloqueia quando há valor conflitante.
    archive = _make_tar_gz({"data.lance": b"conteudo"})

    async def _fake_get(self, url, **kwargs):
        if url.endswith("/rag-library/"):
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
async def test_download_network_error_during_fetch_raises_memory_library_error(
    tmp_path, monkeypatch
):
    async def _fake_get(self, url, **kwargs):
        if url.endswith("/rag-library/"):
            return _catalog_response(
                [{"id": "b1", "embed_model": "embed-multilingual-v3.0", "name": "b1"}]
            )
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(
        "backend.settings.settings.embedding_model", "embed-multilingual-v3.0"
    )
    monkeypatch.setattr("backend.settings.settings.lancedb_dir", None)

    with pytest.raises(MemoryLibraryError, match="Falha ao baixar"):
        await download_memory_bucket("b1", lancedb_dir=tmp_path)


@pytest.mark.asyncio
async def test_list_catalog_network_error_returns_empty_list_not_exception(
    monkeypatch,
):
    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    from backend.services.memory_library import list_catalog

    entries = await list_catalog()

    assert entries == []


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

    with pytest.raises(MemoryLibraryError, match="403"):
        await publish_memory_bucket(
            "b1", "n", "d", "MIT", session_token="tok", lancedb_dir=tmp_path
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

    with pytest.raises(MemoryLibraryError, match="sem id"):
        await publish_memory_bucket(
            "b1", "n", "d", "MIT", session_token="tok", lancedb_dir=tmp_path
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
        "b-empty", "n", "d", "MIT", session_token="tok", lancedb_dir=tmp_path
    )

    assert remote_bucket_id == "empty-bucket-id"
