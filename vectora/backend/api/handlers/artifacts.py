"""Handler de artifacts — lista planos/specs/guias gravados pelo agente.

Reusa ``ArtifactMetadata`` (``src/types/documents.py``) e o layout em
disco produzido por ``create_artifact`` (``src/tools/fs.py``):

    ~/.vectora/artifacts/<session_id>/<slug>.md
"""

from __future__ import annotations

import logging
from datetime import UTC
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.settings import settings
from backend.vtypes.documents import DEFAULT_ARTIFACT_TYPE, ArtifactMetadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


class ListArtifactsResponse(BaseModel):
    artifacts: list[ArtifactMetadata]


class ArtifactContent(BaseModel):
    title: str
    slug: str
    session_id: str
    created_at: str
    content: str
    artifact_type: str = DEFAULT_ARTIFACT_TYPE


def _artifacts_dir(session_id: str) -> Path:
    """Pasta de artifacts da sessão. Sanitiza o session_id (sem traversal)."""
    safe = session_id.replace("/", "").replace("\\", "").replace("..", "")
    return settings.vectora_home / "artifacts" / safe


async def _require_session_owner(request: Request | None, session_id: str) -> None:
    """Require an authenticated owner before reading session artifacts."""
    # Direct callers used by local tooling/tests do not have an HTTP request;
    # routed requests always receive one from FastAPI and are enforced below.
    if request is None:
        return
    if not getattr(request.state, "user", None):
        raise HTTPException(status_code=401, detail="Não autenticado")
    from backend.api.handlers.threads import _assert_existing_thread_ownership

    await _assert_existing_thread_ownership(session_id, request)


def _read_preview(path: Path, limit: int = 200) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(limit + 1)[:limit]
    except OSError:
        return None


def _read_artifact_type(path: Path) -> str:
    """Lê o sidecar `{stem}.artifact_type` gravado por `create_artifact`
    (`tools/fs.py`). Artifacts legados (criados antes do campo existir) ou
    versões de histórico (`{slug}-N.md`) não têm sidecar — caem no default."""
    sidecar = path.with_suffix(".artifact_type")
    try:
        return sidecar.read_text(encoding="utf-8").strip() or DEFAULT_ARTIFACT_TYPE
    except OSError:
        return DEFAULT_ARTIFACT_TYPE


@router.get("/", response_model=ListArtifactsResponse)
async def list_artifacts(
    session_id: Annotated[str, Query()] = "",
    request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> ListArtifactsResponse:
    """Lista os artifacts da sessão, mais novos primeiro."""
    if not session_id:
        return ListArtifactsResponse(artifacts=[])
    await _require_session_owner(request, session_id)

    base = _artifacts_dir(session_id)
    if not base.exists() or not base.is_dir():
        return ListArtifactsResponse(artifacts=[])

    items: list[ArtifactMetadata] = []
    try:
        files = sorted(
            (p for p in base.glob("*.md") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        files = []

    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        title = path.stem.replace("-", " ").strip() or path.stem
        items.append(
            ArtifactMetadata(
                title=title,
                path=str(path),
                session_id=session_id,
                created_at=_format_mtime(stat.st_mtime),
                artifact_type=_read_artifact_type(path),
                content_preview=_read_preview(path),
            )
        )

    items.extend(_media_artifacts(base, session_id))
    items.sort(key=lambda a: a.created_at, reverse=True)
    return ListArtifactsResponse(artifacts=items)


def _media_artifacts(base: Path, session_id: str) -> list[ArtifactMetadata]:
    """Imagem/áudio gerados por `tools/media.py`.

    Ficam em `media/` como binário, então não entram no `glob("*.md")` da
    listagem principal — sem isto, o arquivo existe em disco mas não aparece
    em lugar nenhum da interface, que era o ponto de gerar mídia.
    """
    media_dir = base / "media"
    if not media_dir.is_dir():
        return []

    found: list[ArtifactMetadata] = []
    try:
        files = [p for p in media_dir.iterdir() if p.is_file()]
    except OSError:
        return []

    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append(
            ArtifactMetadata(
                title=path.stem.replace("-", " ").strip() or path.stem,
                path=str(path),
                session_id=session_id,
                created_at=_format_mtime(stat.st_mtime),
                artifact_type="media",
                # Binário não tem preview de texto — `None` em vez de tentar
                # decodificar bytes, que encheria a lista de lixo.
                content_preview=None,
            )
        )
    return found


@router.get("/{slug}", response_model=ArtifactContent)
async def get_artifact(
    slug: str,
    session_id: Annotated[str, Query()],
    request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> ArtifactContent:
    """Devolve o markdown completo de um artifact."""
    await _require_session_owner(request, session_id)
    base = _artifacts_dir(session_id)
    safe_slug = slug.replace("/", "").replace("\\", "").replace("..", "")
    path = base / f"{safe_slug}.md"

    if not path.exists() or not path.is_file():
        return ArtifactContent(
            title=safe_slug,
            slug=safe_slug,
            session_id=session_id,
            created_at="",
            content="",
        )

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        created = _format_mtime(path.stat().st_mtime)
    except OSError:
        content = ""
        created = ""

    return ArtifactContent(
        title=safe_slug.replace("-", " ").strip() or safe_slug,
        slug=safe_slug,
        session_id=session_id,
        created_at=created,
        content=content,
        artifact_type=_read_artifact_type(path),
    )


def _safe_path_segment(value: str) -> str:
    """Mesma sanitização anti-traversal já usada em `_artifacts_dir`/
    `get_artifact` pro slug — aqui aplicada a `session_id` e `filename`."""
    return value.replace("/", "").replace("\\", "").replace("..", "")


@router.get("/{session_id}/media/{filename}")
async def get_media_artifact(
    session_id: str,
    filename: str,
    request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> FileResponse:
    """Serve o binário de mídia gerada por `generate_image`/
    `text_to_speech`/`generate_video` (`tools/media.py::_persist`) — sem
    isso, as tools devolviam só um `path` de arquivo NO SERVIDOR, que o
    `<img src>`/link de download do chat não conseguem carregar. A URL
    servível (`tools/media.py::_media_url`) aponta exatamente pra cá."""
    await _require_session_owner(request, session_id)
    safe_session = _safe_path_segment(session_id)
    safe_filename = _safe_path_segment(filename)
    path = _artifacts_dir(safe_session) / "media" / safe_filename

    if not path.is_file():
        raise HTTPException(status_code=404, detail="mídia não encontrada")

    return FileResponse(path)


def _format_mtime(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=UTC).isoformat()
