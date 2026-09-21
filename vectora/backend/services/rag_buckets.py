"""Catálogo de buckets de RAG — cada pasta indexada vira um bucket próprio
(uma tabela LanceDB isolada, `collection=f"bucket_{bucket_id}"`), com nome,
descrição e um conjunto de buckets ativos por workspace.

Persistido via `RuntimeSettings` (mesmo SQLite `app_settings` já usado por
outras preferências não-secretas) sob duas chaves: `rag_buckets` (dict
`bucket_id -> registro`) e `rag_workspace_active_buckets` (dict
`workspace_id -> list[bucket_id]`) — mesmo padrão de blob JSON já usado por
`storage_services`/`frontend_prefs` no mesmo módulo, sem introduzir schema
SQL novo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from backend.workspace.runtime_settings import RuntimeSettings

_BUCKETS_KEY = "rag_buckets"
_ACTIVE_KEY = "rag_workspace_active_buckets"


@dataclass(frozen=True)
class RagBucket:
    id: str
    workspace_id: str
    name: str
    description_md: str
    source_path: str | None
    created_at: str


def _load_buckets(rs: RuntimeSettings) -> dict[str, dict]:
    raw = rs.get(_BUCKETS_KEY, {})
    return cast("dict[str, dict]", raw) if isinstance(raw, dict) else {}


def _load_active(rs: RuntimeSettings) -> dict[str, list[str]]:
    raw = rs.get(_ACTIVE_KEY, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): [str(v) for v in vs] if isinstance(vs, list) else []
        for k, vs in raw.items()
    }


def _to_bucket(bucket_id: str, record: dict) -> RagBucket:
    return RagBucket(
        id=bucket_id,
        workspace_id=str(record.get("workspace_id", "")),
        name=str(record.get("name", "")),
        description_md=str(record.get("description_md", "")),
        source_path=record.get("source_path"),
        created_at=str(record.get("created_at", "")),
    )


def create_bucket(
    rs: RuntimeSettings,
    *,
    workspace_id: str,
    name: str,
    source_path: str | None = None,
    description_md: str = "",
) -> RagBucket:
    """Cria um bucket novo (nunca reaproveita `bucket_id`) e o retorna."""
    bucket_id = uuid.uuid4().hex
    record = {
        "workspace_id": workspace_id,
        "name": name,
        "description_md": description_md,
        "source_path": source_path,
        "created_at": datetime.now(UTC).isoformat(),
    }
    buckets = _load_buckets(rs)
    buckets[bucket_id] = record
    rs.set(_BUCKETS_KEY, buckets)
    return _to_bucket(bucket_id, record)


def list_buckets(rs: RuntimeSettings, workspace_id: str) -> list[RagBucket]:
    """Buckets do workspace, mais antigos primeiro."""
    buckets = _load_buckets(rs)
    matches = [
        _to_bucket(bucket_id, record)
        for bucket_id, record in buckets.items()
        if record.get("workspace_id") == workspace_id
    ]
    return sorted(matches, key=lambda b: b.created_at)


def get_bucket(rs: RuntimeSettings, bucket_id: str) -> RagBucket | None:
    buckets = _load_buckets(rs)
    record = buckets.get(bucket_id)
    return _to_bucket(bucket_id, record) if record is not None else None


def delete_bucket(
    rs: RuntimeSettings, bucket_id: str, *, workspace_id: str | None = None
) -> bool:
    """Remove o bucket do catálogo e de qualquer lista de ativos. Idempotente
    — bucket já ausente não levanta erro."""
    buckets = _load_buckets(rs)
    bucket = buckets.get(bucket_id)
    if bucket is None:
        return False
    if workspace_id is not None and bucket.get("workspace_id") != workspace_id:
        return False
    buckets.pop(bucket_id, None)
    rs.set(_BUCKETS_KEY, buckets)

    active = _load_active(rs)
    changed = False
    for active_workspace_id, ids in list(active.items()):
        if bucket_id in ids:
            active[active_workspace_id] = [i for i in ids if i != bucket_id]
            changed = True
    if changed:
        rs.set(_ACTIVE_KEY, active)
    return True


def set_active(
    rs: RuntimeSettings, *, workspace_id: str, bucket_id: str, active: bool
) -> None:
    """Ativa/desativa `bucket_id` para `workspace_id`.

    Bucket inexistente é ignorado silenciosamente — nunca cria uma entrada
    de bucket ativo órfã, sem registro correspondente em `rag_buckets`.
    """
    bucket = get_bucket(rs, bucket_id)
    if bucket is None or bucket.workspace_id != workspace_id:
        return
    active_map = _load_active(rs)
    current = set(active_map.get(workspace_id, []))
    if active:
        current.add(bucket_id)
    else:
        current.discard(bucket_id)
    active_map[workspace_id] = sorted(current)
    rs.set(_ACTIVE_KEY, active_map)


def get_active_bucket_ids(rs: RuntimeSettings, workspace_id: str) -> list[str]:
    """Buckets ativos do workspace — só os que ainda existem no catálogo
    (defensivo contra `rag_buckets`/`rag_workspace_active_buckets`
    divergirem por edição manual ou bug futuro)."""
    known_ids = _load_buckets(rs).keys()
    active_map = _load_active(rs)
    return [bid for bid in active_map.get(workspace_id, []) if bid in known_ids]
