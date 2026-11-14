"""Armazenamento de planos de implementação por workspace.

Planos são salvos em dois lugares:
- ``~/.vectora/plans/<workspace_id>/`` — global, persiste entre clones e
  sobrevive mesmo se a pasta local ``.vectora/`` for apagada.
- ``<cwd>/.vectora/plans/`` — cópia local do workspace (se a pasta
  ``.vectora/`` existir).

``index.json`` (em ``~/.vectora/plans/<workspace_id>/``) guarda os metadados
de cada plano para listagem sem precisar abrir todos os arquivos.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PLANS_GLOBAL_ROOT = Path.home() / ".vectora" / "plans"


def _index_path(workspace_id: str) -> Path:
    return PLANS_GLOBAL_ROOT / workspace_id / "index.json"


def _load_index(workspace_id: str) -> dict:
    path = _index_path(workspace_id)
    if not path.is_file():
        return {"plans": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("plans: índice corrompido em %s", path, exc_info=True)
        return {"plans": []}
