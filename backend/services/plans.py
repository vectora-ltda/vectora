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

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PLANS_GLOBAL_ROOT = Path.home() / ".vectora" / "plans"


def _plan_id(title: str) -> str:
    digest = hashlib.sha256(f"{title}:{datetime.now(UTC).isoformat()}".encode())
    return digest.hexdigest()[:12]


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


def save_plan(
    workspace_id: str,
    cwd: str | Path,
    title: str,
    content: str,
    *,
    status: str = "draft",
) -> str:
    """Salva um plano globalmente e, se existir, na cópia local do workspace.

    Retorna o ``plan_id`` gerado.
    """
    plan_id = _plan_id(title)
    filename = f"{plan_id}.md"
    now = datetime.now(UTC).isoformat()

    global_dir = PLANS_GLOBAL_ROOT / workspace_id
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / filename).write_text(content, encoding="utf-8")

    index = _load_index(workspace_id)
    index["plans"].append(
        {
            "id": plan_id,
            "title": title,
            "created_at": now,
            "status": status,
            "file": filename,
        }
    )
    _index_path(workspace_id).write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    local_plans_dir = Path(cwd) / ".vectora" / "plans"
    if local_plans_dir.is_dir():
        (local_plans_dir / filename).write_text(content, encoding="utf-8")

    return plan_id


def list_plans(workspace_id: str) -> list[dict]:
    """Lista os planos salvos globalmente para um workspace."""
    return _load_index(workspace_id).get("plans", [])
