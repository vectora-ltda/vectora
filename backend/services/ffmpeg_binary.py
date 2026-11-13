"""Resolve os binários ``ffmpeg``/``ffprobe`` usados por
``backend/tools/media_native.py`` — mesmo padrão de binário adjacente que
``backend/scheduling/nats_sidecar.py::_resolve_binary`` já usa pro
``nats-server``: bundle congelado (PyInstaller) → PATH do
sistema → árvore-fonte (``resources/``, baixado por
``scons ffmpeg``). Sem override explícito por env var (diferente do NATS)
porque nenhum empacotador externo — só o próprio build do Vectora —
precisa apontar pra um caminho não-padrão.

Nunca lança: qualquer chamador recebe ``None`` quando nenhum dos dois
binários está disponível em lugar nenhum, e degrada a feature (loga e
devolve erro tipado pro LLM, CLAUDE.md #11) em vez de derrubar o backend.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _exe_name(base: str) -> str:
    return f"{base}.exe" if sys.platform == "win32" else base


def _frozen_bundle_bases() -> list[Path]:
    """Mesma fonte de diretórios-raiz de bundle congelado que
    ``nats_sidecar.py::_frozen_bundle_bases`` usa — onde o build de
    produção pode ter colocado uma pasta ``ffmpeg/`` ao lado do
    executável."""
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    return bases


def _resources_dir() -> Path:
    """``<repo>/vectora/resources`` — onde `scons ffmpeg` baixa o binário
    da árvore-fonte (mesmo destino de `scons nats`). Função própria (não
    inline) só pra ficar mockável em teste sem depender de `__file__`."""
    return Path(__file__).resolve().parent.parent.parent / "resources"


def _resolve(base_name: str) -> str | None:
    """Localiza um binário (``ffmpeg`` ou ``ffprobe``).

    Ordem: bundle congelado (pasta ``ffmpeg/`` embutida pelo build) → PATH
    (dev, ex.: ``winget``/``choco``/``brew install ffmpeg``, ou distro com
    ffmpeg no repositório) → árvore-fonte (``resources/``, baixado
    por ``scons ffmpeg``)."""
    exe_name = _exe_name(base_name)

    for base in _frozen_bundle_bases():
        candidate = base / "ffmpeg" / exe_name
        if candidate.is_file():
            return str(candidate)

    from_path = shutil.which(base_name)
    if from_path:
        return from_path

    bundled = _resources_dir() / exe_name
    if bundled.is_file():
        return str(bundled)

    return None


def resolve_ffmpeg() -> str | None:
    path = _resolve("ffmpeg")
    if path is None:
        logger.warning(
            "ffmpeg_binary: ffmpeg não encontrado (bundle/PATH/vectora/resources) "
            "— rode `scons ffmpeg` ou instale ffmpeg no sistema"
        )
    return path


def resolve_ffprobe() -> str | None:
    path = _resolve("ffprobe")
    if path is None:
        logger.warning(
            "ffmpeg_binary: ffprobe não encontrado (bundle/PATH/vectora/resources) "
            "— rode `scons ffmpeg` ou instale ffmpeg no sistema"
        )
    return path
