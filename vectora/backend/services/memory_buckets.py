"""Memory Buckets — download/publish de buckets RAG pré-vetorizados
publicados pela comunidade (`services/src/memory-buckets/routes.ts`, estende
o mesmo catálogo já usado pelas bibliotecas de código first-party).

Download é sempre grátis (decisão de produto) — sem gate de tier/quota.
Publicação exige sessão de usuário da company (`session_token`) — este
módulo não resolve *como* obter esse token (login company↔desktop ainda
não existe no backend local); quem chama `publish_memory_bucket` já deve
ter um token de sessão válido em mãos.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import tarfile
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_BUCKETS_URL: str = "https://services.vectora.company/memory-buckets"
HTTP_TIMEOUT = 30.0


class MemoryBucketsError(RuntimeError):
    """Erro tipado — embed_model incompatível, bucket não encontrado, ou
    falha de rede/publicação. Nunca mistura dimensões de vetor silenciosamente."""


class MemoryBucketsUnavailableError(MemoryBucketsError):
    """O catálogo não pôde ser consultado; não significa catálogo vazio."""


def _validate_bucket_id(bucket_id: str) -> str:
    """Valida o identificador antes de usá-lo em URL ou caminho local."""
    value = bucket_id.strip()
    if (
        not value
        or value in {".", ".."}
        or any(part in {".", ".."} for part in Path(value).parts)
        or any(ch in value for ch in ("/", "\\", "\x00"))
    ):
        raise MemoryBucketsError("Identificador de bucket inválido")
    return value


def _safe_extract(archive: tarfile.TarFile, target_dir: Path) -> None:
    """Extrai somente arquivos dentro do alvo, rejeitando links e traversal."""
    root = target_dir.resolve()
    for member in archive.getmembers():
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise MemoryBucketsError("Bucket contém entrada de arquivo não suportada")
        destination = (root / member.name).resolve()
        if destination != root and root not in destination.parents:
            raise MemoryBucketsError("Bucket contém caminho fora do diretório alvo")
    archive.extractall(root, filter="data")


def _memory_buckets_url() -> str:
    candidates = (
        os.getenv("VECTORA_MEMORY_BUCKETS_URL"),
        os.getenv("VECTORA_RAG_LIBRARY_URL"),
        DEFAULT_MEMORY_BUCKETS_URL,
    )
    return next(value.strip() for value in candidates if value and value.strip())


async def _request_catalog(q: str | None = None) -> list[dict]:
    """Consulta o catálogo preservando vazio e indisponibilidade."""
    base_url = _memory_buckets_url().rstrip("/")
    params = {"q": q} if q else None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(base_url, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        raise MemoryBucketsUnavailableError(
            "Catálogo de Memory Buckets indisponível; tente novamente mais tarde."
        ) from exc
    if not isinstance(payload, list) or any(
        not isinstance(entry, dict) for entry in payload
    ):
        raise MemoryBucketsUnavailableError("Catálogo de Memory Buckets inválido.")
    return payload


async def list_catalog(q: str | None = None) -> list[dict]:
    """Lista o catálogo, preservando a diferença entre vazio e indisponível.

    Uma resposta ``[]`` significa que o serviço respondeu corretamente e não
    há buckets publicados. Falhas de rede, HTTP ou payload continuam sendo
    ``MemoryBucketsUnavailableError`` para que a API possa retornar ``503`` em
    vez de apresentar indisponibilidade como catálogo vazio.
    """
    return await _request_catalog(q)


async def _fetch_bucket_metadata(bucket_id: str) -> dict:
    """Load metadata for a validated bucket or raise a stable domain error."""
    bucket_id = _validate_bucket_id(bucket_id)
    entries = await _request_catalog()
    for entry in entries:
        if entry.get("id") == bucket_id:
            return entry
    raise MemoryBucketsError(f"Bucket '{bucket_id}' não encontrado na Memory Buckets.")


async def download_memory_bucket(
    bucket_id: str,
    *,
    lancedb_dir: Path | None = None,
    workspace_id: str | None = None,
) -> str:
    """Baixa e instala o bucket `bucket_id` como uma coleção LanceDB nova
    (`shared_{bucket_id}` para chamadas sem escopo, ou um nome derivado do workspace), isolada das coleções já existentes do usuário.

    Valida `embed_model` do bucket contra `settings.embedding_model` **antes**
    de tocar em qualquer arquivo — incompatível levanta `MemoryBucketsError`
    sem baixar nada. Retorna o nome da coleção instalada.
    """
    bucket_id = _validate_bucket_id(bucket_id)
    metadata = await _fetch_bucket_metadata(bucket_id)

    from backend.settings import settings

    bucket_embed_model = metadata.get("embed_model")
    if bucket_embed_model and bucket_embed_model != settings.embedding_model:
        raise MemoryBucketsError(
            f"Bucket '{bucket_id}' foi indexado com o embedder "
            f"'{bucket_embed_model}', mas o embedder atual é "
            f"'{settings.embedding_model}'. Buckets compartilhados exigem o "
            "mesmo embedder — misturar dimensões de vetor corromperia a busca."
        )

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(f"{_memory_buckets_url()}/{bucket_id}/download")
            resp.raise_for_status()
            archive_bytes = resp.content
    except Exception as exc:
        raise MemoryBucketsError(
            f"Falha ao baixar o bucket '{bucket_id}': {exc}"
        ) from exc

    # Keep legacy names for unscoped callers, but isolate authenticated
    # workspace installs even when they use the same public bucket id.
    if workspace_id is not None:
        normalized_workspace = workspace_id.strip()
        if not normalized_workspace:
            raise MemoryBucketsError("workspace_id não pode ser vazio.")
        workspace_suffix = hashlib.sha256(
            normalized_workspace.encode("utf-8")
        ).hexdigest()[:16]
        collection = f"shared_{workspace_suffix}_{bucket_id}"
    else:
        collection = f"shared_{bucket_id}"
    # LanceDB grava cada tabela num diretório `{collection}.lance` — extrair
    # pra qualquer outro nome deixa `open_table(collection)` sem achar nada.
    target_dir = Path(lancedb_dir or settings.lancedb_dir or "") / f"{collection}.lance"
    if target_dir.exists():
        raise MemoryBucketsError(
            f"A coleção local '{collection}' já existe; recuse sobrescrever o bucket."
        )
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Extraia primeiro em um diretório temporário no mesmo filesystem. Se
        # a validação ou a descompressão falhar, nenhum diretório parcial fica
        # visível para o LanceDB nem para uma recuperação posterior.
        with tempfile.TemporaryDirectory(
            prefix=f".{collection}.", dir=target_dir.parent
        ) as staging:
            staging_dir = Path(staging)
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
                _safe_extract(tar, staging_dir)
            staging_dir.replace(target_dir)
    except Exception as exc:
        raise MemoryBucketsError(
            f"Falha ao extrair o bucket '{bucket_id}' (arquivo corrompido?): {exc}"
        ) from exc

    logger.info("memory_buckets: bucket %s instalado como %s", bucket_id, collection)
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
    `POST /memory-buckets/publish`. Retorna o `id` do bucket recém-publicado
    na Memory Buckets (sempre `verified=false` até curadoria manual) —
    distinto do `bucket_id` local que originou a publicação.
    """
    bucket_id = _validate_bucket_id(bucket_id)
    from backend.settings import settings

    collection = f"bucket_{bucket_id}"
    # LanceDB grava cada tabela num diretório `{collection}.lance`.
    source_dir = Path(lancedb_dir or settings.lancedb_dir or "") / f"{collection}.lance"
    if not source_dir.is_dir():
        raise MemoryBucketsError(
            f"Bucket '{bucket_id}' não tem tabela LanceDB local pra publicar."
        )

    # Empacota o CONTEÚDO do diretório .lance, não o diretório em si —
    # o nome do bucket muda ao ser instalado do outro lado
    # (`download_memory_bucket` usa `shared_{bucket_id}` remoto), então o
    # arquivo não pode carregar o nome local da coleção.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        root = source_dir.resolve()
        for item in source_dir.iterdir():
            resolved = item.resolve()
            if item.is_symlink() or not (resolved == root or root in resolved.parents):
                raise MemoryBucketsError(
                    "Bucket contém link simbólico ou caminho fora da coleção"
                )
            tar.add(item, arcname=item.name, recursive=True)
    buffer.seek(0)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{_memory_buckets_url()}/publish",
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
        raise MemoryBucketsError(
            f"Falha ao publicar o bucket: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except Exception as exc:
        raise MemoryBucketsError(f"Falha ao publicar o bucket: {exc}") from exc

    remote_bucket_id = data.get("id")
    if not remote_bucket_id:
        raise MemoryBucketsError(
            "Resposta inesperada do memory-buckets/publish (sem id)."
        )
    logger.info(
        "memory_buckets: bucket %s publicado como %s", bucket_id, remote_bucket_id
    )
    return remote_bucket_id
