"""Memory Library — download/publish de buckets RAG pré-vetorizados
publicados pela comunidade (`services/src/rag-library/routes.ts`, estende
o mesmo catálogo já usado pelas bibliotecas de código first-party).

Download é sempre grátis (decisão de produto) — sem gate de tier/quota.
Publicação exige sessão de usuário da company (`session_token`) — este
módulo não resolve *como* obter esse token (login company↔desktop ainda
não existe no backend local); quem chama `publish_memory_bucket` já deve
ter um token de sessão válido em mãos.
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RAG_LIBRARY_URL = "https://services.vectora.company/rag-library"
HTTP_TIMEOUT = 30.0


class MemoryLibraryError(RuntimeError):
    """Erro tipado — embed_model incompatível, bucket não encontrado, ou
    falha de rede/publicação. Nunca mistura dimensões de vetor silenciosamente."""


def _rag_library_url() -> str:
    return os.getenv("VECTORA_RAG_LIBRARY_URL", DEFAULT_RAG_LIBRARY_URL).strip()


async def list_catalog(q: str | None = None) -> list[dict]:
    """Lista o catálogo da Memory Library, opcionalmente filtrado por `q`
    (repassado direto pro Worker — `services/src/rag-library/routes.ts`
    já faz `LIKE` sobre nome/descrição). Degrada para lista vazia em
    qualquer falha de rede — nunca propaga exceção pro handler HTTP (a
    seção da Library trata lista vazia como estado vazio, não erro)."""
    base_url = _rag_library_url().rstrip("/")
    params = {"q": q} if q else None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            # Versões antigas do Worker aceitavam apenas a barra final;
            # versões atuais aceitam a raiz sem ela. Tente ambas para que
            # uma atualização gradual nunca derrube o catálogo no desktop.
            for url in (f"{base_url}/", base_url):
                resp = await client.get(url, params=params)
                if resp.status_code == 404 and url.endswith("/"):
                    continue
                resp.raise_for_status()
                return resp.json()
            raise RuntimeError("Memory Library retornou 404 nas duas rotas")
    except Exception as exc:
        logger.warning("memory_library: falha ao consultar catálogo — %s", exc)
        return []


async def _fetch_bucket_metadata(bucket_id: str) -> dict:
    entries = await list_catalog()
    for entry in entries:
        if entry.get("id") == bucket_id:
            return entry
    raise MemoryLibraryError(f"Bucket '{bucket_id}' não encontrado na Memory Library.")


async def download_memory_bucket(
    bucket_id: str, *, lancedb_dir: Path | None = None
) -> str:
    """Baixa e instala o bucket `bucket_id` como uma coleção LanceDB nova
    (`shared_{bucket_id}`), isolada das coleções já existentes do usuário.

    Valida `embed_model` do bucket contra `settings.embedding_model` **antes**
    de tocar em qualquer arquivo — incompatível levanta `MemoryLibraryError`
    sem baixar nada. Retorna o nome da coleção instalada.
    """
    metadata = await _fetch_bucket_metadata(bucket_id)

    from backend.settings import settings

    bucket_embed_model = metadata.get("embed_model")
    if bucket_embed_model and bucket_embed_model != settings.embedding_model:
        raise MemoryLibraryError(
            f"Bucket '{bucket_id}' foi indexado com o embedder "
            f"'{bucket_embed_model}', mas o embedder atual é "
            f"'{settings.embedding_model}'. Buckets compartilhados exigem o "
            "mesmo embedder — misturar dimensões de vetor corromperia a busca."
        )

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(f"{_rag_library_url()}/{bucket_id}/download")
            resp.raise_for_status()
            archive_bytes = resp.content
    except Exception as exc:
        raise MemoryLibraryError(
            f"Falha ao baixar o bucket '{bucket_id}': {exc}"
        ) from exc

    collection = f"shared_{bucket_id}"
    # LanceDB grava cada tabela num diretório `{collection}.lance` — extrair
    # pra qualquer outro nome deixa `open_table(collection)` sem achar nada.
    target_dir = Path(lancedb_dir or settings.lancedb_dir or "") / f"{collection}.lance"
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            tar.extractall(target_dir, filter="data")
    except Exception as exc:
        raise MemoryLibraryError(
            f"Falha ao extrair o bucket '{bucket_id}' (arquivo corrompido?): {exc}"
        ) from exc

    logger.info("memory_library: bucket %s instalado como %s", bucket_id, collection)
    return collection


async def publish_memory_bucket(
    bucket_id: str,
    name: str,
    description: str,
    license: str,  # noqa: A002 — nome de campo do domínio (licença), não a builtin
    *,
    session_token: str,
    lancedb_dir: Path | None = None,
) -> str:
    """Empacota o bucket local `bucket_id` (uma tabela LanceDB isolada,
    `backend/services/rag_buckets.py`) num tar.gz e publica via
    `POST /rag-library/publish`. Retorna o `id` do bucket recém-publicado
    na Memory Library (sempre `verified=false` até curadoria manual) —
    distinto do `bucket_id` local que originou a publicação.
    """
    from backend.settings import settings

    collection = f"bucket_{bucket_id}"
    # LanceDB grava cada tabela num diretório `{collection}.lance`.
    source_dir = Path(lancedb_dir or settings.lancedb_dir or "") / f"{collection}.lance"
    if not source_dir.is_dir():
        raise MemoryLibraryError(
            f"Bucket '{bucket_id}' não tem tabela LanceDB local pra publicar."
        )

    # Empacota o CONTEÚDO do diretório .lance, não o diretório em si —
    # o nome do bucket muda ao ser instalado do outro lado
    # (`download_memory_bucket` usa `shared_{bucket_id}` remoto), então o
    # arquivo não pode carregar o nome local da coleção.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for item in source_dir.iterdir():
            tar.add(item, arcname=item.name)
    buffer.seek(0)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{_rag_library_url()}/publish",
                headers={"Authorization": f"Bearer {session_token}"},
                data={
                    "name": name,
                    "description": description,
                    "embed_model": settings.embedding_model,
                    "license": license,
                },
                files={"file": (f"{collection}.tar.gz", buffer, "application/gzip")},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise MemoryLibraryError(
            f"Falha ao publicar o bucket: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except Exception as exc:
        raise MemoryLibraryError(f"Falha ao publicar o bucket: {exc}") from exc

    remote_bucket_id = data.get("id")
    if not remote_bucket_id:
        raise MemoryLibraryError("Resposta inesperada do rag-library/publish (sem id).")
    logger.info(
        "memory_library: bucket %s publicado como %s", bucket_id, remote_bucket_id
    )
    return remote_bucket_id
