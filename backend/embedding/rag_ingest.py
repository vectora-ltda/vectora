"""Ingest direto de uma pasta no RAG (sem passar pelo agente).

Compartilha o walk/chunk do ``ingest_docs`` (tool) mas enfileira diretamente
na embedding queue com um ``job_id``, permitindo barra de progresso por pasta.
Usado pelos endpoints REST ``/workspaces/{id}/rag/ingest``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.embedding.queue import get_embedding_queue
from backend.services.ignore import load_ignore_spec, walk_files
from backend.services.security import is_safe_file_path
from backend.services.text import text_service
from backend.settings import settings

logger = logging.getLogger(__name__)

#: Extensões consideradas "código" para o filtro de tipo da UI.
_CODE_EXTS = {
    "py",
    "ts",
    "tsx",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "go",
    "rs",
    "java",
    "c",
    "h",
    "cpp",
    "hpp",
    "cc",
    "cs",
    "rb",
    "php",
    "swift",
    "kt",
    "scala",
    "sh",
    "bash",
    "sql",
    "html",
    "css",
    "scss",
    "vue",
    "svelte",
    "toml",
    "yaml",
    "yml",
    "json",
}


def _split_ext_list(
    value: str | list[str] | None,
) -> list[str]:
    """Normaliza uma entrada de filtros (string CSV ou lista) para uma lista.

    Aceita extensões (com ou sem ponto), nomes de pasta e globs de caminho
    (ex. ``"node_modules"``, ``"**/*.min.js"``, ``"xml, tscn"``), separados
    por vírgula/ponto-e-vírgula quando string. Vazio/None → lista vazia.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
    else:
        parts = [str(p) for p in value]
    return [p for p in parts if p]


def _is_glob_pattern(entry: str) -> bool:
    """True se ``entry`` é um padrão de caminho/glob, não uma extensão simples.

    Extensão simples = sem separador de caminho e sem curinga
    (ex. ``"xml"``, ``".tscn"``). Tudo o que contém ``/``, ``\\\\`` ou
    ``*``/``?``/``[`` (ex. ``"node_modules"`` não, mas ``"**/vendor/**"``
    sim) é tratado como padrão de caminho relativo.
    """
    return any(ch in entry for ch in ("/", "\\", "*", "?", "["))


def _path_matches_any(relative: str, patterns: list[str]) -> bool:
    """Checa se ``relative`` (caminho POSIX relativo ao root) casa um padrão.

    Padrões de glob usam ``fnmatch``; nomes de pasta simples casam se a pasta
    (ou qualquer ancestral) bate, e também casam o próprio arquivo que vive
    dentro dela. Ex. padrão ``node_modules`` exclui ``node_modules/foo.js``.
    """
    import fnmatch

    for pat in patterns:
        if not _is_glob_pattern(pat):
            # Nome de pasta: casa se o caminho inteiro ou qualquer ancestral
            # termina nesse segmento de pasta.
            segments = relative.split("/")
            if any(seg == pat for seg in segments):
                return True
            continue
        norm = pat.replace("\\", "/")
        if fnmatch.fnmatch(relative, norm) or fnmatch.fnmatch(relative, f"{norm}/**"):
            return True
    return False


def _type_allowed_exts(file_types: str | list[str]) -> set[str] | None:
    """Conjunto de extensões permitidas pelo atalho ``file_types``.

    ``None`` significa \"todos\" (o atalho ``\"all\"`` ou lista vazia).
    ``file_types`` mantém os 3 atalhos por compatibilidade
    (``\"all\"``/``\"code\"``/``\"markdown\"``) ou uma lista de extensões
    customizadas (ex. ``[\"xml\"]``).
    """
    if isinstance(file_types, list):
        return None if not file_types else {t.lstrip(".").lower() for t in file_types}
    if file_types == "markdown":
        return {"md", "markdown", "mdx"}
    if file_types == "code":
        return _CODE_EXTS
    return None  # "all"


def _matches_file_type(
    path: Path,
    file_types: str | list[str] = "all",
    *,
    include_exts: str | list[str] | None = None,
    exclude_exts: str | list[str] | None = None,
) -> bool:
    """Decide se ``path`` entra na indexação.

    Os filtros ``include_exts``/``exclude_exts`` (string CSV ou lista)
    sobrepõem o atalho ``file_types``. Cada entrada pode ser uma **extensão**
    (``\"xml\"``/``\".tscn\"``), um **nome de pasta** (``\"node_modules\"``,
    exclui tudo dentro dela) ou um **glob de caminho** (``\"**/vendor/**\"``,
    ``\"**/*.min.js\"``) — mesmo estilo dos padrões de `files.exclude` do
    VS Code. ``exclude_exts`` remove sempre (precedência máxima). Sem filtros
    e com ``file_types=\"all\"`` → tudo (default).
    """
    # Caminho relativo (POSIX) a partir do root — path é sempre absoluto aqui
    # (walk_files devolve caminhos resolvidos dentro do diretório raiz).
    rel = str(path).replace("\\", "/")
    ext = path.suffix.lstrip(".").lower()

    exclude = _split_ext_list(exclude_exts)
    if exclude:
        if ext in {p.lstrip(".").lower() for p in exclude if not _is_glob_pattern(p)}:
            return False
        if _path_matches_any(rel, exclude):
            return False

    include = _split_ext_list(include_exts)
    if include:
        include_exts_only = [p for p in include if not _is_glob_pattern(p)]
        include_globs = [p for p in include if _is_glob_pattern(p)]

        # Extensão simples (ex. "xml"): o ext precisa estar no conjunto.
        if include_exts_only:
            if ext not in {p.lstrip(".").lower() for p in include_exts_only}:
                return False
        # Glob de caminho: se algum glob casa, inclui; se há globs mas
        # nenhum casa (e não há extensão simples casando), exclui.
        if include_globs and not _path_matches_any(rel, include_globs):
            return False

    allowed = _type_allowed_exts(file_types)
    return True if allowed is None else ext in allowed


def list_matching_files(
    directory_path: str,
    *,
    file_types: str | list[str] = "all",
    include_exts: str | list[str] | None = None,
    exclude_exts: str | list[str] | None = None,
    limit: int | None = None,
) -> tuple[list[str], int]:
    """Varre ``directory_path`` e devolve os arquivos que batem no filtro.

    Mesma validação e mesma lógica de match usadas por ``ingest_directory``
    (``is_safe_file_path`` + ``.gitignore``/``.vectoraignore`` via
    ``walk_files`` + ``_matches_file_type``). Paths devolvidos são relativos
    ao diretório, em POSIX.

    Returns:
        ``(paths_relativos[:limit], total_sem_corte)`` — ``total`` reflete a
        contagem real de arquivos que batem, independente de ``limit``.

    Raises:
        ValueError: caminho fora do escopo seguro ou não é diretório.
    """
    if not is_safe_file_path(directory_path):
        raise ValueError(f"Caminho fora do escopo permitido: {directory_path}")
    path = Path(directory_path).resolve()
    if not path.is_dir():
        raise ValueError(f"Não é um diretório: {directory_path}")

    spec = load_ignore_spec(path)
    all_files, _skipped = walk_files(path, "**/*", spec)
    # Glob de pasta/arquivo casa contra o caminho RELATIVO ao root (mesmo
    # contrato dos padrões de files.exclude do VS Code).
    files = [
        f
        for f in all_files
        if _matches_file_type(
            f.relative_to(path),
            file_types,
            include_exts=include_exts,
            exclude_exts=exclude_exts,
        )
    ]
    total = len(files)
    matched = files[:limit] if limit is not None else files
    relative = [str(f.relative_to(path)).replace("\\", "/") for f in matched]
    return relative, total


async def ingest_directory(
    directory_path: str,
    *,
    file_types: str | list[str] = "all",
    include_exts: str | list[str] | None = None,
    exclude_exts: str | list[str] | None = None,
    collection: str = "articles",
    workspace_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Varre uma pasta, faz chunk e enfileira no RAG agrupado por ``job_id``.

    Respeita ``.gitignore``/``.vectoraignore`` (via ``walk_files``) e filtra
    por tipo de arquivo — ver ``_matches_file_type`` (atalhos ``file_types``
    + filtros ``include_exts``/``exclude_exts`` em CSV). Não bloqueia: o
    worker de embedding processa a fila.

    Returns:
        ``{job_id, total_files, total_chunks, status}``.

    Raises:
        ValueError: caminho fora do escopo seguro ou não é diretório.
    """
    job = job_id or str(uuid4())
    relative_files, _total = list_matching_files(
        directory_path,
        file_types=file_types,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
    )
    path = Path(directory_path).resolve()
    files = [path / rel for rel in relative_files]

    if not files:
        return {
            "job_id": job,
            "total_files": 0,
            "total_chunks": 0,
            "status": "no_files",
        }

    queue = await get_embedding_queue(settings.embedding_queue_dsn)
    total_chunks = 0
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            logger.warning("rag_ingest: falha ao ler %s", file_path, exc_info=True)
            continue
        for chunk in text_service.split(text):
            metadata: dict[str, Any] = {
                "source": str(file_path),
                "source_dir": str(path),
                "ingested_at": datetime.now(UTC).isoformat(),
            }
            if workspace_id:
                metadata["workspace_id"] = workspace_id
            try:
                await queue.enqueue(chunk, collection, metadata, job_id=job)
                total_chunks += 1
            except Exception:
                logger.warning(
                    "rag_ingest: falha ao enfileirar chunk de %s",
                    file_path,
                    exc_info=True,
                )

    logger.info(
        "rag_ingest_enqueued",
        extra={"job_id": job, "files": len(files), "chunks": total_chunks},
    )
    return {
        "job_id": job,
        "total_files": len(files),
        "total_chunks": total_chunks,
        "status": "enqueued",
    }
